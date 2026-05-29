from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import AUTO_MEMORY_ENABLED, MEMORY_DIR
from app.services.memory import MemoryDocument, MemoryHeader, MemoryScanner, MemoryStorage, MemoryType
from app.services.memory.consolidator import MemoryConsolidator
from app.services.memory.extractor import MemoryExtractor
from app.services.memory.session_memory import SessionMemoryManager

router = APIRouter(prefix="/api/memory", tags=["memory"])

# -- singleton storage / extractor ---------------------------------------

_storage: Optional[MemoryStorage] = None
_scanner: Optional[MemoryScanner] = None
_extractor: Optional[MemoryExtractor] = None
_consolidator: Optional[MemoryConsolidator] = None
_session_mgr: Optional[SessionMemoryManager] = None


def _get_storage() -> MemoryStorage:
    global _storage
    if _storage is None:
        _storage = MemoryStorage(MEMORY_DIR)
    return _storage


def _get_scanner() -> MemoryScanner:
    global _scanner
    if _scanner is None:
        _scanner = MemoryScanner(_get_storage())
    return _scanner


def _get_extractor() -> MemoryExtractor:
    global _extractor
    if _extractor is None:
        _extractor = MemoryExtractor(_get_storage())
    return _extractor


def _get_consolidator() -> MemoryConsolidator:
    global _consolidator
    if _consolidator is None:
        _consolidator = MemoryConsolidator(_get_storage())
    return _consolidator


def _get_session_mgr() -> SessionMemoryManager:
    global _session_mgr
    if _session_mgr is None:
        _session_mgr = SessionMemoryManager(_get_storage())
    return _session_mgr


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
async def list_memories(type_filter: Optional[str] = Query(None, alias="type")):
    """List all memory files with headers, optionally filtered by type."""
    scanner = _get_scanner()
    headers = scanner.scan()
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
async def read_memory(filename: str):
    """Read a single memory file by filename."""
    storage = _get_storage()
    doc = storage.get(filename)
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
async def export_memory(filename: str):
    """Export a single memory file as raw markdown download."""
    from fastapi.responses import PlainTextResponse

    storage = _get_storage()
    doc = storage.get(filename)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"记忆文件 '{filename}' 不存在")

    # Read raw file content from disk
    file_path = Path(doc.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件 '{filename}' 在磁盘上不存在")

    from urllib.parse import quote

    raw = file_path.read_text(encoding="utf-8")
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
async def batch_export_memories(req: BatchDeleteRequest):
    """Export multiple memory files as a single markdown document download."""
    from fastapi.responses import PlainTextResponse

    storage = _get_storage()
    parts: list[str] = []
    missing: list[str] = []

    for fn in req.filenames:
        doc = storage.get(fn)
        if doc is None:
            missing.append(fn)
            continue
        file_path = Path(doc.file_path)
        if not file_path.exists():
            missing.append(fn)
            continue
        parts.append(file_path.read_text(encoding="utf-8"))

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


@router.post("/files", response_model=MemoryFileInfo)
async def create_memory(req: MemoryCreateRequest):
    """Create a new memory file."""
    storage = _get_storage()
    doc = storage.save(
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
async def update_memory(filename: str, req: MemoryUpdateRequest):
    """Update an existing memory file (partial update)."""
    storage = _get_storage()
    doc = storage.get(filename)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"记忆文件 '{filename}' 不存在")

    new_name = req.name if req.name is not None else doc.meta.name
    new_desc = req.description if req.description is not None else doc.meta.description
    new_type = req.type if req.type is not None else doc.meta.type
    new_body = req.body if req.body is not None else doc.body

    storage.save(
        name=new_name,
        description=new_desc,
        type_=new_type,
        body=new_body,
        filename=filename,
    )
    # Re-read to get fresh metadata
    updated = storage.get(filename)
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
async def delete_memory(filename: str):
    """Delete a memory file."""
    storage = _get_storage()
    ok = storage.delete(filename)
    if not ok:
        raise HTTPException(status_code=404, detail=f"记忆文件 '{filename}' 不存在或无法删除")
    return {"status": "deleted", "filename": filename}


@router.get("/index")
async def get_index():
    """Get the MEMORY.md index content."""
    storage = _get_storage()
    content = storage.get_index_content()
    return {"content": content, "path": str(storage.index_path)}


@router.post("/rebuild")
async def rebuild_index():
    """Force-rebuild the MEMORY.md index from current files."""
    storage = _get_storage()
    content = storage.rebuild_index()
    return {"status": "ok", "content": content}


@router.get("/manifest")
async def get_manifest():
    """Get a formatted text manifest of all memories."""
    scanner = _get_scanner()
    manifest = scanner.format_manifest()
    return {"manifest": manifest}


@router.get("/freshness")
async def get_freshness():
    """Get freshness info for all memory files."""
    scanner = _get_scanner()
    headers = scanner.scan()
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
async def get_extraction_status():
    """Get extraction state: cursors per session and config."""
    extractor = _get_extractor()
    sessions = extractor._state.get("sessions", {})
    return {
        "enabled": AUTO_MEMORY_ENABLED,
        "min_new_messages": extractor._min_new_messages,
        "tracked_sessions": len(sessions),
        "last_updated": extractor._state.get("last_updated", ""),
    }


@router.post("/extraction/backfill")
async def backfill_extraction(session_id: Optional[str] = Query(None)):
    """Extract memories from existing sessions.

    If session_id is provided, extract only from that session.
    Otherwise, extract from ALL sessions.
    """
    if not AUTO_MEMORY_ENABLED:
        raise HTTPException(status_code=400, detail="Auto memory extraction is disabled")

    extractor = _get_extractor()
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
async def reset_extraction(session_id: Optional[str] = Query(None)):
    """Reset extraction cursors.

    If session_id is provided, reset only that session.
    Otherwise, reset ALL sessions (forces full re-extraction on next run).
    """
    extractor = _get_extractor()
    if session_id:
        extractor.reset_session(session_id)
        return {"status": "ok", "session_id": session_id, "reset": True}
    else:
        extractor._state["sessions"] = {}
        extractor._save_state()
        return {"status": "ok", "reset_all": True}


@router.post("/extraction/run")
async def run_extraction_now(session_id: str = Query(..., description="Session ID to extract from")):
    """Run extraction immediately on a specific session."""
    if not AUTO_MEMORY_ENABLED:
        raise HTTPException(status_code=400, detail="Auto memory extraction is disabled")
    extractor = _get_extractor()
    count = await extractor.extract_from_session(session_id)
    return {"status": "ok", "session_id": session_id, "memories_saved": count}


# -- Consolidation Endpoints ---------------------------------------------


@router.post("/consolidate")
async def consolidate_memories(req: ConsolidateRequest):
    """Analyze all memories for dedup/merge/delete (AutoDream).

    dry_run=true: return proposed actions without executing.
    dry_run=false (default): execute merge/delete actions.
    """
    consolidator = _get_consolidator()
    result = await consolidator.consolidate(dry_run=req.dry_run)
    return result


@router.get("/consolidation/status")
async def get_consolidation_status():
    """Get consolidation state: last run, count, merged files history."""
    consolidator = _get_consolidator()
    return consolidator.get_status()


# -- Transfer Endpoint ---------------------------------------------------


@router.post("/transfer")
async def transfer_memories(req: TransferRequest):
    """Transfer memory files to another directory.

    Files are copied to the target directory and optionally removed from current.
    """
    import shutil

    storage = _get_storage()
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
async def batch_merge_memories(req: BatchMergeRequest):
    """Merge multiple memory files into one."""
    from app.services.memory.models import sanitize_filename

    storage = _get_storage()
    bodies: list[str] = []
    mem_type: MemoryType = MemoryType.REFERENCE

    for fn in req.filenames:
        doc = storage.get(fn)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"记忆文件 '{fn}' 不存在")
        if doc.body:
            bodies.append(doc.body)
        if mem_type == MemoryType.REFERENCE:
            mem_type = doc.meta.type

    merged_body = "\n\n---\n\n".join(bodies)
    merged_name = req.merged_name
    merged_desc = req.merged_description or f"合并自 {', '.join(req.filenames)}"

    doc = storage.save(name=merged_name, description=merged_desc, type_=mem_type, body=merged_body)

    # Delete originals
    for fn in req.filenames:
        storage.delete(fn)

    fname = sanitize_filename(merged_name)
    return {"status": "ok", "merged_file": fname, "name": merged_name, "source_files": req.filenames}


