from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.config import AUTO_MEMORY_ENABLED, MEMORY_DIR
from app.services.memory import MemoryDocument, MemoryHeader, MemoryScanner, MemoryStorage, MemoryType
from app.services.memory.consolidator import MemoryConsolidator
from app.services.memory.extractor import MemoryExtractor
from app.services.memory.session_memory import SessionMemoryManager
from app.services.auth.service import get_current_user
from app.services.auth.session_guard import check_session_access
from app.db.session import afetch_all
from app.utils.async_file import aexists, aread_text

router = APIRouter(prefix="/api/memory", tags=["memory"])

# -- Per-user storage singletons -----------------------------------------
# Each user gets their own memory directory: .claude/memory/users/{user_id}/

_storages: dict[str, MemoryStorage] = {}
_scanners: dict[str, MemoryScanner] = {}
_extractors: dict[str, MemoryExtractor] = {}
_consolidators: dict[str, MemoryConsolidator] = {}
_session_mgrs: dict[str, SessionMemoryManager] = {}
# Shared (non-user-scoped) singletons for backward compat
_storage_shared: Optional[MemoryStorage] = None
_scanner_shared: Optional[MemoryScanner] = None
_session_mgr_shared: Optional[SessionMemoryManager] = None


def _get_user_memory_dir(user_id: str) -> Path:
    """Return the per-user memory directory path."""
    return MEMORY_DIR / "users" / user_id


def _get_storage(user_id: str = "") -> MemoryStorage:
    """Get memory storage scoped to a user (or shared if user_id is empty)."""
    if user_id:
        if user_id not in _storages:
            _storages[user_id] = MemoryStorage(_get_user_memory_dir(user_id))
        return _storages[user_id]
    global _storage_shared
    if _storage_shared is None:
        _storage_shared = MemoryStorage(MEMORY_DIR)
    return _storage_shared


def _get_scanner(user_id: str = "") -> MemoryScanner:
    if user_id:
        if user_id not in _scanners:
            _scanners[user_id] = MemoryScanner(_get_storage(user_id))
        return _scanners[user_id]
    global _scanner_shared
    if _scanner_shared is None:
        _scanner_shared = MemoryScanner(_get_storage(""))
    return _scanner_shared


def _get_extractor(user_id: str = "") -> MemoryExtractor:
    if user_id:
        if user_id not in _extractors:
            _extractors[user_id] = MemoryExtractor(_get_storage(user_id))
        return _extractors[user_id]
    # fallback to shared
    from app.services.memory.extractor import MemoryExtractor as ME
    return ME(_get_storage(""))


def _get_consolidator(user_id: str = "") -> MemoryConsolidator:
    if user_id:
        if user_id not in _consolidators:
            _consolidators[user_id] = MemoryConsolidator(_get_storage(user_id))
        return _consolidators[user_id]
    from app.services.memory.consolidator import MemoryConsolidator as MC
    return MC(_get_storage(""))


def _get_session_mgr(user_id: str = "") -> SessionMemoryManager:
    if user_id:
        if user_id not in _session_mgrs:
            _session_mgrs[user_id] = SessionMemoryManager(_get_storage(user_id))
        return _session_mgrs[user_id]
    global _session_mgr_shared
    if _session_mgr_shared is None:
        _session_mgr_shared = SessionMemoryManager(_get_storage(""))
    return _session_mgr_shared


# -- Pydantic request/response models -----------------------------------


class MemoryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="记忆名称")
    description: str = Field("", max_length=512, description="一行描述")
    type: MemoryType = Field(MemoryType.REFERENCE, description="记忆类型")
    body: str = Field("", description="记忆内容正文（Markdown）")
    filename: Optional[str] = Field(None, description="可选：指定文件名，默认从 name 自动生成")


class MemoryUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = Field(None, max_length=512)
    type: Optional[MemoryType] = None
    body: Optional[str] = None


class MemoryFileInfo(BaseModel):
    filename: str
    name: str
    description: str
    type: str
    mtime: float
    created_at: str = ""
    updated_at: str = ""


class MemoryDetail(BaseModel):
    filename: str
    meta: dict
    body: str


class ConsolidateRequest(BaseModel):
    dry_run: bool = Field(False, description="dry_run=true 仅分析不执行")


