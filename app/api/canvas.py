from __future__ import annotations

import base64
import json
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.utils.async_file import aread_text, awrite_bytes, awrite_text, aexists

router = APIRouter(prefix="/api/canvas", tags=["canvas"])

CANVAS_DIR = Path("data/canvases")
EXPORT_DIR = Path("data/exports")
DEFAULT_CANVAS_ID = "default"


def _safe_canvas_id(canvas_id: str) -> str:
    value = canvas_id.strip() or DEFAULT_CANVAS_ID
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise HTTPException(status_code=400, detail="Invalid canvas id")
    return value


def _canvas_path(canvas_id: str) -> Path:
    return CANVAS_DIR / f"{_safe_canvas_id(canvas_id)}.json"


class CanvasSaveRequest(BaseModel):
    id: str = DEFAULT_CANVAS_ID
    name: str = "Agent Workflow Canvas"
    data: dict[str, Any] = Field(default_factory=dict)


class CanvasExportRequest(BaseModel):
    id: str = DEFAULT_CANVAS_ID
    image: str | None = None
    data: dict[str, Any] | None = None


@router.get("/{canvas_id}")
async def get_canvas(canvas_id: str = DEFAULT_CANVAS_ID) -> dict[str, Any]:
    path = _canvas_path(canvas_id)
    if not await aexists(path):
        return {
            "id": _safe_canvas_id(canvas_id),
            "name": "Agent Workflow Canvas",
            "data": None,
        }
    return json.loads(await aread_text(path))


@router.post("/save")
async def save_canvas(payload: CanvasSaveRequest) -> dict[str, Any]:
    canvas_id = _safe_canvas_id(payload.id)
    CANVAS_DIR.mkdir(parents=True, exist_ok=True)
    document = {
        "id": canvas_id,
        "name": payload.name.strip() or "Agent Workflow Canvas",
        "data": payload.data,
    }
    await awrite_text(_canvas_path(canvas_id), json.dumps(document, ensure_ascii=False, indent=2))
    return {"status": "success", "id": canvas_id}


@router.post("/export")
async def export_canvas(payload: CanvasExportRequest) -> dict[str, str]:
    canvas_id = _safe_canvas_id(payload.id)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    if payload.image and payload.image.startswith("data:image/png;base64,"):
        image_data = payload.image.split(",", 1)[1]
        try:
            content = base64.b64decode(image_data)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid image data") from exc
        filename = f"{canvas_id}-{uuid.uuid4().hex[:8]}.png"
        path = EXPORT_DIR / filename
        await awrite_bytes(path, content)
        return {"status": "success", "url": f"/api/canvas/exports/{filename}"}

    filename = f"{canvas_id}-{uuid.uuid4().hex[:8]}.json"
    path = EXPORT_DIR / filename
    await awrite_text(path, json.dumps(payload.data or {}, ensure_ascii=False, indent=2))
    return {"status": "success", "url": f"/api/canvas/exports/{filename}"}


@router.get("/exports/{filename}")
async def get_export(filename: str) -> FileResponse:
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = EXPORT_DIR / filename
    if not await aexists(path):
        raise HTTPException(status_code=404, detail="Export not found")
    return FileResponse(path)

