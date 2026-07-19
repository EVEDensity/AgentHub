from __future__ import annotations

import json
from typing import Any

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute, aexecute_insert
from app.schemas.dag import DAGConfig
from app.services.template_engine import template_engine
from app.services.context_summary_cache import context_summary_cache


class AgentRouteService:
    async def list_routes(self, user_id: str, active_only: bool = False) -> list[dict[str, Any]]:
        if active_only:
            cached = context_summary_cache.get("route", user_id, "active-routes")
            if cached is not None:
                return json.loads(cached)
        if active_only:
            sql = "SELECT id,name,description,trigger_keywords,nodes_json,is_default,active,created_at,updated_at FROM agent_routes WHERE user_id=$1 AND active=1 ORDER BY is_default DESC,id DESC"
            rows = await afetch_all(sql, user_id)
        else:
            sql = "SELECT id,name,description,trigger_keywords,nodes_json,is_default,active,created_at,updated_at FROM agent_routes WHERE user_id=$1 ORDER BY is_default DESC,id DESC"
            rows = await afetch_all(sql, user_id)
        for row in rows:
            row["triggerKeywords"] = json.loads(row.pop("trigger_keywords") or "[]")
            row["nodes"] = json.loads(row.pop("nodes_json") or "[]")
            row["isDefault"] = bool(row.pop("is_default"))
            row["active"] = bool(row["active"])
        if not active_only:
            return rows
        serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
        context_summary_cache.set("route", user_id, "active-routes", serialized)
        return json.loads(serialized)

    async def create_route(self, user_id: str, name: str, description: str, trigger_keywords: list[str], nodes: list[dict[str, Any]], is_default: bool = False) -> dict[str, Any]:
        dag = DAGConfig(total=len(nodes), completed=0, nodes=nodes)
        template_engine.validate(dag)
        existing = await afetch_one("SELECT id FROM agent_routes WHERE name=$1 AND user_id=$2", name, user_id)
        if existing:
            raise ValueError(f"Agent route with name '{name}' already exists")
        if is_default:
            await aexecute("UPDATE agent_routes SET is_default=0 WHERE user_id=$1", user_id)
        route_id = await aexecute_insert(
            "INSERT INTO agent_routes(name,user_id,description,trigger_keywords,nodes_json,is_default,active,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id",
            name, user_id, description, json.dumps(trigger_keywords, ensure_ascii=False), json.dumps(nodes, ensure_ascii=False), 1 if is_default else 0, 1, now(), now(),
        )
        route = await self.get_route(int(route_id), user_id)
        if not route:
            raise ValueError("Agent route create failed")
        context_summary_cache.invalidate("route", user_id)
        return route

    async def get_route(self, route_id: int, user_id: str) -> dict[str, Any] | None:
        row = await afetch_one("SELECT id,name,description,trigger_keywords,nodes_json,is_default,active,created_at,updated_at FROM agent_routes WHERE id=$1 AND user_id=$2", route_id, user_id)
        if not row:
            return None
        row["triggerKeywords"] = json.loads(row.pop("trigger_keywords") or "[]")
        row["nodes"] = json.loads(row.pop("nodes_json") or "[]")
        row["isDefault"] = bool(row.pop("is_default"))
        row["active"] = bool(row["active"])
        return row

    async def set_default(self, route_id: int, user_id: str) -> dict[str, Any]:
        if not await self.get_route(route_id, user_id):
            raise ValueError("Agent route not found")
        await aexecute("UPDATE agent_routes SET is_default=0 WHERE user_id=$1", user_id)
        await aexecute("UPDATE agent_routes SET is_default=1,active=1,updated_at=$1 WHERE id=$2 AND user_id=$3", now(), route_id, user_id)
        route = await self.get_route(route_id, user_id)
        if not route:
            raise ValueError("Agent route not found")
        context_summary_cache.invalidate("route", user_id)
        return route

    async def set_active(self, route_id: int, user_id: str, active: bool) -> dict[str, Any]:
        if not await self.get_route(route_id, user_id):
            raise ValueError("Agent route not found")
        await aexecute("UPDATE agent_routes SET active=$1,updated_at=$2 WHERE id=$3 AND user_id=$4", 1 if active else 0, now(), route_id, user_id)
        route = await self.get_route(route_id, user_id)
        if not route:
            raise ValueError("Agent route not found")
        context_summary_cache.invalidate("route", user_id)
        return route

    async def resolve_dag(self, intent: str, user_id: str = "") -> tuple[DAGConfig, int | None, dict[str, Any] | None]:
        route = await self._match_route(intent, user_id)
        if route:
            dag = DAGConfig(total=len(route["nodes"]), completed=0, nodes=route["nodes"])
            template_engine.validate(dag)
            return dag, None, route
        dag, template_id = await template_engine.match_template(intent)
        return dag, template_id, None

    async def _match_route(self, intent: str, user_id: str = "") -> dict[str, Any] | None:
        routes = await self.list_routes(user_id, active_only=True)
        intent_lower = intent.lower()

        for route in routes:
            explicit_tokens = [f"#route:{route['name'].lower()}", f"#路线:{route['name']}", f"@路线:{route['name']}"]
            if any(token in intent_lower or token in intent for token in explicit_tokens):
                return route

        best_score = 0.0
        best_route: dict[str, Any] | None = None
        for route in routes:
            keywords = route.get("triggerKeywords", [])
            if not keywords:
                continue
            hits = sum(1 for keyword in keywords if keyword.lower() in intent_lower or keyword in intent)
            score = hits / max(len(keywords), 1)
            if score > best_score:
                best_score = score
                best_route = route
        if best_route and best_score >= 0.25:
            return best_route

        return next((route for route in routes if route.get("isDefault")), None)


    async def extract_route_ref(self, content: str, user_id: str = "") -> tuple[dict[str, Any] | None, str]:
        """Extract ``#route:name`` / ``#路线:name`` from content and match against routes.

        Returns ``(matched_route, stripped_content)`` where ``matched_route`` is
        the route dict if a match was found and ``stripped_content`` has the
        route token removed.  Returns ``(None, content)`` if no match.
        """
        import re

        # Match patterns like: #route:标准研发闭环  or  #路线:快速代码生成
        # Also handles leading/trailing whitespace and full-width chars in names
        pattern = r'(?:^|\s)(?:#route:|#路线:|@路线:)\s*([^\s]+)'
        match = re.search(pattern, content)
        if not match:
            return None, content

        route_name = match.group(1).strip()
        routes = await self.list_routes(user_id, active_only=True)

        # Exact match first, then case-insensitive
        matched = None
        route_name_lower = route_name.lower()
        for route in routes:
            if route["name"] == route_name or route["name"].lower() == route_name_lower:
                matched = route
                break

        if not matched:
            return None, content

        # Strip the matched token from content
        stripped = content[:match.start()] + content[match.end():]
        stripped = stripped.strip()

        return matched, stripped


agent_route_service = AgentRouteService()
