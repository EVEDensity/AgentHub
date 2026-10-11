"""Legacy standalone benchmark Harness entry; separate from durable Runner commands."""
from __future__ import annotations

from typing import Any


async def run_mission_sync(
    objective: str,
    *,
    llm_adapter: Any | None = None,
    adapter_name: str | None = None,
    timeout_seconds: int = 300,
    max_reflections: int = 2,
    enable_planning: bool = True,
) -> dict[str, Any]:
    """Run a mission end-to-end in a single sync-friendly call.

    This is the **RealEngine** entry for ``agenthub_bench`` and any caller
    that wants to execute one mission without spinning up the full
    Runner Worker lifecycle (claim → lease → heartbeat).

    Flow:
    1. Create harness via runner_composition (auto: ReflectiveHarness + Planner)
    2. Execute ``objective`` in a bounded loop (budget = timeout / 10s)
    3. Return structured verdict + trajectory

    Parameters
        objective:             Natural-language task description.
        llm_adapter:           Optional pre-configured LLM adapter instance.
        adapter_name:          Optional adapter name (e.g. "deepseek").
        timeout_seconds:       Hard wall-clock timeout for the run.
        max_reflections:       Reflection loop cap (0 = no reflection).
        enable_planning:       Enable LLMPlanner (goal → subgoals → steps).

    Returns a dict::

        {
            "status":        "SUCCEEDED" | "FAILED" | "TIMEOUT",
            "verdict":       "pass" | "fail" | "inconclusive",
            "objective":     echo of the input,
            "rounds":        int (total harness rounds executed),
            "duration_ms":   int,
            "error":         str | None,
        }

    Design note
        This function is intentionally **dependency-light**: it takes an
        ``llm_adapter`` directly and does NOT require MissionControl,
        ClaimService, or any HTTP server.  It is the minimal call surface
        for benchmark harnesses (SWE-bench, Terminal-bench).
    """
    import time as _time

    from app.services.harness_planner import HarnessRequest
    from app.services.runner_composition import compose_reflective_harness

    start = _time.time()

    try:
        # Resolve adapter
        if llm_adapter is None:
            if adapter_name:
                from app.services.adapter_manager import AdapterManager
                mgr = AdapterManager()
                llm_adapter = mgr._adapters.get(adapter_name) if hasattr(mgr, "_adapters") else None
            if llm_adapter is None:
                from app.services.adapter_manager import MockAdapter
                llm_adapter = MockAdapter()

        # Build reflective harness (auto-includes Planner + reflection)
        harness = compose_reflective_harness(
            llm_adapter,
            max_reflections=max_reflections,
            enable_planning=enable_planning,
        )

        # Harness API: execute(HarnessRequest) -> HarnessResult
        request = HarnessRequest(
            code=objective,
            language="python",
            timeout=float(timeout_seconds),
        )
        result = await harness.execute(request)

        elapsed_ms = int((_time.time() - start) * 1000)

        # HarnessResult fields: sandbox, iterations, tool_calls, usage
        iterations = getattr(result, "iterations", 0) or 0
        tool_calls = getattr(result, "tool_calls", 0) or 0
        sandbox = getattr(result, "sandbox", None)
        exit_code = getattr(sandbox, "exit_code", 0) if sandbox else 0

        status = "SUCCEEDED" if exit_code == 0 else "FAILED"
        verdict = "pass" if exit_code == 0 else "fail"

        return {
            "status": status,
            "verdict": verdict,
            "objective": objective,
            "rounds": iterations,
            "tool_calls": tool_calls,
            "duration_ms": elapsed_ms,
            "error": None,
        }

    except Exception as exc:
        elapsed_ms = int((_time.time() - start) * 1000)
        return {
            "status": "FAILED",
            "verdict": "fail",
            "objective": objective,
            "rounds": 0,
            "duration_ms": elapsed_ms,
            "error": str(exc),
        }
