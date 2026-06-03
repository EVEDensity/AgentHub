from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import DATA_DIR, PROJECT_ROOT
from app.services.auth_service import get_current_user
from app.utils.async_file import aexists, aread_bytes, aread_text, aunlink, awrite_bytes, awrite_text, astat_size, armtree, amkdir

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
    await amkdir(chunk_dir, parents=True, exist_ok=True)
    await awrite_text(chunk_dir / "meta.json", json.dumps({"created": True}))
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
    if not await aexists(chunk_dir):
        raise HTTPException(status_code=404, detail="Upload session not found. Call /upload/init first.")

    chunk_path = chunk_dir / f"chunk_{chunk_index:06d}"
    data = await file.read()

    if len(data) > CHUNK_SIZE_LIMIT:
        raise HTTPException(status_code=413, detail="Chunk too large (max 5 MB)")

    await awrite_bytes(chunk_path, data)

    # Persist metadata
    meta_path = chunk_dir / "meta.json"
    meta = {"file_name": file_name, "total_chunks": total_chunks, "received": chunk_index + 1}
    await awrite_text(meta_path, json.dumps(meta))

    return {
        "uploadId": upload_id,
        "chunkIndex": chunk_index,
        "received": meta["received"],
        "totalChunks": total_chunks,
    }


@router.post("/upload/complete")
async def complete_upload(body: ChunkCompleteRequest) -> dict:
    chunk_dir = UPLOAD_DIR / body.upload_id
    if not await aexists(chunk_dir):
        raise HTTPException(status_code=404, detail="Upload session not found")

    meta_path = chunk_dir / "meta.json"
    if not await aexists(meta_path):
        raise HTTPException(status_code=400, detail="No metadata found")

    meta = json.loads(await aread_text(meta_path))

    # Verify all chunks present
    for i in range(body.total_chunks):
        chunk_path = chunk_dir / f"chunk_{i:06d}"
        if not await aexists(chunk_path):
            raise HTTPException(status_code=400, detail=f"Missing chunk {i} of {body.total_chunks}")

    # Assemble file
    file_name = meta.get("file_name", body.file_name)
    safe_name = Path(file_name).name
    output_path = chunk_dir / safe_name

    # Async chunk assembly via thread
    def _assemble():
        with open(output_path, "wb") as out:
            for i in range(body.total_chunks):
                cp = chunk_dir / f"chunk_{i:06d}"
                out.write(cp.read_bytes())

    await asyncio.to_thread(_assemble)

    total_size = await astat_size(output_path)
    if total_size > MAX_FILE_SIZE:
        await armtree(chunk_dir)
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
    await awrite_text(meta_path, json.dumps(final_meta))

    # Clean up individual chunks
    for i in range(body.total_chunks):
        chunk_path = chunk_dir / f"chunk_{i:06d}"
        await aunlink(chunk_path)

    return final_meta


@router.get("/upload/{upload_id}")
async def get_upload_status(upload_id: str) -> dict:
    chunk_dir = UPLOAD_DIR / upload_id
    if not await aexists(chunk_dir):
        raise HTTPException(status_code=404, detail="Upload not found")

    meta_path = chunk_dir / "meta.json"
    if await aexists(meta_path):
        meta = json.loads(await aread_text(meta_path))
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
    if not await aexists(chunk_dir):
        raise HTTPException(status_code=404, detail="File not found")

    meta_path = chunk_dir / "meta.json"
    if not await aexists(meta_path):
        raise HTTPException(status_code=404, detail="File metadata not found")

    meta = json.loads(await aread_text(meta_path))
    file_path = Path(meta["path"])
    if not await aexists(file_path):
        raise HTTPException(status_code=404, detail="File data not found on disk")

    # For text/code files, read content; for binary, return base64
    ext = Path(meta["fileName"]).suffix.lower()
    text_cats = {"code", "config", "document"}
    if meta["category"] in text_cats and ext not in {".pdf", ".docx", ".rtf"}:
        try:
            content = await aread_text(file_path)
            return {**meta, "content": content, "encoding": "text"}
        except (UnicodeDecodeError, UnicodeError):
            pass

    import base64
    content_bytes = await aread_bytes(file_path)
    content = base64.b64encode(content_bytes).decode("ascii")
    return {**meta, "content": content, "encoding": "base64"}


# ── Workspace file preview endpoints ──────────────────────────────────

from app.config import PROJECT_ROOT

WORKSPACE_ROOT = Path(PROJECT_ROOT) if PROJECT_ROOT else DATA_DIR.parent


def _safe_workspace_path(rel_path: str) -> Path | None:
    """Resolve a relative path within WORKSPACE_ROOT, preventing traversal."""
    try:
        resolved = (WORKSPACE_ROOT / rel_path).resolve()
        resolved.relative_to(WORKSPACE_ROOT.resolve())
        return resolved
    except (ValueError, OSError):
        return None


@router.get("/workspace/list")
async def list_workspace_files(
    subdir: str = "",
    user: dict = Depends(get_current_user),
) -> dict:
    """List files in the workspace directory for the file tree."""
    base = _safe_workspace_path(subdir) if subdir else WORKSPACE_ROOT.resolve()
    if base is None or not await aexists(base):
        return {"files": [], "path": subdir, "error": "Directory not found or access denied"}

    files: list[dict] = []
    dirs: list[dict] = []
    try:
        for entry in base.iterdir():
            name = entry.name
            if name.startswith(".") and name not in (".env", ".gitignore", ".editorconfig"):
                continue
            is_dir = entry.is_dir()
            item: dict = {
                "name": name,
                "path": str(entry.relative_to(WORKSPACE_ROOT.resolve())),
                "isDirectory": is_dir,
            }
            if not is_dir:
                item["size"] = entry.stat().st_size
                ext = entry.suffix.lower()
                item["language"] = _ext_to_language(ext)
            if is_dir:
                dirs.append(item)
            else:
                files.append(item)
    except PermissionError:
        return {"files": [], "dirs": [], "path": subdir, "error": "Permission denied"}

    # dirs first, then files; both alphabetical
    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    return {"path": subdir, "dirs": dirs, "files": files, "total": len(dirs) + len(files)}