class TransferRequest(BaseModel):
    filenames: list[str] = Field(..., min_length=1, max_length=100, description="要转移的文件名列表")
    target_dir: str = Field(..., min_length=1, max_length=512, description="目标目录路径")


class BatchMergeRequest(BaseModel):
    filenames: list[str] = Field(..., min_length=2, max_length=50, description="要合并的文件名列表")
    merged_name: str = Field(..., min_length=1, max_length=128, description="合并后的文件名")
    merged_description: str = Field("", max_length=512, description="合并后的描述")


class BatchDeleteRequest(BaseModel):
    filenames: list[str] = Field(..., min_length=1, max_length=100, description="要删除的文件名列表")


class SessionTransferRequest(BaseModel):
    source_session_id: str = Field(..., min_length=1, max_length=128, description="源会话 ID")
    target_session_id: str = Field(..., min_length=1, max_length=128, description="目标会话 ID")
    mode: str = Field("append", description="append 或 overwrite")


# -- endpoints -----------------------------------------------------------


@router.get("/files", response_model=list[MemoryFileInfo])
async def list_memories(
    type_filter: Optional[str] = Query(None, alias="type"),
    user: dict = Depends(get_current_user),
):
    """List all memory files with headers, optionally filtered by type."""
    uid = user["id"]
    scanner = _get_scanner(uid)
    headers = await scanner.scan()
    if type_filter:
        try:
            mt = MemoryType(type_filter)
            headers = [h for h in headers if h.type == mt]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的记忆类型: {type_filter}")
    return [
        MemoryFileInfo(
            filename=h.filename,
            name=h.name,
            description=h.description,
            type=h.type.value,
            mtime=h.mtime,
            created_at=h.created_at,
            updated_at=h.updated_at,
        )
        for h in headers
    ]


@router.get("/files/{filename}", response_model=MemoryDetail)
async def read_memory(filename: str, user: dict = Depends(get_current_user)):
    """Read a single memory file by filename."""
    uid = user["id"]
    storage = _get_storage(uid)
    doc = await storage.get(filename)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"记忆文件 '{filename}' 不存在")
    return MemoryDetail(
        filename=filename,
        meta={
            "name": doc.meta.name,
            "description": doc.meta.description,
            "type": doc.meta.type.value,
            "created_at": doc.meta.created_at,
            "updated_at": doc.meta.updated_at,
        },
        body=doc.body,
    )


@router.get("/files/{filename}/export")
async def export_memory(filename: str, user: dict = Depends(get_current_user)):
    """Export a single memory file as raw markdown download."""
    from fastapi.responses import PlainTextResponse

    uid = user["id"]
    storage = _get_storage(uid)
    doc = await storage.get(filename)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"记忆文件 '{filename}' 不存在")

    # Read raw file content from disk
    file_path = Path(doc.file_path)
    if not await aexists(file_path):
        raise HTTPException(status_code=404, detail=f"文件 '{filename}' 在磁盘上不存在")

    from urllib.parse import quote

    raw = await aread_text(file_path)
    safe_filename = quote(filename, safe="")
    return PlainTextResponse(
        content=raw,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{safe_filename}"
            ),
        },
    )


@router.post("/batch/export")
async def batch_export_memories(req: BatchDeleteRequest, user: dict = Depends(get_current_user)):
    """Export multiple memory files as a single markdown document download."""
    from fastapi.responses import PlainTextResponse

    uid = user["id"]
    storage = _get_storage(uid)
    parts: list[str] = []
    missing: list[str] = []

    for fn in req.filenames:
        doc = await storage.get(fn)
        if doc is None:
            missing.append(fn)
            continue
        file_path = Path(doc.file_path)
        if not await aexists(file_path):
            missing.append(fn)
            continue
        parts.append(await aread_text(file_path))

    if not parts:
        raise HTTPException(status_code=404, detail="没有可导出的文件")

    combined = "\n\n---\n\n".join(parts)
    if missing:
        combined = f"<!-- 以下文件未找到: {', '.join(missing)} -->\n\n{combined}"

    return PlainTextResponse(
        content=combined,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''memories_export.md"},
    )


