from __future__ import annotations

import json
from typing import Any

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute, aexecute_insert, atransaction
from app.schemas.dag import DAGConfig
from app.services.context_summary_cache import context_summary_cache
from app.services.template_engine import template_engine
from app.services.workflow_contract import require_valid_workflow
from app.services.workflow_errors import WorkflowVersionConflict


_ROUTE_COLUMNS = (
    "id,name,description,trigger_keywords,nodes_json,edges_json,is_default,active,"
    "version,schema_version,created_at,updated_at"
)


class AgentRouteService:
    async def list_routes(self, user_id: str, active_only: bool = False) -> list[dict[str, Any]]:
        if active_only:
            cached = context_summary_cache.get("route", user_id, "active-routes")
            if cached is not None:
                return json.loads(cached)
        active_clause = " AND active=1" if active_only else ""
        rows = await afetch_all(
            f"SELECT {_ROUTE_COLUMNS} FROM agent_routes WHERE user_id=$1{active_clause} "
            "ORDER BY is_default DESC,id DESC",
            user_id,
        )
        routes = [self._deserialize_route(row) for row in rows]
        if not active_only:
            return routes
        serialized = json.dumps(routes, ensure_ascii=False, sort_keys=True, default=str)
        context_summary_cache.set("route", user_id, "active-routes", serialized)
        return json.loads(serialized)

    async def create_route(
        self,
        user_id: str,
        name: str,
        description: str,
        trigger_keywords: list[str],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
        is_default: bool = False,
        active: bool = True,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        normalized = require_valid_workflow(nodes, edges, schema_version=schema_version)
        existing = await afetch_one("SELECT id FROM agent_routes WHERE name=$1 AND user_id=$2", name, user_id)
        if existing:
            raise ValueError(f"Agent route with name '{name}' already exists")
        if is_default:
            await aexecute(
                "UPDATE agent_routes SET is_default=0,version=version+1,updated_at=$1 "
                "WHERE user_id=$2 AND is_default=1",
                now(),
                user_id,
            )
        route_id = await aexecute_insert(
            "INSERT INTO agent_routes(name,user_id,description,trigger_keywords,nodes_json,edges_json,"
            "is_default,active,version,schema_version,created_at,updated_at) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,1,$9,$10,$11) RETURNING id",
            name,
            user_id,
            description,
            json.dumps(trigger_keywords, ensure_ascii=False),
            json.dumps(normalized["nodes"], ensure_ascii=False),
            json.dumps(normalized["edges"], ensure_ascii=False),
            1 if is_default else 0,
            1 if active else 0,
            schema_version,
            now(),
            now(),
        )
        route = await self.get_route(int(route_id), user_id)
        if not route:
            raise ValueError("Agent route create failed")
        context_summary_cache.invalidate("route", user_id)
        return route

    async def update_route(
        self,
        route_id: int,
        user_id: str,
        *,
        name: str,
        description: str,
        trigger_keywords: list[str],
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        is_default: bool,
        active: bool,
        schema_version: int,
        expected_version: int,
    ) -> dict[str, Any]:
        normalized = require_valid_workflow(nodes, edges, schema_version=schema_version)
        async with atransaction() as conn:
            updated = await conn.fetchrow(
                "UPDATE agent_routes SET name=$1,description=$2,trigger_keywords=$3,nodes_json=$4,"
                "edges_json=$5,is_default=$6,active=$7,schema_version=$8,version=version+1,updated_at=$9 "
                "WHERE id=$10 AND user_id=$11 AND version=$12 RETURNING id,version",
                name,
                description,
                json.dumps(trigger_keywords, ensure_ascii=False),
                json.dumps(normalized["nodes"], ensure_ascii=False),
                json.dumps(normalized["edges"], ensure_ascii=False),
                1 if is_default else 0,
                1 if active else 0,
                schema_version,
                now(),
                route_id,
                user_id,
                expected_version,
            )
            if updated and is_default:
                await conn.execute(
                    "UPDATE agent_routes SET is_default=0,version=version+1,updated_at=$1 "
                    "WHERE user_id=$2 AND id<>$3 AND is_default=1",
                    now(),
                    user_id,
                    route_id,
                )
        if not updated:
            current = await afetch_one(
                "SELECT version FROM agent_routes WHERE id=$1 AND user_id=$2", route_id, user_id,
            )
            if not current:
                raise LookupError("Workflow not found")
            raise WorkflowVersionConflict(expected_version, int(current["version"]))
        route = await self.get_route(route_id, user_id)
        if not route:
            raise LookupError("Workflow not found")
        context_summary_cache.invalidate("route", user_id)
        return route

    async def get_route(self, route_id: int, user_id: str) -> dict[str, Any] | None:
        row = await afetch_one(
            f"SELECT {_ROUTE_COLUMNS} FROM agent_routes WHERE id=$1 AND user_id=$2", route_id, user_id,
        )
        return self._deserialize_route(row) if row else None

    async def set_default(self, route_id: int, user_id: str) -> dict[str, Any]:
        if not await self.get_route(route_id, user_id):
            raise ValueError("Agent route not found")
        await aexecute(
            "UPDATE agent_routes SET is_default=0,version=version+1,updated_at=$1 "
            "WHERE user_id=$2 AND id<>$3 AND is_default=1",
            now(), user_id, route_id,
        )
        await aexecute(
            "UPDATE agent_routes SET is_default=1,active=1,version=version+1,updated_at=$1 "
            "WHERE id=$2 AND user_id=$3",
            now(), route_id, user_id,
        )
        route = await self.get_route(route_id, user_id)
        if not route:
            raise ValueError("Agent route not found")
        context_summary_cache.invalidate("route", user_id)
        return route

    async def set_active(self, route_id: int, user_id: str, active: bool) -> dict[str, Any]:
        if not await self.get_route(route_id, user_id):
            raise ValueError("Agent route not found")
        await aexecute(
            "UPDATE agent_routes SET active=$1,version=version+1,updated_at=$2 WHERE id=$3 AND user_id=$4",
            1 if active else 0, now(), route_id, user_id,
        )
        route = await self.get_route(route_id, user_id)
        if not route:
            raise ValueError("Agent route not found")
        context_summary_cache.invalidate("route", user_id)
        return route

    async def resolve_dag(self, intent: str, user_id: str = "") -> tuple[DAGConfig, int | None, dict[str, Any] | None]:
        route = await self._match_route(intent, user_id)
        if route:
            dag = DAGConfig(
                total=len(route["nodes"]), nodes=route["nodes"], edges=route.get("edges", []),
                version=route.get("version", 1), schema_version=route.get("schemaVersion", 1),
            )
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
                best_score, best_route = score, route
        if best_route and best_score >= 0.25:
            return best_route
        return next((route for route in routes if route.get("isDefault")), None)

    async def extract_route_ref(self, content: str, user_id: str = "") -> tuple[dict[str, Any] | None, str]:
        import re

        match = re.search(r"(?:^|\s)(?:#route:|#路线:|@路线:)\s*([^\s]+)", content)
        if not match:
            return None, content
        route_name = match.group(1).strip().lower()
        matched = next(
            (route for route in await self.list_routes(user_id, active_only=True) if route["name"].lower() == route_name),
            None,
        )
        if not matched:
            return None, content
        return matched, (content[:match.start()] + content[match.end():]).strip()

    @staticmethod
    def _deserialize_route(row: dict[str, Any]) -> dict[str, Any]:
        route = dict(row)
        route["triggerKeywords"] = json.loads(route.pop("trigger_keywords") or "[]")
        route["nodes"] = json.loads(route.pop("nodes_json") or "[]")
        route["edges"] = json.loads(route.pop("edges_json") or "[]")
        route["isDefault"] = bool(route.pop("is_default"))
        route["active"] = bool(route["active"])
        route["schemaVersion"] = int(route.pop("schema_version"))
        route["version"] = int(route["version"])
        return route


agent_route_service = AgentRouteService()
