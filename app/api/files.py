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

from app.config import DATA_DIR, WORKSPACES_DIR, OFFICE_PREVIEW_MAX_MB, OFFICE_WORKSPACE_READ_MAX_MB
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
    "presentation": {".pptx", ".ppt"},
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


# ── Uploaded file preview endpoint (unified text/code/md/docx) ────────────

_PREVIEW_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".java", ".go", ".rs", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".swift", ".kt", ".rb", ".php", ".pl", ".sh", ".bash", ".zsh",
    ".ps1", ".bat", ".cmd", ".sql", ".graphql", ".gql", ".proto",
    ".html", ".htm", ".css", ".scss", ".less", ".sass",
    ".json", ".yaml", ".yml", ".xml", ".toml", ".ini", ".cfg", ".conf",
    ".cnf", ".env", ".properties", ".vue", ".svelte", ".astro",
    ".tex", ".org", ".csv", ".tsv",
}


def _detect_preview_kind(ext: str) -> str:
    """Return one of: text, markdown, docx, pdf, image, binary."""
    ext = ext.lower()
    if ext in {".md", ".markdown", ".mdx"}:
        return "markdown"
    if ext == ".docx":
        return "docx"
    if ext in {".pptx", ".ppt"}:
        return "pptx"
    if ext == ".pdf":
        return "pdf"
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}:
        return "image"
    if ext in _PREVIEW_TEXT_EXTS:
        return "text"
    return "binary"


def _detect_image_mime(ext: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
    }.get(ext.lower(), "application/octet-stream")