@router.post("/import")
async def import_memories(
    file: UploadFile = File(...),
    target_filename: str = Query("", alias="target", description="可选：拼接到的目标文件名（当前编辑器选中的文件）"),
    user: dict = Depends(get_current_user),
):
    """Import a markdown file — supports two modes:

    **Append mode** (target_filename is provided):
        Appends the uploaded content to the specified target memory file,
        merging it into the existing body. The target's frontmatter is
        preserved; only updated_at is refreshed.

    **Create mode** (target_filename omitted):
        Parses the upload into individual memory files (supports both
        single files and batch exports joined by \\n\\n---\\n\\n).

    Returns:
        status: "ok"
        mode: "append" | "create"
        ...
    """
    if not file.filename or not file.filename.lower().endswith(('.md', '.markdown', '.txt')):
        raise HTTPException(
            status_code=400,
            detail="仅支持 .md / .markdown / .txt 格式的记忆导出文件",
        )

    content = (await file.read()).decode("utf-8")
    if not content.strip():
        raise HTTPException(status_code=400, detail="上传的文件内容为空")

    uid = user["id"]
    storage = _get_storage(uid)

    # ── Append mode: merge into existing target file ──────────────────
    if target_filename:
        existing = await storage.get(target_filename)
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail=f"目标记忆文件 '{target_filename}' 不存在，请先选择一个文件",
            )

        # Strip frontmatter from uploaded content to get raw body
        imported_doc = MemoryDocument.parse(content)
        imported_body = imported_doc.body.strip() if imported_doc.body else content.strip()

        # Merge: existing body + separator + imported body
        merged_body = existing.body.rstrip() + "\n\n---\n\n" + imported_body

        await storage.save(
            name=existing.meta.name,
            description=existing.meta.description,
            type_=existing.meta.type,
            body=merged_body,
            filename=target_filename,
        )

        return {
            "status": "ok",
            "mode": "append",
            "target_filename": target_filename,
            "imported_chars": len(imported_body),
            "total_chars": len(merged_body),
        }

    # ── Create mode: parse into individual files ──────────────────────
    BATCH_SEP = "\n\n---\n\n"
    sections_by_sep = content.split(BATCH_SEP)
    fm_count = sum(1 for s in sections_by_sep if s.strip().startswith("---"))
    raw_sections = [s.strip() for s in sections_by_sep if s.strip()] if fm_count > 1 else [content.strip()]

    imported: list[str] = []
    skipped: list[dict] = []

    for section in raw_sections:
        if not section.strip():
            continue

        try:
            doc = MemoryDocument.parse(section)
        except Exception:
            skipped.append({"content_preview": section[:80], "error": "无法解析YAML frontmatter"})
            continue

        if not doc.meta.name or doc.meta.name == "untitled":
            skipped.append({"content_preview": section[:80], "error": "缺少 name 字段"})
            continue

        try:
            from app.services.memory.models import sanitize_filename

            filename = sanitize_filename(doc.meta.name)
            await storage.save(
                name=doc.meta.name,
                description=doc.meta.description,
                type_=doc.meta.type,
                body=doc.body,
                filename=filename,
            )
            imported.append(filename)
        except Exception as exc:
            skipped.append({"name": doc.meta.name, "error": str(exc)})

    return {
        "status": "ok",
        "mode": "create",
        "imported": imported,
        "imported_count": len(imported),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "total_sections": len(raw_sections),
    }


@router.post("/files", response_model=MemoryFileInfo)
async def create_memory(req: MemoryCreateRequest, user: dict = Depends(get_current_user)):
    """Create a new memory file."""
    uid = user["id"]
    storage = _get_storage(uid)
    doc = await storage.save(
        name=req.name,
        description=req.description,
        type_=req.type,
        body=req.body,
        filename=req.filename,
    )
    fname = Path(doc.file_path).name
    return MemoryFileInfo(
        filename=fname,
        name=doc.meta.name,
        description=doc.meta.description,
        type=doc.meta.type.value,
        mtime=0,
        created_at=doc.meta.created_at,
        updated_at=doc.meta.updated_at,
    )


