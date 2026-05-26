from __future__ import annotations

import json
from typing import Any

from app.db.init_db import now
from app.db.session import dict_rows, get_connection, one_row
from app.schemas.dag import DAGConfig
from app.services.template_engine import template_engine


class AgentRouteService:
    def list_routes(self, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT id,name,description,trigger_keywords,nodes_json,is_default,active,created_at,updated_at FROM agent_routes"
        params: tuple[Any, ...] = ()
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY is_default DESC,id DESC"
        rows = dict_rows(sql, params)
        for row in rows:
            row["triggerKeywords"] = json.loads(row.pop("trigger_keywords") or "[]")
            row["nodes"] = json.loads(row.pop("nodes_json") or "[]")
            row["isDefault"] = bool(row.pop("is_default"))
            row["active"] = bool(row["active"])
        return rows

    def create_route(self, name: str, description: str, trigger_keywords: list[str], nodes: list[dict[str, Any]], is_default: bool = False) -> dict[str, Any]:
        dag = DAGConfig(total=len(nodes), completed=0, nodes=nodes)
        template_engine.validate(dag)
        with get_connection() as conn:
            existing = conn.execute("SELECT id FROM agent_routes WHERE name=?", (name,)).fetchone()
            if existing:
                raise ValueError(f"Agent route with name '{name}' already exists")
            if is_default:
                conn.execute("UPDATE agent_routes SET is_default=0")
            cursor = conn.execute(
                "INSERT INTO agent_routes(name,description,trigger_keywords,nodes_json,is_default,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (name, description, json.dumps(trigger_keywords, ensure_ascii=False), json.dumps(nodes, ensure_ascii=False), 1 if is_default else 0, 1, now(), now()),
            )
        route = self.get_route(cursor.lastrowid)
        if not route:
            raise ValueError("Agent route create failed")
        return route

    def get_route(self, route_id: int) -> dict[str, Any] | None:
        row = one_row("SELECT id,name,description,trigger_keywords,nodes_json,is_default,active,created_at,updated_at FROM agent_routes WHERE id=?", (route_id,))
        if not row:
            return None
        row["triggerKeywords"] = json.loads(row.pop("trigger_keywords") or "[]")
        row["nodes"] = json.loads(row.pop("nodes_json") or "[]")
        row["isDefault"] = bool(row.pop("is_default"))
        row["active"] = bool(row["active"])
        return row

    def set_default(self, route_id: int) -> dict[str, Any]:
        if not self.get_route(route_id):
            raise ValueError("Agent route not found")
        with get_connection() as conn:
            conn.execute("UPDATE agent_routes SET is_default=0")
            conn.execute("UPDATE agent_routes SET is_default=1,active=1,updated_at=? WHERE id=?", (now(), route_id))
        route = self.get_route(route_id)
        if not route:
            raise ValueError("Agent route not found")
        return route

    def set_active(self, route_id: int, active: bool) -> dict[str, Any]:
        if not self.get_route(route_id):
            raise ValueError("Agent route not found")
        with get_connection() as conn:
            conn.execute("UPDATE agent_routes SET active=?,updated_at=? WHERE id=?", (1 if active else 0, now(), route_id))
        route = self.get_route(route_id)
        if not route:
            raise ValueError("Agent route not found")
        return route

    def resolve_dag(self, intent: str) -> tuple[DAGConfig, int | None, dict[str, Any] | None]:
        route = self._match_route(intent)
        if route:
            dag = DAGConfig(total=len(route["nodes"]), completed=0, nodes=route["nodes"])
            template_engine.validate(dag)
            return dag, None, route
        dag, template_id = template_engine.match_template(intent)
        return dag, template_id, None

    def _match_route(self, intent: str) -> dict[str, Any] | None:
        routes = self.list_routes(active_only=True)
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


agent_route_service = AgentRouteService()
