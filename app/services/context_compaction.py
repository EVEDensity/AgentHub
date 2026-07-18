from __future__ import annotations

from typing import Any


def compact_text(text: str, *, max_chars: int = 240, ellipsis: str = "...") -> str:
    """Collapse whitespace and trim a string to a compact prompt-friendly form."""
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= len(ellipsis):
        return normalized[:max_chars]
    return normalized[: max_chars - len(ellipsis)].rstrip() + ellipsis


def _compact_tags(tags_raw: Any, *, max_tags: int = 4) -> str:
    if isinstance(tags_raw, str):
        import json

        try:
            tags = json.loads(tags_raw)
        except Exception:
            tags = []
    elif isinstance(tags_raw, list):
        tags = tags_raw
    else:
        tags = []
    return ",".join(str(tag) for tag in tags[:max_tags] if str(tag).strip())


def build_agent_roster_summary(
    agents: list[dict[str, Any]],
    *,
    max_agents: int = 8,
    max_tags: int = 4,
    max_duty_chars: int = 72,
) -> str:
    """Build a compact agent capability block for LLM prompts."""
    if not agents:
        return ""

    lines: list[str] = ["【Agent能力摘要】"]
    for agent in agents[:max_agents]:
        agent_id = compact_text(str(agent.get("agent_id", "unknown")), max_chars=28)
        domain = compact_text(str(agent.get("domain", "unknown")), max_chars=18)
        risk = compact_text(str(agent.get("risk_level", "L1")), max_chars=8)
        status = compact_text(str(agent.get("status", "sleeping")), max_chars=12)
        duty = compact_text(str(agent.get("duty_note", "")), max_chars=max_duty_chars)
        tags = _compact_tags(agent.get("capability_tags", []), max_tags=max_tags)
        tag_part = f" tags={tags}" if tags else ""
        duty_part = f" duty={duty}" if duty else ""
        lines.append(f"- {agent_id}|{domain}|{status}|{risk}{tag_part}{duty_part}")

    if len(agents) > max_agents:
        lines.append(f"- ... 其余 {len(agents) - max_agents} 个 Agent 已省略")

    return "\n".join(lines) + "\n"


def build_preprocess_context(preprocessed: dict[str, Any]) -> str:
    """Compress orchestrator preprocessor output into a short prompt block."""
    if not preprocessed or preprocessed.get("is_simple"):
        return ""

    parts: list[str] = ["【预处理摘要】"]
    parts.append(f"intent={preprocessed.get('intent_type', 'technical_development')}")

    clarified = compact_text(preprocessed.get("clarified_question", ""), max_chars=160)
    if clarified:
        parts.append(f"question={clarified}")

    requirements = [
        compact_text(req, max_chars=70)
        for req in preprocessed.get("requirements", [])[:5]
        if compact_text(req, max_chars=70)
    ]
    if requirements:
        parts.append("requirements=" + "；".join(requirements))

    nf_requirements = [
        compact_text(req, max_chars=70)
        for req in preprocessed.get("non_functional_requirements", [])[:4]
        if compact_text(req, max_chars=70)
    ]
    if nf_requirements:
        parts.append("nonfunctional=" + "；".join(nf_requirements))

    selected_solution = preprocessed.get("_selected_solution")
    if isinstance(selected_solution, dict):
        sol_name = compact_text(selected_solution.get("name", ""), max_chars=60)
        sol_stack = ", ".join(str(item) for item in selected_solution.get("tech_stack", [])[:4] if str(item).strip())
        sol_arch = compact_text(selected_solution.get("architecture", ""), max_chars=120)
        sol_bits = [bit for bit in [sol_name, sol_stack, sol_arch] if bit]
        if sol_bits:
            parts.append("selected=" + " | ".join(sol_bits))

    solutions = preprocessed.get("solutions", [])[:2]
    if solutions:
        solution_bits: list[str] = []
        for sol in solutions:
            if not isinstance(sol, dict):
                continue
            sol_id = compact_text(sol.get("id", ""), max_chars=20)
            sol_name = compact_text(sol.get("name", ""), max_chars=48)
            stack = ", ".join(str(item) for item in sol.get("tech_stack", [])[:4] if str(item).strip())
            score = sol.get("score")
            risk = compact_text(sol.get("risk_level", ""), max_chars=10)
            bit = f"{sol_id}:{sol_name}"
            extras: list[str] = []
            if stack:
                extras.append(stack)
            if score is not None:
                extras.append(f"score={score}")
            if risk:
                extras.append(f"risk={risk}")
            if extras:
                bit += f" ({', '.join(extras)})"
            solution_bits.append(bit)
        if solution_bits:
            parts.append("solutions=" + " | ".join(solution_bits))

    sub_tasks = preprocessed.get("sub_tasks", [])[:5]
    if sub_tasks:
        task_bits: list[str] = []
        for task in sub_tasks:
            if not isinstance(task, dict):
                continue
            task_id = compact_text(str(task.get("id", "?")), max_chars=12)
            domain = compact_text(str(task.get("domain", "general")), max_chars=12)
            title = compact_text(task.get("title", ""), max_chars=36)
            deps = ",".join(f"n{dep}" if str(dep).isdigit() else compact_text(str(dep), max_chars=12) for dep in task.get("depends_on", [])[:4]) or "-"
            task_bits.append(f"{task_id}:{domain}:{title}<-{deps}")
        if task_bits:
            parts.append("subtasks=" + " | ".join(task_bits))

    constraints = [
        compact_text(item, max_chars=64)
        for item in preprocessed.get("constraints", [])[:5]
        if compact_text(item, max_chars=64)
    ]
    if constraints:
        parts.append("constraints=" + "；".join(constraints))

    routing = preprocessed.get("routing")
    if isinstance(routing, dict):
        order = [compact_text(item, max_chars=16) for item in routing.get("execution_order", [])[:5] if compact_text(item, max_chars=16)]
        parallel = [compact_text(item, max_chars=24) for item in routing.get("parallel_opportunities", [])[:3] if compact_text(item, max_chars=24)]
        conflicts = [compact_text(item, max_chars=24) for item in routing.get("potential_conflicts", [])[:3] if compact_text(item, max_chars=24)]
        if order:
            parts.append("route=" + "->".join(order))
        if parallel:
            parts.append("parallel=" + "；".join(parallel))
        if conflicts:
            parts.append("conflicts=" + "；".join(conflicts))

    approach = compact_text(preprocessed.get("suggested_approach", ""), max_chars=120)
    if approach:
        parts.append(f"approach={approach}")

    return "\n".join(parts) + "\n"


def build_task_preview_item(node: Any) -> dict[str, Any]:
    """Build a compact preview item for websocket task confirmations."""
    def _node_value(obj: Any, key: str, default: Any = "") -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    dependencies = _node_value(node, "dependencies", [])
    if not isinstance(dependencies, list):
        dependencies = list(dependencies) if dependencies else []

    return {
        "id": str(_node_value(node, "id", "")),
        "description": compact_text(_node_value(node, "description", ""), max_chars=84),
        "agent": str(_node_value(node, "agent", "")),
        "dependencies": dependencies,
        "estimatedSeconds": {
            "low": 20,
            "medium": 45,
            "high": 90,
        }.get(str(_node_value(node, "estimated_effort", "medium")), 45),
    }


def build_result_preview(text: str, *, max_chars: int = 220) -> str:
    """Compress a result text into a short preview for synthesis metadata."""
    return compact_text(text, max_chars=max_chars)