@router.put("/files/{filename}", response_model=MemoryFileInfo)
async def update_memory(filename: str, req: MemoryUpdateRequest, user: dict = Depends(get_current_user)):
    """Update an existing memory file (partial update)."""
    uid = user["id"]
    storage = _get_storage(uid)
    doc = await storage.get(filename)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"记忆文件 '{filename}' 不存在")

    new_name = req.name if req.name is not None else doc.meta.name
    new_desc = req.description if req.description is not None else doc.meta.description
    new_type = req.type if req.type is not None else doc.meta.type
    new_body = req.body if req.body is not None else doc.body

    await storage.save(
        name=new_name,
        description=new_desc,
        type_=new_type,
        body=new_body,
        filename=filename,
    )
    # Re-read to get fresh metadata
    updated = await storage.get(filename)
    if updated is None:
        raise HTTPException(status_code=500, detail="保存后读取失败")
    return MemoryFileInfo(
        filename=filename,
        name=updated.meta.name,
        description=updated.meta.description,
        type=updated.meta.type.value,
        mtime=0,
        created_at=updated.meta.created_at,
        updated_at=updated.meta.updated_at,
    )


@router.delete("/files/{filename}")
async def delete_memory(filename: str, user: dict = Depends(get_current_user)):
    """Delete a memory file."""
    uid = user["id"]
    storage = _get_storage(uid)
    ok = await storage.delete(filename)
    if not ok:
        raise HTTPException(status_code=404, detail=f"记忆文件 '{filename}' 不存在或无法删除")
    return {"status": "deleted", "filename": filename}


@router.get("/index")
async def get_index(user: dict = Depends(get_current_user)):
    """Get the MEMORY.md index content."""
    uid = user["id"]
    storage = _get_storage(uid)
    content = await storage.get_index_content()
    return {"content": content, "path": str(storage.index_path)}


@router.post("/rebuild")
async def rebuild_index(user: dict = Depends(get_current_user)):
    """Force-rebuild the MEMORY.md index from current files."""
    uid = user["id"]
    storage = _get_storage(uid)
    content = await storage.rebuild_index()
    return {"status": "ok", "content": content}


@router.get("/manifest")
async def get_manifest(user: dict = Depends(get_current_user)):
    """Get a formatted text manifest of all memories."""
    uid = user["id"]
    scanner = _get_scanner(uid)
    manifest = await scanner.format_manifest()
    return {"manifest": manifest}


@router.get("/freshness")
async def get_freshness(user: dict = Depends(get_current_user)):
    """Get freshness info for all memory files."""
    uid = user["id"]
    scanner = _get_scanner(uid)
    headers = await scanner.scan()
    result = []
    for h in headers:
        warning = scanner.freshness_text(h.mtime)
        result.append(
            {
                "filename": h.filename,
                "name": h.name,
                "mtime": h.mtime,
                "age_days": scanner._age_days(h.mtime),
                "warning": warning,
            }
        )
    return {"freshness": result}


# -- Memory Extraction Endpoints -----------------------------------------


@router.get("/extraction/status")
async def get_extraction_status(user: dict = Depends(get_current_user)):
    """Get extraction state: cursors per session and config."""
    uid = user["id"]
    extractor = _get_extractor(uid)
    sessions = extractor._state.get("sessions", {})
    return {
        "enabled": AUTO_MEMORY_ENABLED,
        "min_new_messages": extractor._min_new_messages,
        "tracked_sessions": len(sessions),
        "last_updated": extractor._state.get("last_updated", ""),
    }


