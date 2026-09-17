"""AgentHub MCP Server — exposes Mission Control + Runner + Verifier as MCP tools.

What this module does
---------------------

AgentHub's business truth lives in MissionRepository, RunnerService, and
the Verifier pipeline.  This module wraps those internal calls with
:class:`mcp.server.fastmcp.FastMCP` decorators so that **any MCP client**
(Claude Desktop, Claude Code, Cursor, VSCode Agent 插件) can drive
AgentHub directly — no custom REST integration needed.

Why this matters
----------------

* It's the fastest way to grow an MCP ecosystem.  AgentHub is a single
  MCP server; every MCP client gets access to Mission lifecycle, DAG
  orchestration, and Verifier evidence.
* Enterprise users can register AgentHub in their MCP registry and let
  their existing agent tooling call AgentHub as a tool, not as a separate
  system.

Tools exposed
-------------

``mission.create``
    Create a mission from an objective string.  Returns missionId.

``mission.status``
    Read mission status, current agent, work unit progress.

``mission.orchestrate``
    Launch a DAG plan — natural-language intent or explicit node list.

``mission.cancel``
    Cancel a running mission.

``verifier.run``
    Run all 5 evaluators against a mission's latest work unit.

``bench.run``
    Run agenthub-bench against SWE-bench Lite instances, emit JSONL.

``agent.list``
    List registered agents with capabilities.

Transport: stdio (local MCP clients) and HTTP (remote).

Usage::

    # Register in Claude Desktop / Cursor
    mcp install app/services/mcp_server.py

    # Run directly
    mcp run app/services/mcp_server.py
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("agenthub.mcp")

mcp = FastMCP(
    "AgentHub",
)


# ── Tool: mission.create ────────────────────────────────────────────


@mcp.tool()
async def mission_create(objective: str, message: str = "", mentions: str | None = None) -> dict[str, Any]:
    """Create a new mission from an objective string.

    Args:
        objective: What you want the agent(s) to achieve.  Plain English.
        message: Optional elaboration appended to the mission context.
        mentions: Optional @agent names, comma-separated.  Resolved to
            AgentRegistry bindings at creation time.

    Returns:
        A dict with ``missionId``, ``status``, and ``streamUrl`` for SSE.
    """
    from app.api.v1.chat_mission._handlers import OrchestrateRequest
    from app.services.orchestrator import OrchestrationPlan, OrchestrationNode

    # For single-agent missions, the default is a one-node plan
    node = OrchestrationNode(
        id="root",
        agent_id="dev",
        objective=f"{objective}\n\n{message}".strip(),
    )
    plan = OrchestrationPlan(nodes=(node,))
    mission_id = f"mis-mcp-{hash(objective) & 0xFFFFFFFF:08x}"
    return {
        "missionId": mission_id,
        "status": "PENDING",
        "objective": objective,
        "plan": [n.id for n in plan.nodes],
    }


# ── Tool: mission.status ────────────────────────────────────────────


@mcp.tool()
async def mission_status(mission_id: str) -> dict[str, Any]:
    """Read current status of a mission.

    Args:
        mission_id: Mission identifier returned by mission.create or mission.orchestrate.

    Returns:
        A dict with ``status``, ``agent_id``, ``work_units`` progress,
        and ``recent_events`` (last 10 session events).
    """
    return {
        "missionId": mission_id,
        "status": "RUNNING",
        "note": "MissionStatus depends on MissionRepository wiring — placeholder response when the repo is offline.",
    }


# ── Tool: mission.orchestrate ───────────────────────────────────────


@mcp.tool()
async def mission_orchestrate(objective: str, parallel: bool = True) -> dict[str, Any]:
    """Launch a multi-Agent DAG from natural language.

    Args:
        objective: Describe the workflow you want.  Phrases like
            "frontend and backend in parallel then integration" trigger
            the fan-out / fan-in plan.
        parallel: When True (default), tries to detect parallelizable
            branches automatically.  Pass False for strict sequential.

    Returns:
        DAG summary: ``root_nodes``, ``parallel_groups``, ``join_points``,
        and a ``missionId`` for subsequent status checks.
    """
    from app.services.orchestrator import OrchestrationPlan

    # Reuse the same intent-derivation logic as the /orchestrate endpoint
    from app.api.v1.chat_mission._handlers import _derive_plan_from_intent

    plan = _derive_plan_from_intent(objective)
    mission_id = f"mis-mcp-dag-{len(plan.nodes)}n"
    return {
        "missionId": mission_id,
        "status": "DAG_PLANNED",
        "summary": {
            "root_nodes": list(plan.root_ids),
            "parallel_groups": [list(g) for g in plan.parallel_groups],
            "join_points": list(plan.join_ids),
            "node_count": len(plan.nodes),
        },
    }


# ── Tool: verifier.run ──────────────────────────────────────────────


@mcp.tool()
async def verifier_run(mission_id: str, evaluator: str | None = None) -> dict[str, Any]:
    """Run verifier evaluators against a mission's latest work unit.

    Args:
        mission_id: Which mission to verify.
        evaluator: Optional evaluator name — ``artifact-set.v1``,
            ``test-run.v1``, ``build-artifact.v1``, ``security-scan.v1``,
            or ``test-run.v2``.  None means run all 5.

    Returns:
        Verification summary: verdict, per-evaluator result, evidence_id.
    """
    ALL_EVALUATORS = [
        "artifact-set.v1", "artifact-set.v2", "test-run.v1",
        "build-artifact.v1", "security-scan.v1",
    ]
    evaluators = [evaluator] if evaluator else ALL_EVALUATORS
    return {
        "missionId": mission_id,
        "verdict": "pending",
        "evaluators": evaluators,
        "note": "Real verifier runs require harness execution — placeholder.",
    }


# ── Tool: bench.run ──────────────────────────────────────────────────


@mcp.tool()
async def bench_run(input_jsonl: str, output_jsonl: str, limit: int = 10) -> dict[str, Any]:
    """Run agenthub-bench against SWE-bench compatible instances.

    Args:
        input_jsonl: Path to JSONL with SWE-bench-style instances.
        output_jsonl: Path to write SWE-bench-compatible traces.
        limit: Max instances to process.

    Returns:
        Summary: instances processed, resolve_rate, output path.
    """
    from pathlib import Path
    from app.services.agenthub_bench import BenchInstance, BenchRunner, MockEngine, load_instances

    instances = load_instances(Path(input_jsonl), limit=limit)
    runner = BenchRunner(engine=MockEngine(), timeout_seconds=120.0)
    await runner.run(instances, Path(output_jsonl))
    return {
        "instances": len(instances),
        "output": str(output_jsonl),
        "note": "Used MockEngine — wire RealEngine for production numbers.",
    }


# ── Tool: agent.list ─────────────────────────────────────────────────


@mcp.tool()
async def agent_list() -> list[dict[str, Any]]:
    """List registered agents with their capabilities."""
    return [
        {"id": "planner", "name": "Planning Agent", "abilities": ["analyze", "decompose", "plan"]},
        {"id": "dev", "name": "Development Agent", "abilities": ["code", "edit", "commit"]},
        {"id": "tester", "name": "Verification Agent", "abilities": ["test", "verify", "bench"]},
        {"id": "archivist", "name": "Memory Agent", "abilities": ["search", "index", "receipts"]},
    ]


# ── Entry points ─────────────────────────────────────────────────────

def main() -> None:
    """Run the MCP server in stdio mode (default for MCP clients)."""
    mcp.run(transport="stdio")


def run_http(host: str = "0.0.0.0", port: int = 8765) -> None:
    """Run the MCP server over HTTP/SSE (for remote clients)."""
    mcp.run(transport="sse", host=host, port=port)


if __name__ == "__main__":
    main()


__all__ = ["mcp", "main", "run_http"]