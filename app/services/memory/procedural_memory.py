from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from app.services.memory.models import CognitiveMemoryType, MemoryScope
from app.services.memory.storage import MemoryStorage
from app.services.tool_registry import ToolDefinition, tool_registry


@dataclass(frozen=True)
class ProceduralMemoryRecord:
    id: str
    kind: str
    name: str
    description: str
    source: str
    source_version: str
    content_hash: str
    scope: str
    risk_level: str = ""
    memory_type: str = CognitiveMemoryType.PROCEDURAL.value

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ProceduralMemoryCatalog:
    """Read-through catalog over existing procedural sources of truth."""

    def __init__(self, user_id: str, memory_storage: MemoryStorage) -> None:
        self._user_id = user_id
        self._memory_storage = memory_storage

    async def list_records(self) -> list[ProceduralMemoryRecord]:
        groups = await self._load_groups()
        records = [record for group in groups for record in group]
        unique = {record.id: record for record in records}
        return sorted(unique.values(), key=lambda record: (record.kind, record.name.lower()))

    async def search(self, query: str, limit: int = 8) -> list[ProceduralMemoryRecord]:
        records = await self.list_records()
        terms = _terms(query)
        scored: list[tuple[int, ProceduralMemoryRecord]] = []
        for record in records:
            haystack = _terms(f"{record.kind} {record.name} {record.description}")
            score = len(terms & haystack)
            if score or not terms:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].kind, item[1].name), reverse=True)
        return [record for _, record in scored[:limit]]

    async def _load_groups(self) -> list[list[ProceduralMemoryRecord]]:
        async def safe(loader) -> list[ProceduralMemoryRecord]:
            try:
                return await loader()
            except Exception:
                return []

        return [
            await safe(self._skills),
            await safe(self._tools),
            await safe(self._dag_templates),
            await safe(self._agent_routes),
            await safe(self._sops),
            await safe(self._tool_policies),
        ]

    async def _skills(self) -> list[ProceduralMemoryRecord]:
        from app.services.tools.skill_tools import skill_list_handler

        response = await skill_list_handler("all")
        skills = (response.get("result") or {}).get("skills") if response.get("success") else []
        return [
            _record(
                "skill",
                str(item.get("name") or "unknown"),
                str(item.get("description") or ""),
                f"skill:{item.get('source', 'unknown')}:{item.get('path', '')}",
                str(item.get("version") or "1"),
                MemoryScope.USER.value if item.get("source") == "user" else MemoryScope.TEAM.value,
                json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
            )
            for item in (skills or [])
        ]

    async def _tools(self) -> list[ProceduralMemoryRecord]:
        tools = tool_registry.list_all()
        if not tools:
            from app.services.tools.definitions import BUILTIN_TOOLS

            tools = BUILTIN_TOOLS
        return records_from_tools(tools)

    async def _dag_templates(self) -> list[ProceduralMemoryRecord]:
        from app.services.template_engine import template_engine

        return [
            _record(
                "dag-template", str(item["name"]), str(item.get("category") or ""),
                f"dag-template:{item['id']}", "1", MemoryScope.GLOBAL.value,
                json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
            )
            for item in await template_engine.list_templates()
        ]

    async def _agent_routes(self) -> list[ProceduralMemoryRecord]:
        from app.services.agent_route_service import agent_route_service

        routes = await agent_route_service.list_routes(self._user_id)
        return [
            _record(
                "agent-route", str(item["name"]), str(item.get("description") or ""),
                f"agent-route:{item['id']}", str(item.get("updated_at") or "1"),
                MemoryScope.USER.value,
                json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
            )
            for item in routes
        ]

    async def _sops(self) -> list[ProceduralMemoryRecord]:
        headers = await self._memory_storage.list_headers()
        return [
            _record(
                "sop", header.name or header.filename, header.description,
                f"memory-file:{header.filename}", str(header.version), header.scope.value,
                f"{header.path}|{header.mtime}|{header.version}",
            )
            for header in headers
            if header.memory_type == CognitiveMemoryType.PROCEDURAL
        ]

    async def _tool_policies(self) -> list[ProceduralMemoryRecord]:
        from app.db.session import afetch_all

        rows = await afetch_all(
            "SELECT id,agent_id,tool_pattern,path_pattern,behavior,source,priority,enabled "
            "FROM tool_permission_rules WHERE enabled=1 ORDER BY priority DESC"
        )
        return [
            _record(
                "tool-policy",
                f"{row.get('agent_id', '*')}:{row.get('tool_pattern', '*')}",
                f"{row.get('behavior', 'ask')} path={row.get('path_pattern', '*')}",
                f"tool-policy:{row['id']}", str(row.get("priority", 0)),
                MemoryScope.TENANT.value,
                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
            )
            for row in rows
        ]


def records_from_tools(tools: Iterable[ToolDefinition]) -> list[ProceduralMemoryRecord]:
    return [
        _record(
            "tool", tool.name, tool.description, f"tool-registry:{tool.name}", "1",
            MemoryScope.GLOBAL.value, json.dumps(tool.to_dict(), ensure_ascii=False, sort_keys=True),
            risk_level=tool.risk_level,
        )
        for tool in tools
    ]


def _record(
    kind: str,
    name: str,
    description: str,
    source: str,
    version: str,
    scope: str,
    fingerprint: str,
    *,
    risk_level: str = "",
) -> ProceduralMemoryRecord:
    content_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
    record_id = hashlib.sha256(f"{kind}|{source}".encode("utf-8")).hexdigest()[:24]
    return ProceduralMemoryRecord(
        id=record_id,
        kind=kind,
        name=name,
        description=description[:500],
        source=source,
        source_version=version,
        content_hash=content_hash,
        scope=scope,
        risk_level=risk_level,
    )


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    latin = set(re.findall(r"[a-z0-9_-]{2,}", lowered))
    cjk_text = "".join(re.findall(r"[\u3400-\u9fff]", lowered))
    cjk = {cjk_text[index:index + 2] for index in range(max(0, len(cjk_text) - 1))}
    return latin | cjk
