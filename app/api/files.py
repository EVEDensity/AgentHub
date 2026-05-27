from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import DATA_DIR
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/files", tags=["files"])

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB total limit
CHUNK_SIZE_LIMIT = 5 * 1024 * 1024  # 5 MB per chunk

ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "code": {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp", ".swift", ".kt", ".rb", ".php", ".sql", ".sh", ".bash", ".vue", ".svelte", ".astro"},
    "document": {".txt", ".md", ".pdf", ".docx", ".rtf", ".tex", ".rst", ".org", ".log"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "spreadsheet": {".xlsx", ".xls", ".csv", ".tsv"},
    "config": {".json", ".yaml", ".yml", ".xml", ".toml", ".ini", ".cfg", ".env", ".conf", ".cnf", ".editorconfig", ".gitignore", ".dockerfile", ".makefile", ".gemfile", ".prisma", ".graphql", ".proto"},
}

ALL_ALLOWED: set[str] = set()
for exts in ALLOWED_EXTENSIONS.values():
    ALL_ALLOWED.update(exts)


def _category_for(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    for cat, exts in ALLOWED_EXTENSIONS.items():
        if ext in exts:
            return cat
    return "unknown"


def _validate_filename(filename: str) -> None:
    name = Path(filename).name
    if not name or ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if Path(name).suffix.lower() not in ALL_ALLOWED:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {Path(name).suffix}")


class ChunkCompleteRequest(BaseModel):
    upload_id: str
    file_name: str
    total_chunks: int


class UploadInitResponse(BaseModel):
    upload_id: str
    chunk_size_hint: int = 512 * 1024


@router.get("/allowed-types")
async def allowed_types() -> dict:
    return {"categories": {cat: sorted(exts) for cat, exts in ALLOWED_EXTENSIONS.items()}}


@router.post("/upload/init")
async def init_upload(user: dict = Depends(get_current_user)) -> dict:
    upload_id = f"up-{uuid.uuid4().hex[:12]}"
    chunk_dir = UPLOAD_DIR / upload_id
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / "meta.json").write_text(json.dumps({"created": True}), encoding="utf-8")
    return {"uploadId": upload_id, "chunkSizeHint": 512 * 1024}


@router.post("/upload/chunk")
async def upload_chunk(
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    file_name: str = Form(...),
    total_chunks: int = Form(1),
) -> dict:
    _validate_filename(file_name)

    chunk_dir = UPLOAD_DIR / upload_id
    if not chunk_dir.exists():
        raise HTTPException(status_code=404, detail="Upload session not found. Call /upload/init first.")

    chunk_path = chunk_dir / f"chunk_{chunk_index:06d}"
    data = await file.read()

    if len(data) > CHUNK_SIZE_LIMIT:
        raise HTTPException(status_code=413, detail="Chunk too large (max 5 MB)")

    chunk_path.write_bytes(data)

    # Persist metadata
    meta_path = chunk_dir / "meta.json"
    meta = {"file_name": file_name, "total_chunks": total_chunks, "received": chunk_index + 1}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    return {
        "uploadId": upload_id,
        "chunkIndex": chunk_index,
        "received": meta["received"],
        "totalChunks": total_chunks,
    }


@router.post("/upload/complete")
async def complete_upload(body: ChunkCompleteRequest) -> dict:
    chunk_dir = UPLOAD_DIR / body.upload_id
    if not chunk_dir.exists():
        raise HTTPException(status_code=404, detail="Upload session not found")

    meta_path = chunk_dir / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=400, detail="No metadata found")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # Verify all chunks present
    for i in range(body.total_chunks):
        chunk_path = chunk_dir / f"chunk_{i:06d}"
        if not chunk_path.exists():
            raise HTTPException(status_code=400, detail=f"Missing chunk {i} of {body.total_chunks}")

    # Assemble file
    file_name = meta.get("file_name", body.file_name)
    safe_name = Path(file_name).name
    output_path = chunk_dir / safe_name

    with open(output_path, "wb") as out:
        for i in range(body.total_chunks):
            chunk_path = chunk_dir / f"chunk_{i:06d}"
            out.write(chunk_path.read_bytes())

    total_size = output_path.stat().st_size
    if total_size > MAX_FILE_SIZE:
        shutil.rmtree(chunk_dir, ignore_errors=True)
        raise HTTPException(status_code=413, detail="Assembled file exceeds 50 MB limit")

    file_id = body.upload_id
    category = _category_for(safe_name)

    # Record final metadata
    final_meta = {
        "fileId": file_id,
        "fileName": safe_name,
        "size": total_size,
        "category": category,
        "path": str(output_path),
    }
    meta_path.write_text(json.dumps(final_meta), encoding="utf-8")

    # Clean up individual chunks
    for i in range(body.total_chunks):
        chunk_path = chunk_dir / f"chunk_{i:06d}"
        chunk_path.unlink(missing_ok=True)

    return final_meta


@router.get("/upload/{upload_id}")
async def get_upload_status(upload_id: str) -> dict:
    chunk_dir = UPLOAD_DIR / upload_id
    if not chunk_dir.exists():
        raise HTTPException(status_code=404, detail="Upload not found")

    meta_path = chunk_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if "fileId" in meta:
            return {"status": "completed", **meta}
        return {
            "status": "in_progress",
            "uploadId": upload_id,
            "fileName": meta.get("file_name", ""),
            "received": meta.get("received", 0),
            "totalChunks": meta.get("total_chunks", 0),
        }
    return {"status": "unknown", "uploadId": upload_id}


@router.get("/download/{file_id}")
async def download_file(file_id: str) -> dict:
    """Return file metadata and content for AI consumption."""
    chunk_dir = UPLOAD_DIR / file_id
    if not chunk_dir.exists():
        raise HTTPException(status_code=404, detail="File not found")

    meta_path = chunk_dir / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="File metadata not found")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    file_path = Path(meta["path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File data not found on disk")

    # For text/code files, read content; for binary, return base64
    ext = Path(meta["fileName"]).suffix.lower()
    text_cats = {"code", "config", "document"}
    if meta["category"] in text_cats and ext not in {".pdf", ".docx", ".rtf"}:
        try:
            content = file_path.read_text(encoding="utf-8")
            return {**meta, "content": content, "encoding": "text"}
        except (UnicodeDecodeError, UnicodeError):
            pass

    import base64
    content = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return {**meta, "content": content, "encoding": "base64"}
