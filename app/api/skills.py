from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/skills", tags=["skills"])

logger = logging.getLogger("agenthub.skills")

# ── YAML frontmatter parser ──────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract YAML-like frontmatter from a markdown string.

    Handles: simple key:value, multi-line indented values, pipe-style (|),
    folded-style (>), list items (-), nested object keys.
    No PyYAML dependency required.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}

    raw = m.group(1)
    result: dict[str, Any] = {"_raw_frontmatter": raw}
    current_key: Optional[str] = None
    current_value: list[str] = []
    current_style: Optional[str] = None  # 'indent' | 'pipe' | 'fold' | 'list'

    def _flush() -> None:
        nonlocal current_key, current_value, current_style
        if current_key is None:
            return
        val = "\n".join(current_value).strip()
        if current_style == "list":
            # Accumulate as list
            items = [v.lstrip("- ") for v in current_value if v.strip()]
            if current_key in result and isinstance(result[current_key], list):
                result[current_key].extend(items)
            else:
                result[current_key] = items
        else:
            full = " ".join(v.strip() for v in current_value if v.strip())
            # For pipe/fold, preserve newlines
            if current_style in ("pipe", "fold"):
                full = val
            # Accumulate multi-line lists (e.g. authors:)
            if current_key in result:
                if isinstance(result[current_key], list):
                    result[current_key].append(full)
                else:
                    result[current_key] = [result[current_key], full]
            else:
                result[current_key] = full
        current_key = None
        current_value = []
        current_style = None

    for line in raw.split("\n"):
        # Skip comments and empty lines (except inside multi-line values)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if current_key and current_style == "pipe":
                current_value.append(line)  # preserve blank lines inside |
                continue
            _flush()
            continue

        # Multi-line continuation (indented under a key)
        if current_key and (line.startswith("  ") or line.startswith("\t")):
            if current_style is None:
                # Detect list items: lines starting with "- " after indentation
                current_style = "list" if stripped.startswith("- ") else "indent"
            current_value.append(line.strip())
            continue

        # List item continuation (for authors, tags, credentials)
        if current_key and current_style == "list" and stripped.startswith("- "):
            current_value.append(stripped)
            continue

        # Check if this is a list item start
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip().lower()
            val = val.strip()

            if not val:  # empty value — could be start of list or multi-line
                _flush()
                current_key = key
                current_value = []
                current_style = None  # will be determined by next line
                continue

            if val == "|":
                _flush()
                current_key = key
                current_value = []
                current_style = "pipe"
                continue

            if val == ">":
                _flush()
                current_key = key
                current_value = []
                current_style = "fold"
                continue

            # Could be start of inline list like: authors:
            #   - name1
            _flush()
            current_key = key
            current_value = [val]
            current_style = None
            continue

        # Line without colon — could be list item continuation for previous key
        if current_key and current_style in (None, "list") and stripped.startswith("- "):
            current_style = "list"
            current_value.append(stripped)
            continue

        # Fallback: append to current multi-line value
        if current_key:
            current_value.append(stripped)

    _flush()

    # Normalize: collapse single-element lists back to strings where appropriate
    for k in list(result.keys()):
        if k.startswith("_"):
            continue
        v = result[k]
        if isinstance(v, list) and len(v) == 1 and k not in ("authors", "credentials", "tags"):
            result[k] = v[0]

    return result