@router.get("/workspace/read")
async def read_workspace_file(
    path: str,
    max_lines: int = 10000,
    user: dict = Depends(get_current_user),
) -> dict:
    """Read a workspace file and return content with language detection.

    Supports code, markdown, and text files. Returns binary indicator for
    non-previewable files.
    """
    safe = _safe_workspace_path(path)
    if safe is None:
        raise HTTPException(status_code=400, detail=f"Path '{path}' is outside workspace")

    if not await aexists(safe):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    if safe.is_dir():
        raise HTTPException(status_code=400, detail="Path is a directory, not a file")

    file_size = safe.stat().st_size
    if file_size > 10 * 1024 * 1024:  # 10 MB limit
        return {
            "path": path,
            "name": safe.name,
            "size": file_size,
            "state": "too_large",
            "language": _ext_to_language(safe.suffix.lower()),
        }

    ext = safe.suffix.lower()
    text_exts = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".c", ".cpp",
        ".h", ".hpp", ".swift", ".kt", ".rb", ".php", ".sql", ".sh", ".bash",
        ".vue", ".svelte", ".astro", ".html", ".htm", ".css", ".scss", ".less",
        ".json", ".yaml", ".yml", ".xml", ".toml", ".ini", ".cfg", ".conf",
        ".cnf", ".env", ".editorconfig", ".gitignore", ".md", ".txt", ".tex",
        ".rst", ".org", ".log", ".csv", ".tsv", ".graphql", ".gql", ".proto",
        ".dockerfile", ".makefile", ".prisma", ".gemfile",
    }

    if ext not in text_exts:
        # Binary file – return metadata only
        return {
            "path": path,
            "name": safe.name,
            "size": file_size,
            "state": "binary",
            "language": "binary",
        }

    try:
        content = await aread_text(safe)
    except (UnicodeDecodeError, UnicodeError):
        return {
            "path": path,
            "name": safe.name,
            "size": file_size,
            "state": "binary",
            "language": "binary",
        }

    lines = content.split("\n")
    truncated = len(lines) > max_lines
    display_content = "\n".join(lines[:max_lines])

    return {
        "path": path,
        "name": safe.name,
        "size": file_size,
        "content": display_content,
        "totalLines": len(lines),
        "truncated": truncated,
        "state": "ok",
        "language": _ext_to_language(ext),
    }


def _ext_to_language(ext: str) -> str:
    """Map file extension to language identifier."""
    return {
        ".py": "python", ".pyi": "python",
        ".ts": "typescript", ".tsx": "tsx",
        ".js": "javascript", ".jsx": "jsx",
        ".java": "java", ".go": "go", ".rs": "rust",
        ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
        ".swift": "swift", ".kt": "kotlin",
        ".rb": "ruby", ".php": "php",
        ".sh": "bash", ".bash": "bash",
        ".sql": "sql", ".graphql": "graphql", ".gql": "graphql",
        ".html": "html", ".htm": "html",
        ".css": "css", ".scss": "scss", ".less": "less",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml",
        ".toml": "toml", ".ini": "ini", ".cfg": "ini",
        ".md": "markdown", ".mdx": "mdx",
        ".txt": "text", ".log": "text",
        ".vue": "vue", ".svelte": "svelte", ".astro": "astro",
        ".xml": "xml",
        ".dockerfile": "dockerfile", ".makefile": "makefile",
    }.get(ext, "text")


# ── Workspace upload endpoint ──────────────────────────────────────────

WORKSPACE_MAX_SIZE = 50 * 1024 * 1024  # 50 MB per file


@router.post("/workspace/upload")
async def upload_to_workspace(
    file: UploadFile = File(...),
    subdir: str = "",
    user: dict = Depends(get_current_user),
) -> dict:
    """Upload a file directly into WORKSPACE_ROOT (or subdirectory).

    Writes the file to the workspace so it's immediately visible in the
    file tree and available for agent tools like file_read / file_write.
    """
    safe_name = Path(file.filename or "untitled").name
    if not safe_name or ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Resolve target directory
    base = WORKSPACE_ROOT.resolve()
    if subdir:
        target_dir = _safe_workspace_path(subdir)
        if target_dir is None:
            raise HTTPException(status_code=400, detail=f"Invalid subdir: {subdir}")
    else:
        target_dir = base

    if not await aexists(target_dir):
        await amkdir(target_dir, parents=True, exist_ok=True)

    target_path = target_dir / safe_name

    # Stream to disk in chunks to bound memory
    CHUNK = 1 * 1024 * 1024  # 1 MB
    total = 0
    oversize = False
    try:
        with open(target_path, "wb") as out:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > WORKSPACE_MAX_SIZE:
                    oversize = True
                    break  # exit with-block first so Windows can unlink
                out.write(chunk)
    except Exception as exc:
        if await aexists(target_path):
            await aunlink(target_path)
        raise HTTPException(status_code=500, detail=f"Write failed: {exc}")

    if oversize:
        await aunlink(target_path)
        raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")

    file_size = target_path.stat().st_size
    ext = safe_name.split(".")[-1].lower() if "." in safe_name else ""
    return {
        "success": True,
        "name": safe_name,
        "path": str(target_path.relative_to(base)),
        "size": file_size,
        "language": _ext_to_language(f".{ext}") if ext else "text",
        "message": f"已上传到工作区: {safe_name}",
    }