def _extract_docx_html(file_path: str, max_chars: int) -> dict:
    """Parse a .docx file and return HTML with inline base64 images.

    Opens the .docx as a ZIP, extracts images from ``word/media/*``, then
    walks ``word/document.xml`` to interleave text paragraphs, tables, and
    embedded images.  Returns a dict with keys:

    * ``content``       – full HTML string (truncated to *max_chars*)
    * ``contentType``   – ``"html"``
    * ``totalChars``    – original HTML length before truncation
    * ``truncated``     – whether the HTML was truncated
    * ``imageCount``    – number of embedded images found
    * ``textLength``    – plain-text character count (for the UI footer)
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile
    from base64 import b64encode
    from io import BytesIO

    MIME_BY_EXT: dict[str, str] = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }

    W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
    RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

    # ------------------------------------------------------------------
    # Step 1 – Open the ZIP and map image relationships
    # ------------------------------------------------------------------
    with zipfile.ZipFile(file_path, "r") as zf:
        # rId → (target_filename, mime)
        rels_map: dict[str, tuple[str, str]] = {}
        try:
            rels_xml = zf.read("word/_rels/document.xml.rels")
            rels_root = ET.fromstring(rels_xml)
            for rel_el in rels_root:
                rid = rel_el.get("Id")
                target = rel_el.get("Target", "")
                rtype = rel_el.get("Type", "")
                if rid and "image" in rtype.lower():
                    ext = Path(target).suffix.lower()
                    rels_map[rid] = (target, MIME_BY_EXT.get(ext, "application/octet-stream"))
        except (KeyError, ET.ParseError):
            pass

        # Read every referenced image blob → base64
        media_data: dict[str, tuple[str, str]] = {}  # target → (mime, b64)
        for target, mime in rels_map.values():
            try:
                blob = zf.read(f"word/{target}")
                media_data[target] = (mime, b64encode(blob).decode("ascii"))
            except KeyError:
                pass

        # ------------------------------------------------------------------
        # Step 2 – Parse word/document.xml
        # ------------------------------------------------------------------
        doc_xml = zf.read("word/document.xml")
        doc_root = ET.fromstring(doc_xml)

    body_el = doc_root.find(f"{{{W_NS}}}body")
    if body_el is None:
        return {
            "content": "<p>(空文档)</p>",
            "contentType": "html",
            "totalChars": 0,
            "truncated": False,
            "imageCount": 0,
            "textLength": 0,
        }

    html_parts: list[str] = []
    text_chunks: list[str] = []
    image_count = 0

    # ------------------------------------------------------------------
    # Helpers for namespace-heavy lookups
    # ------------------------------------------------------------------
    def _attrv(el: ET.Element, local: str) -> str | None:
        return el.get(f"{{{W_NS}}}{local}")

    def _qname(ns: str, local: str) -> str:
        return f"{{{ns}}}{local}"

    def _iter_elems(parent: ET.Element, ns: str, local: str):
        return parent.iter(_qname(ns, local))

    def _find_elems(parent: ET.Element, ns: str, local: str):
        return parent.findall(f".//{{{ns}}}{local}")

    # ------------------------------------------------------------------
    # Step 3 – Walk body children (paragraphs & tables)
    # ------------------------------------------------------------------
    for child in body_el:
        tag_local = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag

        # ── Paragraph ────────────────────────────────────────────────
        if tag_local == "p":

            # --- detect heading level ---
            heading_level = 0
            pPr = child.find(_qname(W_NS, "pPr"))
            if pPr is not None:
                pStyle = pPr.find(_qname(W_NS, "pStyle"))
                if pStyle is not None:
                    style_val = _attrv(pStyle, "val") or ""
                    if style_val.lower().startswith("heading"):
                        m = re.search(r"\d+", style_val)
                        heading_level = max(1, min(6, int(m.group()) if m else 1))

            run_html: list[str] = []
            run_text: list[str] = []

            for r_el in child.findall(_qname(W_NS, "r")):
                # --- drawings / images inside the run ---
                for drawing in r_el.findall(_qname(W_NS, "drawing")):
                    for blip in drawing.iter(_qname(A_NS, "blip")):
                        embed = blip.get(_qname(R_NS, "embed"))
                        if embed and embed in rels_map:
                            target, mime = rels_map[embed]
                            if target in media_data:
                                _, b64 = media_data[target]
                                img_html = (
                                    f'<img src="data:{mime};base64,{b64}" '
                                    f'style="max-width:100%;height:auto;display:block;'
                                    f'margin:8px 0;border-radius:6px;" '
                                    f'alt="图片 {image_count + 1}" />'
                                )
                                run_html.append(img_html)
                                run_text.append("[图片]")
                                image_count += 1

                # --- text nodes ---
                for t_el in r_el.findall(_qname(W_NS, "t")):
                    t = t_el.text or ""
                    run_text.append(t)
                    # minimal XML escaping for HTML safety
                    escaped = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    run_html.append(escaped)

            combined_html = "".join(run_html).strip()
            combined_text = "".join(run_text).strip()

            if combined_html:
                if heading_level:
                    tag = f"h{heading_level}"
                    html_parts.append(f"<{tag}>{combined_html}</{tag}>")
                else:
                    html_parts.append(f"<p>{combined_html}</p>")
                text_chunks.append(combined_text)

        # ── Table ────────────────────────────────────────────────────
        elif tag_local == "tbl":
            html_parts.append(
                '<table style="border-collapse:collapse;width:100%;margin:8px 0;'
                'font-size:14px;">'
            )
            for tr in child.findall(_qname(W_NS, "tr")):
                html_parts.append("<tr>")
                row_texts: list[str] = []
                for tc in tr.findall(_qname(W_NS, "tc")):
                    cell_strs: list[str] = []
                    for p in tc.findall(_qname(W_NS, "p")):
                        for t in p.findall(_qname(W_NS, "t")):
                            if t.text:
                                cell_strs.append(
                                    t.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                                )
                    cell_text = " ".join(cell_strs).strip()
                    html_parts.append(
                        f'<td style="border:1px solid #ddd;padding:6px 10px;">{cell_text}</td>'
                    )
                    row_texts.append(cell_text)
                html_parts.append("</tr>")
                text_chunks.append(" | ".join(row_texts))
            html_parts.append("</table>")

    # ------------------------------------------------------------------
    # Step 4 – Assemble complete HTML document
    # ------------------------------------------------------------------
    body_html = "\n".join(html_parts)
    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{
    font-family: system-ui, -apple-system, 'Microsoft YaHei', 'PingFang SC', 'Noto Sans SC', sans-serif;
    font-size: 15px; line-height: 1.7; color: #1a1a1a;
    max-width: 860px; margin: 0 auto; padding: 20px 24px;
    background: #fff;
  }}
  h1 {{ font-size: 1.6em; margin: 20px 0 10px; color: #111; border-bottom: 2px solid #eee; padding-bottom: 6px; }}
  h2 {{ font-size: 1.35em; margin: 18px 0 8px; color: #222; }}
  h3 {{ font-size: 1.15em; margin: 14px 0 6px; color: #333; }}
  h4, h5, h6 {{ margin: 12px 0 4px; }}
  p {{ margin: 0 0 8px; }}
  img {{ max-width: 100%; height: auto; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
  td, th {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

    text_body = "\n\n".join(t for t in text_chunks if t)
    total_chars = len(full_html)
    truncated = total_chars > max_chars
    content = full_html[:max_chars] if truncated else full_html

    return {
        "content": content,
        "contentType": "html",
        "totalChars": total_chars,
        "truncated": truncated,
        "imageCount": image_count,
        "textLength": len(text_body),
    }


def _extract_pptx_html(file_path: str, max_chars: int) -> dict:
    """Parse a .pptx file and return HTML with inline base64 images.

    Opens the .pptx as a ZIP, enumerates ``ppt/slides/slideN.xml``,
    extracts images from ``ppt/media/*``, and converts every slide to a
    styled card in a self-contained HTML document.

    Returns a dict with keys:

    * ``content``       – full HTML string (truncated to *max_chars*)
    * ``contentType``   – ``"html"``
    * ``totalChars``    – original HTML length before truncation
    * ``truncated``     – whether the HTML was truncated
    * ``imageCount``    – number of embedded images found
    * ``textLength``    – plain-text character count
    * ``slideCount``    – number of slides
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile
    from base64 import b64encode

    MIME_BY_EXT: dict[str, str] = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
        ".svg": "image/svg+xml", ".tiff": "image/tiff", ".tif": "image/tiff",
    }

    # ── Image optimisation for web preview ────────────────────────
    def _optimize_image(blob: bytes, ext: str) -> tuple[bytes, str]:
        """Resize + compress an image blob for HTML preview.

        Returns ``(optimized_bytes, mime_type)``.  Dimensions larger than
        *MAX_PX* are down-scaled proportionally (LANCZOS).  Raster images
        are re-encoded at quality 85; PNGs without alpha are converted to
        JPEG for a 5-10× size reduction.  SVG, GIF and unrecognised blobs
        pass through unchanged so the preview always works.
        """
        _svgext = ext == ".svg"
        if _svgext:
            return blob, "image/svg+xml"

        try:
            from io import BytesIO
            from PIL import Image

            img = Image.open(BytesIO(blob))
            w, h = img.size
            max_dim = 1920
            if max(w, h) > max_dim:
                ratio = max_dim / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

            out = BytesIO()
            has_alpha = img.mode in ("RGBA", "PA", "LA")

            # GIF / animated → keep as-is (Pillow loses animation on resize)
            if ext == ".gif":
                img.save(out, format="GIF", optimize=True)
                return out.getvalue(), "image/gif"

            if not has_alpha:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(out, format="JPEG", quality=85, optimize=True)
                return out.getvalue(), "image/jpeg"

            # Keep PNG for transparent images
            img.save(out, format="PNG", optimize=True)
            return out.getvalue(), "image/png"
        except Exception:
            mime = MIME_BY_EXT.get(ext, "application/octet-stream")
            return blob, mime

    P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
    A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
    R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

    def _qname(ns: str, local: str) -> str:
        return f"{{{ns}}}{local}"

    def _xml_text(el: ET.Element) -> str:
        """Recursively collect all text from ``<a:t>`` descendants."""
        parts: list[str] = []
        for t in el.iter(_qname(A_NS, "t")):
            if t.text:
                parts.append(t.text)
        return "".join(parts)

    # ------------------------------------------------------------------
    # Step 1 – Open ZIP, enumerate slides
    # ------------------------------------------------------------------
    try:
        zf = zipfile.ZipFile(file_path, "r")
    except zipfile.BadZipFile:
        # File is not a valid ZIP archive: could be an old .ppt binary,
        # a corrupted file, or an empty placeholder.
        return {
            "content": (
                '<div style="text-align:center;padding:40px;color:#888;">'
                "<p>⚠️ 无法解析此文件</p>"
                "<p style=\"font-size:14px;margin-top:8px;\">"
                "文件不是有效的 PPTX/ZIP 格式，可能为空文件、已损坏，或为旧版 .ppt 二进制格式。</p>"
                "<p style=\"font-size:13px;margin-top:4px;\">"
                "请尝试用 PowerPoint 打开后另存为 .pptx 格式。</p>"
                "</div>"
            ),
            "contentType": "html",
            "totalChars": 0,
            "truncated": False,
            "imageCount": 0,
            "textLength": 0,
            "slideCount": 0,
            "error": "文件不是有效的PPTX格式（可能为空文件、旧版.ppt或已损坏）",
        }
    with zf:
        slide_names = sorted(
            [n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)],
            key=lambda n: int(re.search(r"slide(\d+)", n).group(1))  # type: ignore[union-attr]
        )

        if not slide_names:
            # Old .ppt format (binary OLE2) – zipfile sees nothing useful
            return {
                "content": "<p>(无法解析旧版 .ppt 格式；仅支持 .pptx)</p>",
                "contentType": "html",
                "totalChars": 0,
                "truncated": False,
                "imageCount": 0,
                "textLength": 0,
                "slideCount": 0,
            }

        # --- Load all media blobs into memory (optimised) ---
        media_data: dict[str, str] = {}  # relative_path → base64
        for name in zf.namelist():
            if name.startswith("ppt/media/") and not name.endswith("/"):
                ext = Path(name).suffix.lower()
                blob = zf.read(name)
                opt_blob, mime = _optimize_image(blob, ext)
                media_data[name] = f"data:{mime};base64,{b64encode(opt_blob).decode('ascii')}"

        # ------------------------------------------------------------------
        # Step 2 – Process each slide
        # ------------------------------------------------------------------
        slide_cards: list[str] = []
        total_text_len = 0
        total_images = 0

        for idx, slide_name in enumerate(slide_names, start=1):
            # --- 2a. Relationships: rId → media target ---
            rels_map: dict[str, str] = {}  # rId → media data-URI
            rels_name = f"ppt/slides/_rels/slide{idx}.xml.rels"
            try:
                rels_xml = zf.read(rels_name)
                rels_root = ET.fromstring(rels_xml)
                for rel_el in rels_root:
                    rid = rel_el.get("Id")
                    target = rel_el.get("Target", "")
                    rtype = rel_el.get("Type", "")
                    if rid and "image" in rtype.lower():
                        # target = "../media/image1.png"
                        normalized = f"ppt/media/{target.split('/')[-1]}"
                        if normalized in media_data:
                            rels_map[rid] = media_data[normalized]
            except (KeyError, ET.ParseError):
                pass

            # --- 2b. Parse slide XML ---
            slide_xml = zf.read(slide_name)
            slide_root = ET.fromstring(slide_xml)

            # --- 2c. Slide dimensions ---
            sld_sz = slide_root.find(_qname(P_NS, "sldSz"))
            slide_w_emu = int(sld_sz.get("cx", "12192000")) if sld_sz is not None else 12192000
            slide_h_emu = int(sld_sz.get("cy", "6858000")) if sld_sz is not None else 6858000
            # EMU → CSS px (1 EMU = 1/914400 inch → 1 EMU ≈ 96/914400 px)
            slide_w_px = max(300, slide_w_emu * 96 // 914400)
            slide_h_px = max(200, slide_h_emu * 96 // 914400)

            # --- 2d. Walk shapes ---
            shape_html: list[str] = []
            shape_text: list[str] = []
            for sp in slide_root.iter(_qname(P_NS, "sp")):
                # --- Position & size ---
                xfrm = sp.find(_qname(P_NS, "spPr"))
                off = {"x": 0, "y": 0}
                ext = {"cx": slide_w_emu, "cy": slide_h_emu}
                if xfrm is not None:
                    xfrm_el = xfrm.find(_qname(A_NS, "xfrm"))
                    if xfrm_el is not None:
                        off_el = xfrm_el.find(_qname(A_NS, "off"))
                        ext_el = xfrm_el.find(_qname(A_NS, "ext"))
                        if off_el is not None:
                            off["x"] = int(off_el.get("x", "0"))
                            off["y"] = int(off_el.get("y", "0"))
                        if ext_el is not None:
                            ext["cx"] = int(ext_el.get("cx", str(slide_w_emu)))
                            ext["cy"] = int(ext_el.get("cy", str(slide_h_emu)))

                left_pct = off["x"] * 100 / slide_w_emu
                top_pct = off["y"] * 100 / slide_h_emu
                w_pct = ext["cx"] * 100 / slide_w_emu
                h_pct = ext["cy"] * 100 / slide_h_emu

                # --- Images ---
                for blip in sp.iter(_qname(A_NS, "blip")):
                    embed = blip.get(_qname(R_NS, "embed"))
                    if embed and embed in rels_map:
                        shape_html.append(
                            f'<img src="{rels_map[embed]}" '
                            f'style="position:absolute;left:{left_pct:.1f}%;top:{top_pct:.1f}%;'
                            f'width:{w_pct:.1f}%;height:{h_pct:.1f}%;object-fit:contain;" '
                            f'alt="Slide {idx} image" />'
                        )
                        shape_text.append("[图片]")
                        total_images += 1

                # --- Text ---
                txt = _xml_text(sp)
                if txt.strip():
                    escaped = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    # Smarter font-size: use the smaller of height-based and
                    # width-per-character estimates, so long titles don't get
                    # clipped inside narrow shapes.
                    shape_h_px = max(1, ext["cy"] * 96 // 914400)
                    shape_w_px = max(1, ext["cx"] * 96 // 914400)
                    char_count = max(1, len(txt))
                    font_from_h = shape_h_px // 4       # fits ~4 lines
                    font_from_w = (shape_w_px - 8) // char_count  # roughly fit 1 line
                    font_px = max(10, min(36, font_from_h, font_from_w))
                    shape_html.append(
                        f'<div style="position:absolute;left:{left_pct:.1f}%;top:{top_pct:.1f}%;'
                        f'width:{w_pct:.1f}%;height:{h_pct:.1f}%;'
                        f'display:flex;align-items:center;justify-content:center;'
                        f'font-size:{font_px}px;'
                        f'word-break:keep-all;overflow-wrap:break-word;'
                        f'overflow:hidden;padding:4px;'
                        f'text-align:center;color:#333;">{escaped}</div>'
                    )
                    shape_text.append(txt)

            combined_text = " ".join(shape_text).strip()
            total_text_len += len(combined_text)

            slide_cards.append(
                f'<div class="slide-card" style="position:relative;'
                f'width:{slide_w_px}px;height:{slide_h_px}px;'
                f'max-width:100%;'
                f'background:#fff;border-radius:8px;'
                f'box-shadow:0 1px 6px rgba(0,0,0,.1);overflow:hidden;'
                f'margin:0 auto 24px;flex-shrink:0;">'
                + "\n".join(shape_html) +
                f'</div>'
            )

    # ------------------------------------------------------------------
    # Step 3 – Assemble full HTML
    # ------------------------------------------------------------------
    body_html = "\n".join(slide_cards)
    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{
    font-family:system-ui,-apple-system,'Microsoft YaHei','PingFang SC','Noto Sans SC','WenQuanYi Micro Hei',sans-serif;
    background:#f0f2f5; padding:24px 16px;
    display:flex; flex-direction:column; align-items:center;
  }}
  .slide-card {{
    transition: box-shadow .2s;
  }}
  .slide-card:hover {{
    box-shadow: 0 4px 20px rgba(0,0,0,.15);
  }}
  .slide-number {{
    text-align:center; font-size:12px; color:#999; margin-bottom:8px;
  }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

    total_chars = len(full_html)
    truncated = total_chars > max_chars
    content = full_html[:max_chars] if truncated else full_html

    return {
        "content": content,
        "contentType": "html",
        "totalChars": total_chars,
        "truncated": truncated,
        "imageCount": total_images,
        "textLength": total_text_len,
        "slideCount": len(slide_cards),
    }


@router.get("/preview/{file_id}")
async def preview_uploaded_file(
    file_id: str,
    max_chars: int = 200_000,
    user: dict = Depends(get_current_user),
) -> dict:
    """Return preview-ready content for a previously uploaded file.

    This is the single endpoint the frontend uses for inline file previews
    (eye button on attachment chips). The response shape is normalized so
    the client can switch on `kind`:

    - kind=text:        raw UTF-8 content
    - kind=markdown:    raw UTF-8 content (frontend renders with react-markdown)
    - kind=docx:        converted to plain text via python-docx (best-effort,
                        preserves paragraph breaks)
    - kind=pdf:         raw bytes returned as base64; the frontend renders
                        pages with pdfjs-dist
    - kind=image:       raw bytes returned as base64; the frontend renders
                        the image directly
    - kind=binary:      no content, just metadata; the frontend should show
                        a friendly "preview not supported" message
    - state=too_large:  file exceeds 5 MB; same shape as binary
    - state=missing:    file metadata or payload no longer exists
    """
    if max_chars < 1000 or max_chars > 2_000_000:
        raise HTTPException(status_code=400, detail="max_chars must be in [1000, 2000000]")

    chunk_dir = UPLOAD_DIR / file_id
    if not await aexists(chunk_dir):
        return {"fileId": file_id, "state": "missing", "kind": "binary"}

    meta_path = chunk_dir / "meta.json"
    if not await aexists(meta_path):
        return {"fileId": file_id, "state": "missing", "kind": "binary"}

    try:
        meta = json.loads(await aread_text(meta_path))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"fileId": file_id, "state": "missing", "kind": "binary"}

    file_name = meta.get("fileName") or meta.get("name") or "未命名文件"
    ext = Path(file_name).suffix.lower()
    file_path_str = meta.get("path")
    if not file_path_str:
        return {
            "fileId": file_id,
            "name": file_name,
            "ext": ext,
            "size": meta.get("size", 0),
            "category": meta.get("category", "unknown"),
            "state": "missing",
            "kind": _detect_preview_kind(ext),
        }
    file_path = Path(file_path_str)
    if not await aexists(file_path):
        return {
            "fileId": file_id,
            "name": file_name,
            "ext": ext,
            "size": meta.get("size", 0),
            "category": meta.get("category", "unknown"),
            "state": "missing",
            "kind": _detect_preview_kind(ext),
        }

    try:
        file_size = (await asyncio.to_thread(file_path.stat)).st_size
    except OSError:
        file_size = meta.get("size", 0)

    kind = _detect_preview_kind(ext)
    base = {
        "fileId": file_id,
        "name": file_name,
        "ext": ext,
        "size": file_size,
        "category": meta.get("category", "unknown"),
        "kind": kind,
    }

    # Size cap: office documents (pptx/docx) are allowed up to
    # OFFICE_PREVIEW_MAX_MB because they bundle images; other files
    # stay at 5 MB.
    _office_limit = OFFICE_PREVIEW_MAX_MB * 1024 * 1024
    _general_limit = 5 * 1024 * 1024
    if kind in ("pptx", "docx"):
        if file_size > _office_limit:
            return {**base, "state": "too_large", "size": file_size}
    elif file_size > _general_limit:
        return {**base, "state": "too_large", "size": file_size}

    if kind == "binary":
        return {**base, "state": "binary", "size": file_size}

    if kind == "docx":
        try:
            result = await asyncio.to_thread(_extract_docx_html, str(file_path), max_chars)
            return {**base, "state": "ok", **result}
        except Exception as exc:  # noqa: BLE001 - 任何 docx 解析异常都视作 binary
            return {
                **base,
                "state": "binary",
                "size": file_size,
                "error": f"docx 解析失败: {exc}",
            }

    if kind == "pptx":
        try:
            result = await asyncio.to_thread(_extract_pptx_html, str(file_path), max_chars)
            return {**base, "state": "ok", **result}
        except Exception as exc:  # noqa: BLE001
            return {
                **base,
                "state": "binary",
                "size": file_size,
                "error": f"pptx 解析失败: {exc}",
            }

    if kind in ("pdf", "image"):
        # PDF / 图片: 以 base64 返回原始字节, 前端用 pdfjs-dist 或 <img> 渲染
        try:
            import base64

            content_bytes = await aread_bytes(file_path)
        except OSError as exc:
            return {
                **base,
                "state": "binary",
                "size": file_size,
                "error": f"读取失败: {exc}",
            }
        encoded = base64.b64encode(content_bytes).decode("ascii")
        result: dict = {
            **base,
            "state": "ok",
            "content": encoded,
            "mimeType": _detect_image_mime(ext) if kind == "image" else "application/pdf",
            "size": file_size,
        }
        if kind == "pdf":
            # 顺便用 pypdf 提取纯文本, 方便前端做快速预览 / 搜索
            try:
                from pypdf import PdfReader

                def _read_text() -> tuple[str, int]:
                    reader = PdfReader(str(file_path))
                    pages = len(reader.pages)
                    chunks: list[str] = []
                    for i, page in enumerate(reader.pages):
                        try:
                            text = page.extract_text() or ""
                        except Exception:  # noqa: BLE001 - 单页解析失败不影响整体
                            text = ""
                        if text.strip():
                            chunks.append(text)
                    return ("\n\n".join(chunks), pages)

                extracted, page_count = await asyncio.to_thread(_read_text)
                result["pageCount"] = page_count
                truncated = len(extracted) > max_chars
                result["extractedText"] = extracted[:max_chars] if extracted else ""
                result["textTruncated"] = truncated
            except Exception as exc:  # noqa: BLE001
                result["textError"] = f"PDF 文本提取失败: {exc}"
        return result

    # kind in {text, markdown}
    try:
        content = await aread_text(file_path)
    except (UnicodeDecodeError, UnicodeError):
        return {**base, "state": "binary", "size": file_size}
    truncated = len(content) > max_chars
    display = content[:max_chars]
    return {
        **base,
        "state": "ok",
        "content": display,
        "truncated": truncated,
        "totalChars": len(content),
    }


# ── Workspace file preview endpoints ──────────────────────────────────
# Each user+session pair gets an isolated workspace:
#   DATA_DIR/workspaces/{user_id}/{session_id}/
# Files uploaded by user A are never visible to user B, and different
# conversations for the same user are also isolated from each other.

from app.services.workspace_context import (
    build_workspace_root,
    resolve_workspace_path,
    slugify_user_dir,
    slugify_session_dir,
)

WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)


def _get_session_workspace_root(user_id: str, session_id: str) -> Path:
    """Return (and auto-create) the per-user, per-session workspace root."""
    root = build_workspace_root(user_id, session_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_session_workspace_path(user_id: str, session_id: str, rel_path: str) -> Path | None:
    """Resolve a relative path within the user+session workspace, preventing traversal."""
    try:
        root = _get_session_workspace_root(user_id, session_id)
        resolved = (root / rel_path).resolve()
        resolved.relative_to(root)
        return resolved
    except (ValueError, OSError):
        return None


@router.get("/workspace/list")
async def list_workspace_files(
    subdir: str = "",
    session_id: str = "",
    user: dict = Depends(get_current_user),
) -> dict:
    """List files in the user's per-session workspace for the file tree.

    Each user+session has an isolated workspace:
    ``DATA_DIR/workspaces/{user_id}/{session_id}/``

    When *session_id* is omitted the listing falls back to the user-level
    directory so the tree still renders, but the agent tools will always
    operate within a session-scoped workspace.
    """
    sid = session_id or "default"
    ws_root = _get_session_workspace_root(user["id"], sid)

    base = _safe_session_workspace_path(user["id"], sid, subdir) if subdir else ws_root
    if base is None or not await aexists(base):
        return {"dirs": [], "files": [], "path": subdir, "total": 0, "error": "Directory not found"}

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
                "path": str(entry.relative_to(ws_root)),
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
        return {"dirs": [], "files": [], "path": subdir, "total": 0, "error": "Permission denied"}

    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    return {"path": subdir, "dirs": dirs, "files": files, "total": len(dirs) + len(files)}


@router.get("/workspace/read")
async def read_workspace_file(
    path: str,
    session_id: str = "",
    max_lines: int = 10000,
    user: dict = Depends(get_current_user),
) -> dict:
    """Read a file from the user's per-session workspace with language detection.

    Supports code, markdown, and text files. Returns a binary indicator for
    non-previewable files.
    """
    sid = session_id or "default"
    safe = _safe_session_workspace_path(user["id"], sid, path)
    if safe is None:
        raise HTTPException(status_code=400, detail=f"Path '{path}' is outside workspace")

    if not await aexists(safe):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    if safe.is_dir():
        raise HTTPException(status_code=400, detail="Path is a directory, not a file")

    file_size = safe.stat().st_size
    ext = safe.suffix.lower()

    # Office documents (pptx/docx) get a higher limit (default 30 MB).
    _ws_office_limit = OFFICE_WORKSPACE_READ_MAX_MB * 1024 * 1024
    _ws_general_limit = 10 * 1024 * 1024
    if ext in (".pptx", ".ppt", ".docx"):
        if file_size > _ws_office_limit:
            return {
                "path": path, "name": safe.name, "size": file_size,
                "state": "too_large", "language": _ext_to_language(ext),
            }
    elif file_size > _ws_general_limit:
        return {
            "path": path, "name": safe.name, "size": file_size,
            "state": "too_large", "language": _ext_to_language(ext),
        }
    text_exts = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".c", ".cpp",
        ".h", ".hpp", ".swift", ".kt", ".rb", ".php", ".sql", ".sh", ".bash",
        ".vue", ".svelte", ".astro", ".html", ".htm", ".css", ".scss", ".less",
        ".json", ".yaml", ".yml", ".xml", ".toml", ".ini", ".cfg", ".conf",
        ".cnf", ".env", ".editorconfig", ".gitignore", ".md", ".txt", ".tex",
        ".rst", ".org", ".log", ".csv", ".tsv", ".graphql", ".gql", ".proto",
        ".dockerfile", ".makefile", ".prisma", ".gemfile",
    }

    # ── Office document preview (pptx, docx) ──────────────────────────
    if ext in {".pptx", ".ppt"}:
        try:
            result = await asyncio.to_thread(
                _extract_pptx_html, str(safe), 200_000,
            )
            return {
                "path": path,
                "name": safe.name,
                "size": file_size,
                "state": "ok",
                "language": "pptx",
                "contentType": "html",
                **result,
            }
        except Exception as exc:
            import logging
            logging.getLogger("agenthub.files").warning(
                "pptx workspace preview failed for %s: %s", path, exc,
            )
            return {
                "path": path,
                "name": safe.name,
                "size": file_size,
                "state": "binary",
                "language": "binary",
            }

    if ext == ".docx":
        try:
            result = await asyncio.to_thread(
                _extract_docx_html, str(safe), 200_000,
            )
            return {
                "path": path,
                "name": safe.name,
                "size": file_size,
                "state": "ok",
                "language": "docx",
                "contentType": "html",
                **result,
            }
        except Exception as exc:
            import logging
            logging.getLogger("agenthub.files").warning(
                "docx workspace preview failed for %s: %s", path, exc,
            )
            return {
                "path": path,
                "name": safe.name,
                "size": file_size,
                "state": "binary",
                "language": "binary",
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
        ".pptx": "pptx", ".ppt": "ppt", ".docx": "docx",
    }.get(ext, "text")


# ── Workspace upload endpoint ──────────────────────────────────────────

WORKSPACE_MAX_SIZE = 50 * 1024 * 1024  # 50 MB per file


@router.post("/workspace/upload")
async def upload_to_workspace(
    file: UploadFile = File(...),
    subdir: str = "",
    session_id: str = "",
    user: dict = Depends(get_current_user),
) -> dict:
    """Upload a file into the user's per-session workspace.

    Writes the file to the session-scoped workspace so it's immediately
    visible in the file tree and available for agent operations within
    that session.
    """
    safe_name = Path(file.filename or "untitled").name
    if not safe_name or ".." in safe_name or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Resolve target directory within the user+session workspace
    sid = session_id or "default"
    user_root = _get_session_workspace_root(user["id"], sid)

    base = user_root
    if subdir:
        target_dir = _safe_session_workspace_path(user["id"], sid, subdir)
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


# ── Workspace delete endpoint ──────────────────────────────────────────

class DeleteWorkspaceItemRequest(BaseModel):
    path: str
    session_id: str = ""


@router.delete("/workspace/item")
async def delete_workspace_item(
    body: DeleteWorkspaceItemRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Delete a file or directory from the user's per-session workspace.

    Directories are removed recursively.  The *path* must be relative to
    the workspace root; traversal attempts are blocked.
    """
    sid = body.session_id or "default"
    safe = _safe_session_workspace_path(user["id"], sid, body.path)
    if safe is None:
        raise HTTPException(status_code=400, detail=f"Path '{body.path}' is outside workspace")

    if not await aexists(safe):
        raise HTTPException(status_code=404, detail=f"Not found: {body.path}")

    try:
        if safe.is_dir():
            await armtree(safe)
            return {"success": True, "path": body.path, "type": "directory", "message": f"Deleted directory: {body.path}"}
        else:
            await aunlink(safe)
            return {"success": True, "path": body.path, "type": "file", "message": f"Deleted file: {body.path}"}
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Delete failed: {exc}")
