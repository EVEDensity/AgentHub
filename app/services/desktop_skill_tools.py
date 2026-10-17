"""Compatibility workspace skills and explicit reviewed-catalog selection.

The workspace scanner remains a legacy read-only adapter. Reviewed mode reads
only the already frozen operator catalog and never merges either directory.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.services.harness_service import FunctionTool

SKILL_LIST_TOOL_NAME = "skill_list"
SKILL_LOAD_TOOL_NAME = "skill_load"


def _workspace_skills_dir(workspace_root: Path) -> Path:
    return workspace_root / ".claude" / "skills"


async def _list_workspace_skills(workspace_root: Path) -> list[dict[str, Any]]:
    from app.services.tools.skill_tools import _parse_skill_md

    skills_dir = _workspace_skills_dir(workspace_root)
    entries: list[dict[str, Any]] = []
    if not skills_dir.is_dir():
        return entries
    try:
        children = sorted(skills_dir.iterdir())
    except OSError:
        return entries
    for entry in children:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        parsed = await _parse_skill_md(entry)
        if parsed is None:
            continue
        entries.append(
            {
                "name": str(parsed.get("name") or entry.name),
                "description": str(parsed.get("description") or ""),
                "version": str(parsed.get("version") or ""),
                "scripts": list(parsed.get("scripts") or []),
            }
        )
    return entries


def _validate_skill_list_arguments(
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(arguments, Mapping):
        raise TypeError("tool arguments must be an object")
    return {}


def _validate_skill_load_arguments(
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(arguments, Mapping):
        raise TypeError("tool arguments must be an object")
    name = arguments.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    cleaned = name.strip()
    # Skill names are directory names; refuse traversal-shaped input.
    if "/" in cleaned or "\\" in cleaned or cleaned in (".", ".."):
        raise ValueError("name must be a plain skill directory name")
    return {"name": cleaned}


def build_workspace_skill_tools(
    workspace_root: Path,
    max_result_chars: int,
    *, render: Callable[[object, int], str],
) -> list[FunctionTool]:
    async def list_execute(_arguments: Mapping[str, Any]) -> str:
        skills = await _list_workspace_skills(workspace_root)
        outcome = {
            "success": True,
            "result": {
                "skills": skills,
                "total": len(skills),
                "hint": (
                    "用 skill_load 加载某技能的完整文档后再遵循其指引"
                    if skills
                    else "工作区 .claude/skills/ 下没有技能包"
                ),
            },
        }
        return render(outcome, max_result_chars)

    async def load_execute(arguments: Mapping[str, Any]) -> str:
        from app.services.tools.skill_tools import _parse_skill_md

        name = str(arguments.get("name") or "")
        skill_dir = _workspace_skills_dir(workspace_root) / name
        if not skill_dir.is_dir():
            return f"工具执行失败: 未找到技能 '{name}'（可用 skill_list 查看）"
        parsed = await _parse_skill_md(skill_dir)
        if parsed is None:
            return f"工具执行失败: 技能 '{name}' 缺少可解析的 SKILL.md"
        outcome = {
            "success": True,
            "result": {
                "name": parsed.get("name"),
                "description": parsed.get("description"),
                "version": parsed.get("version"),
                "body": parsed.get("body"),
                "scripts": parsed.get("scripts"),
            },
        }
        return render(outcome, max_result_chars)

    return [
        FunctionTool(
            name=SKILL_LIST_TOOL_NAME,
            description=(
                "列出工作区 .claude/skills/ 下的技能包（名称/描述/版本/脚本）。"
                "技能是可复用的工作流说明，先列出再按需加载。"
            ),
            parameters={"type": "object", "properties": {}},
            validate_arguments=_validate_skill_list_arguments,
            handler=list_execute,
        ),
        FunctionTool(
            name=SKILL_LOAD_TOOL_NAME,
            description=(
                "加载指定技能的完整文档（SKILL.md 正文与脚本清单），"
                "加载后严格遵循该技能的流程指引。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "技能目录名（见 skill_list 结果）",
                    },
                },
                "required": ["name"],
            },
            validate_arguments=_validate_skill_load_arguments,
            handler=load_execute,
        ),
    ]


def build_skill_tools(workspace_root, max_result_chars, *, render,
                      reviewed_catalog=None, available_capabilities=frozenset()):
    if reviewed_catalog is None:
        return build_workspace_skill_tools(workspace_root, max_result_chars, render=render)
    from app.services.reviewed_skills.tools import build_reviewed_skill_tools

    return list(build_reviewed_skill_tools(reviewed_catalog, max_result_chars=max_result_chars,
                                         available_capabilities=available_capabilities))