@router.post("/extraction/backfill")
async def backfill_extraction(session_id: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    """Extract memories from existing sessions.

    If session_id is provided, extract only from that session.
    Otherwise, extract from ALL sessions.
    """
    if not AUTO_MEMORY_ENABLED:
        raise HTTPException(status_code=400, detail="Auto memory extraction is disabled")

    uid = user["id"]
    extractor = _get_extractor(uid)
    if session_id:
        count = await extractor.extract_from_session(session_id)
        return {"status": "ok", "session_id": session_id, "memories_saved": count}
    else:
        results = await extractor.backfill_all_sessions()
        total = sum(results.values())
        return {
            "status": "ok",
            "sessions_processed": len(results),
            "total_memories_saved": total,
            "details": results,
        }


@router.post("/extraction/reset")
async def reset_extraction(session_id: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    """Reset extraction cursors.

    If session_id is provided, reset only that session.
    Otherwise, reset ALL sessions (forces full re-extraction on next run).
    """
    uid = user["id"]
    extractor = _get_extractor(uid)
    if session_id:
        extractor.reset_session(session_id)
        return {"status": "ok", "session_id": session_id, "reset": True}
    else:
        extractor._state["sessions"] = {}
        extractor._save_state()
        return {"status": "ok", "reset_all": True}


@router.post("/extraction/run")
async def run_extraction_now(session_id: str = Query(..., description="Session ID to extract from"), user: dict = Depends(get_current_user)):
    """Run extraction immediately on a specific session."""
    if not AUTO_MEMORY_ENABLED:
        raise HTTPException(status_code=400, detail="Auto memory extraction is disabled")
    uid = user["id"]
    extractor = _get_extractor(uid)
    count = await extractor.extract_from_session(session_id)
    return {"status": "ok", "session_id": session_id, "memories_saved": count}


# -- Consolidation Endpoints ---------------------------------------------


@router.post("/consolidate")
async def consolidate_memories(req: ConsolidateRequest, user: dict = Depends(get_current_user)):
    """Analyze all memories for dedup/merge/delete (AutoDream).

    dry_run=true: return proposed actions without executing.
    dry_run=false (default): execute merge/delete actions.
    """
    uid = user["id"]
    consolidator = _get_consolidator(uid)
    result = await consolidator.consolidate(dry_run=req.dry_run)
    return result


@router.get("/consolidation/status")
async def get_consolidation_status(user: dict = Depends(get_current_user)):
    """Get consolidation state: last run, count, merged files history."""
    uid = user["id"]
    consolidator = _get_consolidator(uid)
    return consolidator.get_status()


# -- Transfer Endpoint ---------------------------------------------------


@router.post("/transfer")
async def transfer_memories(req: TransferRequest, user: dict = Depends(get_current_user)):
    """Transfer memory files to another directory.

    Files are copied to the target directory and optionally removed from current.
    """
    import shutil

    uid = user["id"]
    storage = _get_storage(uid)
    target = Path(req.target_dir)
    if not target.exists():
        raise HTTPException(status_code=400, detail=f"目标目录不存在: {req.target_dir}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"目标路径不是目录: {req.target_dir}")

    transferred: list[str] = []
    failed: list[dict] = []

    for fn in req.filenames:
        src = storage.base / fn
        dst = target / fn
        if not src.exists():
            failed.append({"filename": fn, "error": "源文件不存在"})
            continue
        try:
            shutil.copy2(src, dst)
            transferred.append(fn)
        except OSError as exc:
            failed.append({"filename": fn, "error": str(exc)})

    return {
        "status": "ok",
        "transferred": transferred,
        "count": len(transferred),
        "target_dir": req.target_dir,
        "failed": failed,
    }


# -- Batch Operations ----------------------------------------------------


@router.post("/batch/merge")
async def batch_merge_memories(req: BatchMergeRequest, user: dict = Depends(get_current_user)):
    """Merge multiple memory files into one."""
    from app.services.memory.models import sanitize_filename

    uid = user["id"]
    storage = _get_storage(uid)
    bodies: list[str] = []
    mem_type: MemoryType = MemoryType.REFERENCE

    for fn in req.filenames:
        doc = await storage.get(fn)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"记忆文件 '{fn}' 不存在")
        if doc.body:
            bodies.append(doc.body)
        if mem_type == MemoryType.REFERENCE:
            mem_type = doc.meta.type

    merged_body = "\n\n---\n\n".join(bodies)
    merged_name = req.merged_name
    merged_desc = req.merged_description or f"合并自 {', '.join(req.filenames)}"

    doc = await storage.save(name=merged_name, description=merged_desc, type_=mem_type, body=merged_body)

    # Delete originals
    for fn in req.filenames:
        await storage.delete(fn)

    fname = sanitize_filename(merged_name)
    return {"status": "ok", "merged_file": fname, "name": merged_name, "source_files": req.filenames}


@router.post("/batch/delete")
async def batch_delete_memories(req: BatchDeleteRequest, user: dict = Depends(get_current_user)):
    """Delete multiple memory files at once."""
    uid = user["id"]
    storage = _get_storage(uid)
    deleted: list[str] = []
    not_found: list[str] = []

    for fn in req.filenames:
        ok = await storage.delete(fn)
        if ok:
            deleted.append(fn)
        else:
            not_found.append(fn)

    return {"status": "ok", "deleted": deleted, "count": len(deleted), "not_found": not_found}


# -- Trash / Recovery Endpoints -------------------------------------------

TRASH_RETENTION_DAYS = 30


@router.get("/trash")
async def list_trash(user: dict = Depends(get_current_user)):
    """List all files in the trash with deletion time and days remaining."""
    uid = user["id"]
    storage = _get_storage(uid)
    items = await storage.list_trash()
    return {"trash": items, "count": len(items), "retention_days": TRASH_RETENTION_DAYS}


@router.post("/trash/{trash_name}/recover")
async def recover_from_trash(trash_name: str, user: dict = Depends(get_current_user)):
    """Recover a file from trash back to the main memory directory."""
    uid = user["id"]
    storage = _get_storage(uid)
    ok = await storage.recover_from_trash(trash_name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"回收站中找不到文件 '{trash_name}'")
    return {"status": "recovered", "trash_name": trash_name}


@router.delete("/trash/{trash_name}")
async def purge_trash_item(trash_name: str, user: dict = Depends(get_current_user)):
    """Permanently delete a file from trash (no recovery)."""
    uid = user["id"]
    storage = _get_storage(uid)
    ok = await storage.purge_trash_item(trash_name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"回收站中找不到文件 '{trash_name}'")
    return {"status": "purged", "trash_name": trash_name}


@router.post("/trash/cleanup")
async def cleanup_trash(user: dict = Depends(get_current_user)):
    """Run trash cleanup — permanently deletes files older than retention period."""
    uid = user["id"]
    storage = _get_storage(uid)
    purged = await storage.cleanup_trash(TRASH_RETENTION_DAYS)
    return {"status": "ok", "purged": purged, "retention_days": TRASH_RETENTION_DAYS}


# -- Search Endpoint -----------------------------------------------------


@router.get("/search")
async def search_memories(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    user: dict = Depends(get_current_user),
):
    """Search memory file names, descriptions, and bodies for a keyword."""
    uid = user["id"]
    scanner = _get_scanner(uid)
    headers = await scanner.scan()
    storage = _get_storage(uid)
    keyword = q.lower()
    results: list[dict] = []

    for h in headers:
        score = 0
        if keyword in h.name.lower():
            score += 3
        if keyword in h.description.lower():
            score += 2
        body = ""
        doc = await storage.get(h.filename)
        if doc:
            body = doc.body
            if keyword in body.lower():
                score += 1
        if score > 0:
            body_lower = body.lower()
            idx = body_lower.find(keyword)
            snippet = ""
            if idx >= 0:
                start = max(0, idx - 40)
                end = min(len(body), idx + len(keyword) + 60)
                snippet = ("..." if start > 0 else "") + body[start:end] + ("..." if end < len(body) else "")
            results.append({
                "filename": h.filename,
                "name": h.name,
                "description": h.description,
                "type": h.type.value,
                "score": score,
                "snippet": snippet,
                "mtime": h.mtime,
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return {"query": q, "count": len(results), "results": results}


# -- Session Memory Endpoints --------------------------------------------


@router.get("/sessions")
async def list_session_summaries(user: dict = Depends(get_current_user)):
    """List session memory summaries — only for sessions the user can access."""
    uid = user["id"]
    session_mgr = _get_session_mgr(uid)

    # Collect session IDs the user is a member of (or public sessions)
    member_rows = await afetch_all(
        "SELECT sm.session_id FROM session_members sm WHERE sm.user_id=$1 "
        "UNION "
        "SELECT s.id FROM sessions s WHERE s.visibility='public'",
        uid,
    )
    allowed_ids = {row["session_id"] for row in member_rows}

    summaries = await session_mgr.list_session_summaries()
    # Filter: only return summaries for sessions the user can access
    filtered = [s for s in summaries if s.get("session_id") in allowed_ids]
    return {"sessions": filtered, "count": len(filtered)}


@router.get("/sessions/{session_id}")
async def get_session_summary(session_id: str, user: dict = Depends(get_current_user)):
    """Get the cached summary for a specific session. Only accessible by session members."""
    # Verify the user has access to this session
    await check_session_access(session_id, user)
    uid = user["id"]
    session_mgr = _get_session_mgr(uid)
    summary = await session_mgr.get_session_summary(session_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 没有已缓存的摘要")
    return {"session_id": session_id, "summary": summary}


@router.get("/global-summary")
async def get_global_summary(user: dict = Depends(get_current_user)):
    """Get the global aggregated summary across all sessions."""
    uid = user["id"]
    session_mgr = _get_session_mgr(uid)
    summary = await session_mgr.get_global_summary()
    if not summary:
        return {"global_summary": "", "message": "暂无全局摘要"}
    return {"global_summary": summary}


@router.post("/global-summary/refresh")
async def refresh_global_summary(user: dict = Depends(get_current_user)):
    """Force-refresh the global aggregated summary."""
    uid = user["id"]
    session_mgr = _get_session_mgr(uid)
    summary = await session_mgr.update_global_summary()
    if not summary:
        return {"status": "ok", "message": "没有可聚合的会话摘要"}
    return {"status": "ok", "global_summary": summary}


@router.post("/sessions/reset/{session_id}")
async def reset_session_memory(session_id: str, user: dict = Depends(get_current_user)):
    """Reset cursor and delete summary for a session. Only the session owner can do this."""
    from app.services.auth.session_guard import SessionRole, require_session_role
    # Only the session owner can reset memory
    access = await check_session_access(session_id, user)
    if not access.can_manage:
        raise HTTPException(status_code=403, detail="Only the session owner can reset session memory")
    uid = user["id"]
    session_mgr = _get_session_mgr(uid)
    await session_mgr.reset_session(session_id)
    return {"status": "ok", "session_id": session_id, "reset": True}


@router.post("/sessions/transfer")
async def transfer_session_memory(req: SessionTransferRequest, user: dict = Depends(get_current_user)):
    """Transfer (copy/merge) memory summary from one session to another.

    - mode="append":  Append source summary to target session's summary.
    - mode="overwrite": Replace target session's summary with source's.

    User must have read access to the source session and write access to the target.
    """
    uid = user["id"]

    # Verify access to both sessions
    source_access = await check_session_access(req.source_session_id, user)
    target_access = await check_session_access(req.target_session_id, user)
    if not target_access.can_write:
        raise HTTPException(status_code=403, detail="No write permission on target session")

    session_mgr = _get_session_mgr(uid)

    source_summary = await session_mgr.get_session_summary(req.source_session_id)
    if not source_summary:
        raise HTTPException(
            status_code=404,
            detail=f"源会话 '{req.source_session_id}' 没有已缓存的摘要",
        )

    if req.mode == "overwrite":
        await session_mgr.write_session_summary(req.target_session_id, source_summary)
        return {
            "status": "ok",
            "mode": "overwrite",
            "source_session_id": req.source_session_id,
            "target_session_id": req.target_session_id,
        }

    # append mode (default)
    target_summary = await session_mgr.get_session_summary(req.target_session_id)
    if target_summary:
        merged = target_summary.rstrip() + "\n\n---\n\n" + source_summary
    else:
        merged = source_summary
    await session_mgr.write_session_summary(req.target_session_id, merged)
    return {
        "status": "ok",
        "mode": "append",
        "source_session_id": req.source_session_id,
        "target_session_id": req.target_session_id,
    }


# -- Session Memory Store Endpoints (per-session append-only memory) -----

_session_stores: dict[str, object] = {}  # user_id → SessionMemoryStore


def _get_session_store_api(user_id: str = ""):
    """Return a per-user SessionMemoryStore for API endpoints."""
    global _session_stores
    uid = user_id or "local-admin"
    if uid not in _session_stores:
        from app.services.memory.session_store import SessionMemoryStore
        user_dir = MEMORY_DIR / "users" / uid
        _session_stores[uid] = SessionMemoryStore(user_dir)
    return _session_stores[uid]


class SessionTopicRequest(BaseModel):
    topic: str = Field("", max_length=256, description="新话题标签")


class CreateMemorySessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128, description="会话 ID")
    session_name: str = Field("", max_length=256, description="会话名称")
    topic: str = Field("", max_length=256, description="话题标签")


@router.get("/session-store")
async def list_memory_sessions(user: dict = Depends(get_current_user)):
    """List all memory sessions (from the append-only per-session store)."""
    uid = user["id"]
    store = _get_session_store_api(uid)
    sessions = await store.list_sessions()
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "session_name": s.session_name,
                "topic": s.topic,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "conversation_size_chars": s.conversation_size_chars,
                "turn_count": s.turn_count,
                "is_active": s.is_active,
            }
            for s in sessions
        ],
        "count": len(sessions),
    }


@router.get("/session-store/{session_id}")
async def get_memory_session_info(session_id: str, user: dict = Depends(get_current_user)):
    """Get metadata for a specific memory session."""
    uid = user["id"]
    store = _get_session_store_api(uid)
    info = await store.get_session_info(session_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 的记忆不存在")
    return {
        "session_id": info.session_id,
        "session_name": info.session_name,
        "topic": info.topic,
        "created_at": info.created_at,
        "updated_at": info.updated_at,
        "conversation_size_chars": info.conversation_size_chars,
        "turn_count": info.turn_count,
        "is_active": info.is_active,
    }


@router.get("/session-store/{session_id}/conversation")
async def get_memory_session_conversation(
    session_id: str,
    max_chars: int = Query(0, description="最大返回字符数（0=全部）"),
    recent_turns: int = Query(0, description="仅返回最近 N 轮（0=全部）"),
    user: dict = Depends(get_current_user),
):
    """Get the raw conversation memory for a session (append-only store)."""
    uid = user["id"]
    store = _get_session_store_api(uid)
    content = await store.get_conversation(
        session_id, max_chars=max_chars, recent_turns=recent_turns,
    )
    if not content:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 的对话记忆不存在或为空")
    info = await store.get_session_info(session_id)
    return {
        "session_id": session_id,
        "content": content,
        "turn_count": info.turn_count if info else 0,
        "size_chars": len(content),
    }


@router.post("/session-store/{session_id}/consolidate")
async def consolidate_memory_session(session_id: str, user: dict = Depends(get_current_user)):
    """Trigger LLM-based consolidation for a session's conversation memory."""
    uid = user["id"]
    store = _get_session_store_api(uid)
    result = await store.trigger_llm_consolidation(session_id)
    if result is None:
        return {"status": "skipped", "message": "会话记忆不足无需整合，或 LLM 不可用"}
    return {"status": "ok", "session_id": session_id, "size_chars": len(result)}


@router.put("/session-store/{session_id}/topic")
async def update_memory_session_topic(
    session_id: str, req: SessionTopicRequest, user: dict = Depends(get_current_user),
):
    """Update the topic label for a memory session."""
    uid = user["id"]
    store = _get_session_store_api(uid)
    await store.update_topic(session_id, req.topic)
    return {"status": "ok", "session_id": session_id, "topic": req.topic}


@router.post("/session-store")
async def create_memory_session(
    req: CreateMemorySessionRequest, user: dict = Depends(get_current_user),
):
    """Manually create a new memory session."""
    uid = user["id"]
    store = _get_session_store_api(uid)
    info = await store.create_new_session(
        req.session_id, req.session_name, req.topic,
    )
    return {
        "status": "ok",
        "session_id": info.session_id,
        "session_name": info.session_name,
        "topic": info.topic,
        "created_at": info.created_at,
    }


@router.get("/session-store/{session_id}/search")
async def search_memory_session(
    session_id: str,
    q: str = Query(..., min_length=1, description="搜索关键词"),
    user: dict = Depends(get_current_user),
):
    """Search within a specific session's conversation memory."""
    uid = user["id"]
    store = _get_session_store_api(uid)
    content = await store.get_conversation(session_id)
    if not content:
        return {"session_id": session_id, "query": q, "matches": [], "count": 0}

    keyword = q.lower()
    lines = content.split("\n")
    matches: list[dict] = []
    for i, line in enumerate(lines):
        if keyword in line.lower():
            start = max(0, i - 1)
            end = min(len(lines), i + 3)
            snippet = "\n".join(lines[start:end])
            matches.append({
                "line_number": i + 1,
                "snippet": snippet[:300],
                "turn_match": _extract_turn_from_line(lines, i),
            })

    matches = matches[:20]
    return {"session_id": session_id, "query": q, "matches": matches, "count": len(matches)}


def _extract_turn_from_line(lines: list[str], line_idx: int) -> str:
    """Look backward from line_idx to find the turn header."""
    import re
    for i in range(line_idx, max(0, line_idx - 50), -1):
        m = re.match(r"## Turn (\d+) — (.+)", lines[i])
        if m:
            return f"Turn {m.group(1)} ({m.group(2)})"
    return "未知轮次"
