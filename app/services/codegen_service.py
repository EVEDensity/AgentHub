from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from fastapi import HTTPException

from app.config import PROJECT_ROOT
from app.services.git_service import git_service

GENERATED_DIR = PROJECT_ROOT / "agenthub_generated"
GENERATED_DIR.mkdir(exist_ok=True)


def _safe_rel_path(name: str) -> Path:
    raw = name.replace("\\", "/").strip().strip("'\"")
    raw = re.sub(r"[^a-zA-Z0-9_./-]+", "_", raw).strip("./")
    rel = Path(raw or "generated_api.py")
    if rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(status_code=400, detail="Invalid generated file path")
    return rel


def infer_filename(original: str) -> str:
    explicit = re.search(r"(?:保存为|文件名|命名为|save as)\s*[:：]?\s*([\w./\\-]+)", original, re.I)
    if explicit:
        return explicit.group(1)
    lower = original.lower()
    if "fastapi" in lower or "api" in lower or "后端" in original or "路由" in original:
        return "backend/health_router.py"
    if "react" in lower or "next" in lower or "前端" in original or "页面" in original:
        return "frontend/GeneratedPanel.jsx"
    return "backend/generated_api.py"


def language_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".json": "json", ".md": "markdown"}.get(suffix, "text")


def default_content(path: str, original: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "from fastapi import APIRouter\n\nrouter = APIRouter(prefix=\"/generated\", tags=[\"generated\"])\n\n\n@router.get(\"/health\")\nasync def generated_health() -> dict[str, str]:\n    return {\"status\": \"ok\", \"module\": \"agenthub-generated\"}\n"
    if suffix in {".jsx", ".tsx", ".js", ".ts"}:
        return "export default function GeneratedPanel() {\n  return (\n    <section className=\"rounded-2xl border bg-white p-6 shadow-sm\">\n      <h2 className=\"text-xl font-semibold\">AgentHub Generated Panel</h2>\n      <p className=\"mt-2 text-slate-500\">由 CodeGenAgent 根据任务生成，可在确认后提交到 Git。</p>\n    </section>\n  );\n}\n"
    return f"AgentHub generated file\n\nTask:\n{original}\n"


def strip_code_fence(text: str) -> str:
    match = re.search(r"```(?:\w+)?\s*([\s\S]*?)```", text)
    return match.group(1).strip() if match else text.strip()


def _try_parse_json_with_files(text: str) -> dict | None:
    """Parse JSON with a 'files' key, tolerating non-JSON prefixes/suffixes."""
    # Direct parse first
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("files"), list):
            return data
    except json.JSONDecodeError:
        pass
    # Try to locate the JSON object containing "files"
    for m in re.finditer(r"\{[^{}]*\"files\"\s*:\s*\[[^\]]*\][^{}]*\}", text):
        try:
            data = json.loads(m.group())
            if isinstance(data, dict) and isinstance(data.get("files"), list):
                return data
        except json.JSONDecodeError:
            continue
    return None


def extract_files(model_output: str, original: str) -> list[dict]:
    # Strip decorative prefixes so JSON parsing can succeed
    cleaned = re.sub(r"^(【[^】]*】\s*)+", "", model_output.strip())
    data = _try_parse_json_with_files(cleaned)
    if data:
        files = []
        for item in data["files"]:
            rel = _safe_rel_path(str(item.get("path") or infer_filename(original)))
            content = str(item.get("content") or default_content(str(rel), original))
            files.append({"path": str(rel), "content": strip_code_fence(content), "language": language_for(str(rel))})
        if files:
            return files
    # Fallback: treat entire output as single file content, but only if it
    # looks like actual code — don't create bogus files from chat replies.
    rel = _safe_rel_path(infer_filename(original))
    content = strip_code_fence(model_output)
    if not content or content.startswith("本地 Mock 模型响应") or content.startswith("模型调用失败"):
        content = default_content(str(rel), original)
    # Only create a file if the content resembles source code
    is_code = bool(re.search(r"\b(def |class |import |from |function |const |export |require\(|package |use )", content))
    if not is_code:
        return []
    return [{"path": str(rel), "content": content, "language": language_for(str(rel))}]


async def write_generated_files(model_output: str, original: str) -> dict:
    files = extract_files(model_output, original)
    public_files: list[dict] = []

    async def _write_one(item: dict) -> dict:
        rel = _safe_rel_path(item["path"])
        target = GENERATED_DIR / rel

        def _sync():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")

        await asyncio.to_thread(_sync)
        return {"path": str(target.relative_to(PROJECT_ROOT)), "content": item["content"], "language": item["language"]}

    for item in files:
        public_files.append(await _write_one(item))
    return {"files": [file["path"] for file in public_files], "fileDetails": public_files, "diff": git_service.diff().get("diff", "")}