def _extract_body(text: str) -> str:
    """Return everything after the frontmatter block."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text
    return text[m.end():].strip()


# ── Skill category tree ──────────────────────────────────────────────
# Primary categories (一级分类) → list of (subcategory, keywords) tuples.

_CATEGORY_TREE: dict[str, list[tuple[str, list[str]]]] = {
    "工具集成": [
        ("API/HTTP 调用", ["api", "http", "rest", "graphql", "fetch", "request", "curl",
                           "endpoint", "webhook", "openapi", "swagger", "post", "get",
                           "openapi-explorer", "integration", "connector", "adapter"]),
        ("数据库操作", ["database", "sql", "db", "mongo", "redis", "mysql", "postgres",
                        "orm", "query", "storage", "indexdb", "datastore", "base"]),
        ("浏览器自动化", ["browser", "puppeteer", "playwright", "selenium", "crawl",
                          "scrape", "dom", "headless", "page", "automation"]),
        ("本地命令/脚本", ["shell", "bash", "script", "exec", "run", "cli", "command",
                           "terminal", "process", "spawn", "execute", "subprocess"]),
        ("云服务/SDK", ["cloud", "aws", "s3", "lambda", "azure", "gcp", "sdk", "serverless",
                        "service", "docker", "kubernetes", "k8s"]),
    ],
    "决策规划": [
        ("任务拆解", ["task", "plan", "breakdown", "decompose", "todo", "step", "pipeline",
                      "checklist", "goal", "objective", "okr"]),
        ("流程编排", ["orchestrate", "flow", "chain", "compose", "coordinate", "schedule",
                      "dag", "router", "dispatch", "agent", "workflow", "multi-agent"]),
        ("多轮推理", ["reason", "think", "chain-of-thought", "deduce", "infer", "logic",
                      "analysis", "cot", "deep", "reasoning", "deliberate"]),
        ("路由/分支判断", ["route", "branch", "condition", "switch", "if-else", "dispatch",
                           "classify", "triage", "intent"]),
        ("反思与纠错", ["reflect", "review", "correct", "error", "validate", "verify",
                        "check", "fix", "self-correct", "audit", "proofread"]),
    ],
    "交互感知": [
        ("自然语言对话", ["chat", "conversation", "message", "reply", "assistant", "dialogue",
                          "nlp", "language", "talk", "interact", "prompt", "im", "instant"]),
        ("文件解析", ["file", "parse", "pdf", "word", "excel", "document", "csv", "read",
                      "extract", "text", "markdown", "md", "drive", "attachment"]),
        ("图像识别/OCR", ["image", "ocr", "vision", "photo", "picture", "recognize",
                          "screenshot", "scan", "detect"]),
        ("语音识别/合成", ["voice", "speech", "audio", "tts", "stt", "sound", "transcribe",
                           "speak", "listen"]),
    ],
    "数据处理": [
        ("结构化数据", ["structured", "sql", "csv", "excel", "table", "json", "xml",
                        "schema", "column", "row", "dataset", "sheet", "spreadsheet",
                        "grid", "record", "field"]),
        ("数据清洗转换", ["clean", "transform", "etl", "format", "normalize", "convert",
                          "map", "filter", "sanitize"]),
        ("数据可视化", ["visualize", "chart", "graph", "plot", "dashboard", "heatmap",
                        "diagram", "report", "statistic"]),
        ("文档摘要/提取", ["summary", "extract", "summarize", "digest", "abstract", "tldr",
                          "key", "highlight", "overview", "search", "research"]),
    ],
    "开发工程": [
        ("代码生成/补全", ["code", "generate", "complete", "suggest", "autocomplete",
                           "copilot", "snippet", "template", "scaffold",
                           "design", "ui", "ux", "html", "css", "svg", "react",
                           "vue", "component", "frontend", "prototype",
                           "shipfast", "scaffold"]),
        ("代码调试/测试", ["debug", "test", "lint", "unit", "jest", "pytest", "mocha",
                           "spec", "assert", "coverage"]),
        ("Git 操作", ["git", "commit", "branch", "merge", "pull-request",
                      "push", "repo", "diff", "rebase", "clone", "version-control"]),
        ("容器/部署", ["container", "deploy", "docker", "kubernetes", "k8s", "ci",
                       "cd", "build", "release", "artifact", "devops", "shipfast"]),
    ],
    "业务场景": [
        ("内容创作", ["content", "write", "blog", "article", "draft", "copy", "create",
                      "edit", "story", "writing", "创作", "写作", "文案", "doc",
                      "slide", "presentation", "wiki", "knowledge"]),
        ("办公自动化", ["office", "email", "calendar", "schedule", "report", "automation",
                        "productivity", "邮件", "日程", "报表", "办公", "approval",
                        "attendance", "contact", "mail", "form", "application",
                        "leave", "reimbursement", "check-in"]),
        ("会议与协作", ["meeting", "collaborate", "note", "minutes", "team", "share",
                        "sync", "communicate", "conference", "whiteboard", "board",
                        "canvas", "vc", "video", "call", "standup"]),
        ("行业专项", ["finance", "legal", "law", "education", "medical", "health",
                      "industry", "domain", "compliance", "regulatory", "hr", "人事",
                      "payroll", "recruitment"]),
    ],
}

# Flattened: subcategory → primary category lookup
_SUB_TO_PRIMARY: dict[str, str] = {}
for _pri, _subs in _CATEGORY_TREE.items():
    for _sub_name, _keywords in _subs:
        _SUB_TO_PRIMARY[_sub_name] = _pri


def _classify_skill(meta: dict[str, Any], body: str, skill_name: str) -> tuple[str, str]:
    """Classify a skill into (primary_category, subcategory).

    Strategy (in priority order):
    1. Frontmatter ``category`` field — if it matches a known primary category,
       use it directly (with keyword matching for the subcategory).
    2. Frontmatter ``tags`` field — if any tag exactly matches a known
       subcategory name, use that subcategory and its primary.
    3. Keyword matching against skill name, description, and body headings.
    4. Fallback: ("其他", "未分类").
    """
    # ── Step 1: frontmatter category ─────────────────────────────────
    fm_category = meta.get("category")
    if isinstance(fm_category, str) and fm_category in _CATEGORY_TREE:
        # Frontmatter declared a valid primary category — use it.
        # Still need to find the best subcategory within it.
        primary = fm_category
        subcats = _CATEGORY_TREE[primary]

        # Build search text for subcategory matching
        search_text = " ".join([
            skill_name,
            str(meta.get("description", "")),
            str(meta.get("name", "")),
        ] + re.findall(r"^#{1,3}\s+(.+)", body, re.MULTILINE)[:10]).lower()
        name_lower = skill_name.lower()

        best_sub = ""
        best_score = 0
        for sub_name, keywords in subcats:
            score = 0
            for kw in keywords:
                if kw in search_text:
                    score += 1 + search_text.count(kw)
                if kw in name_lower:
                    score += 3
            if score > best_score:
                best_score = score
                best_sub = sub_name

        if best_sub:
            return (primary, best_sub)
        # Frontmatter category valid but no subcategory matched — use first sub
        return (primary, subcats[0][0])

    # ── Step 2: frontmatter tags ────────────────────────────────────
    fm_tags = meta.get("tags", [])
    if isinstance(fm_tags, list):
        for tag in fm_tags:
            tag_str = str(tag)
            if tag_str in _SUB_TO_PRIMARY:
                return (_SUB_TO_PRIMARY[tag_str], tag_str)

    # ── Step 3: keyword matching (full scan) ─────────────────────────
    search_parts: list[str] = [
        skill_name,
        str(meta.get("description", "")),
        str(meta.get("name", "")),
    ]
    if isinstance(fm_category, str):
        search_parts.append(fm_category)
    if isinstance(fm_tags, list):
        search_parts.extend(str(t) for t in fm_tags)

    headings = re.findall(r"^#{1,3}\s+(.+)", body, re.MULTILINE)
    search_parts.extend(headings[:10])

    text = " ".join(search_parts).lower()
    name_lower = skill_name.lower()

    # Score each subcategory — name matches get 3x weight
    scores: dict[str, int] = {}
    for primary, subcats in _CATEGORY_TREE.items():
        for sub_name, keywords in subcats:
            score = 0
            for kw in keywords:
                if kw in text:
                    score += 1 + text.count(kw)
                if kw in name_lower:
                    score += 3  # name match is a strong signal
            if score > 0:
                scores[sub_name] = score

    if not scores:
        return ("其他", "未分类")

    # Best matching subcategory
    best_sub = max(scores, key=lambda k: scores[k])  # type: ignore[arg-type]
    best_primary = _SUB_TO_PRIMARY.get(best_sub, "其他")
    return (best_primary, best_sub)


def _derive_category(meta: dict[str, Any], body: str, skill_name: str) -> str:
    """Return the primary category label (for backward compatibility)."""
    primary, _sub = _classify_skill(meta, body, skill_name)
    return primary


# ── Skill scanner ────────────────────────────────────────────────────


def _get_skill_dirs() -> list[tuple[str, Path]]:
    """Return list of (source, path) tuples for all skill directories.

    Sources: 'user' (~/.claude/skills/) and 'project' (.claude/skills/).
    Symlinks are followed to support global skill registries.
    """
    results: list[tuple[str, Path]] = []

    # User-level skills
    user_dir = Path.home() / ".claude" / "skills"
    if user_dir.is_dir():
        results.append(("user", user_dir))

    # Project-level skills — walk up from cwd
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        project_dir = parent / ".claude" / "skills"
        if project_dir.is_dir():
            results.append(("project", project_dir))
            break

    # Also check AGENTHUB_PROJECT_DIR env var
    proj_from_env = os.environ.get("AGENTHUB_PROJECT_DIR", "")
    if proj_from_env:
        env_dir = Path(proj_from_env) / ".claude" / "skills"
        if env_dir.is_dir() and env_dir not in [p for _, p in results]:
            results.append(("project", env_dir))

    return results


def scan_skills(source_filter: Optional[str] = None) -> list[dict[str, Any]]:
    """Scan local skill directories and return rich metadata for all skills.

    Each skill dict contains:
      name, display_name, description, version, source, category,
      icon, enabled, credentials, authors, tags,
      content_length, body_lines, file_count, path
    """
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source, base_dir in _get_skill_dirs():
        if source_filter and source != source_filter:
            continue

        if not base_dir.is_dir():
            continue

        for entry in sorted(base_dir.iterdir()):
            # Follow symlinks for global skill registries
            try:
                target = entry.resolve() if entry.is_symlink() else entry
            except OSError:
                target = entry

            if not target.is_dir():
                continue

            skill_name = entry.name
            key = f"{source}/{skill_name}"
            if key in seen:
                continue
            seen.add(key)

            # Look for SKILL.md (case-insensitive)
            actual_file = None
            for candidate in ("SKILL.md", "skill.md"):
                fp = target / candidate
                if fp.exists():
                    actual_file = fp
                    break

            if not actual_file:
                continue

            try:
                raw = actual_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                logger.debug("failed to read skill file: %s", actual_file)
                continue

            meta = _parse_frontmatter(raw)
            body = _extract_body(raw)

            body_lines = [l for l in body.split("\n") if l.strip()]
            content_length = len(raw)

            try:
                file_count = sum(1 for e in target.iterdir() if e.is_file())
            except OSError:
                file_count = 0

            # Extract tags from body sections
            section_headings = re.findall(r"^##\s+(.+)", body, re.MULTILINE)
            derived_tags = [h.strip() for h in section_headings[:6]]

            # Build credential list for "入参/出参" display
            creds = meta.get("credentials", [])
            if isinstance(creds, str):
                creds = [{"name": creds}]

            primary, sub = _classify_skill(meta, body, skill_name)

            skill_info: dict[str, Any] = {
                "name": skill_name,
                "display_name": meta.get("name", skill_name),
                "description": meta.get("description", ""),
                "version": meta.get("version", ""),
                "source": source,
                "category": primary,
                "subcategory": sub,
                "icon": meta.get("icon", ""),
                "enabled": True,  # skill exists and SKILL.md is readable
                "credentials": creds,
                "authors": meta.get("authors", []),
                "tags": derived_tags,
                "content_length": content_length,
                "body_lines": len(body_lines),
                "has_skill_md": True,
                "file_count": file_count,
                "path": str(entry),
            }

            skills.append(skill_info)

    return skills


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("")
@router.get("/")
async def list_skills(
    source: Optional[str] = Query(None, description="Filter by source: user, project"),
    refresh: bool = Query(False, description="Force re-scan of local skill directories"),
):
    """List all locally installed skills with full metadata."""
    skills = scan_skills(source_filter=source)
    total_tokens = sum(s.get("content_length", 0) // 4 for s in skills)
    return {
        "skills": skills,
        "total": len(skills),
        "total_tokens_estimate": total_tokens,
        "sources": list({s["source"] for s in skills}),
        "categories": sorted(
            {s.get("category", "其他") for s in skills},
            key=lambda c: (c == "其他", c),
        ),
        "_refresh_hint": refresh,
    }


@router.get("/{skill_name}")
async def get_skill_detail(
    skill_name: str,
    source: Optional[str] = Query(None, description="Source to look in: user, project"),
):
    """Get the full SKILL.md content for a specific skill."""
    skill_dirs = _get_skill_dirs()
    if source:
        skill_dirs = [(s, p) for s, p in skill_dirs if s == source]

    for src, base_dir in skill_dirs:
        skill_dir = base_dir / skill_name
        if not skill_dir.is_dir():
            # Also check if it's a symlink
            try:
                resolved = skill_dir.resolve() if skill_dir.is_symlink() else skill_dir
            except OSError:
                resolved = skill_dir
            if not resolved.is_dir():
                continue
            skill_dir = resolved

        for filename in ("SKILL.md", "skill.md"):
            skill_file = skill_dir / filename
            if skill_file.exists():
                try:
                    raw = skill_file.read_text(encoding="utf-8")
                    meta = _parse_frontmatter(raw)
                    body = _extract_body(raw)
                    primary, sub = _classify_skill(meta, body, skill_name)
                    return {
                        "name": skill_name,
                        "source": src,
                        "category": primary,
                        "subcategory": sub,
                        "meta": {k: v for k, v in meta.items() if not k.startswith("_")},
                        "body": body,
                        "raw": raw,
                        "path": str(skill_file),
                    }
                except (OSError, UnicodeDecodeError) as exc:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to read skill file: {exc}",
                    )

    raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")


@router.get("/{skill_name}/raw")
async def get_skill_raw(
    skill_name: str,
    source: Optional[str] = Query(None, description="Source to look in: user, project"),
):
    """Get raw SKILL.md content for download/export."""
    from fastapi.responses import PlainTextResponse

    skill_dirs = _get_skill_dirs()
    if source:
        skill_dirs = [(s, p) for s, p in skill_dirs if s == source]

    for src, base_dir in skill_dirs:
        skill_dir = base_dir / skill_name
        if not skill_dir.is_dir():
            try:
                resolved = skill_dir.resolve() if skill_dir.is_symlink() else skill_dir
            except OSError:
                resolved = skill_dir
            if not resolved.is_dir():
                continue
            skill_dir = resolved

        for filename in ("SKILL.md", "skill.md"):
            skill_file = skill_dir / filename
            if skill_file.exists():
                try:
                    raw = skill_file.read_text(encoding="utf-8")
                    return PlainTextResponse(
                        content=raw,
                        media_type="text/markdown; charset=utf-8",
                        headers={
                            "Content-Disposition": (
                                f"attachment; filename*=UTF-8''{skill_name}_SKILL.md"
                            ),
                        },
                    )
                except OSError as exc:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to read skill file: {exc}",
                    )

    raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
