from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import AUTO_MEMORY_ENABLED, MEMORY_DIR
from app.services.memory import MemoryDocument, MemoryHeader, MemoryScanner, MemoryStorage, MemoryType
from app.services.memory.extractor import MemoryExtractor

router = APIRouter(prefix="/api/memory", tags=["memory"])

# ── singleton storage / extractor ───────────────────────────────────

_storage: Optional[MemoryStorage] = None
_scanner: Optional[MemoryScanner] = None
_extractor: Optional[MemoryExtractor] = None


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


# ── Pydantic request/response models ────────────────────────────────


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


# ── endpoints ───────────────────────────────────────────────────────


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


# ── Memory Extraction Endpoints ──────────────────────────────────────


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