@router.post("/batch/delete")
async def batch_delete_memories(req: BatchDeleteRequest):
    """Delete multiple memory files at once."""
    storage = _get_storage()
    deleted: list[str] = []
    not_found: list[str] = []

    for fn in req.filenames:
        ok = storage.delete(fn)
        if ok:
            deleted.append(fn)
        else:
            not_found.append(fn)

    return {"status": "ok", "deleted": deleted, "count": len(deleted), "not_found": not_found}


# -- Search Endpoint -----------------------------------------------------


@router.get("/search")
async def search_memories(q: str = Query(..., min_length=1, description="搜索关键词")):
    """Search memory file names, descriptions, and bodies for a keyword."""
    scanner = _get_scanner()
    headers = scanner.scan()
    storage = _get_storage()
    keyword = q.lower()
    results: list[dict] = []

    for h in headers:
        score = 0
        if keyword in h.name.lower():
            score += 3
        if keyword in h.description.lower():
            score += 2
        body = ""
        doc = storage.get(h.filename)
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
async def list_session_summaries():
    """List all session memory summaries."""
    session_mgr = _get_session_mgr()
    summaries = session_mgr.list_session_summaries()
    return {"sessions": summaries, "count": len(summaries)}


@router.get("/sessions/{session_id}")
async def get_session_summary(session_id: str):
    """Get the cached summary for a specific session."""
    session_mgr = _get_session_mgr()
    summary = session_mgr.get_session_summary(session_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 没有已缓存的摘要")
    return {"session_id": session_id, "summary": summary}


@router.get("/global-summary")
async def get_global_summary():
    """Get the global aggregated summary across all sessions."""
    session_mgr = _get_session_mgr()
    summary = session_mgr.get_global_summary()
    if not summary:
        return {"global_summary": "", "message": "暂无全局摘要"}
    return {"global_summary": summary}


@router.post("/global-summary/refresh")
async def refresh_global_summary():
    """Force-refresh the global aggregated summary."""
    session_mgr = _get_session_mgr()
    summary = await session_mgr.update_global_summary()
    if not summary:
        return {"status": "ok", "message": "没有可聚合的会话摘要"}
    return {"status": "ok", "global_summary": summary}


@router.post("/sessions/reset/{session_id}")
async def reset_session_memory(session_id: str):
    """Reset cursor and delete summary for a session."""
    session_mgr = _get_session_mgr()
    session_mgr.reset_session(session_id)
    return {"status": "ok", "session_id": session_id, "reset": True}
