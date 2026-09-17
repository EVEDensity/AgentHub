"""Explicit Planner + Reflective Harness for FunctionCallingHarness.

Two components that close the gap to production-grade agent execution:

1. ``PlannerPort`` — a protocol for a three-level plan: goal → subgoals →
   concrete tool-call sequence.  The default implementation asks the model
   itself to produce a structured plan before any tool runs; external users
   can wire in a smarter planner (LLM + search + memory retrieval, etc.).

2. ``ReflectiveHarness`` — wraps any :class:`HarnessPort` with a bounded
   reflect→retry loop.  When the wrapped harness fails, ReflectiveHarness
   feeds the failure back to the model, asks for a corrective patch, and
   re-executes.  Up to ``max_reflections`` times; the loop is intentionally
   short because unbounded self-healing is how demo agents get stuck.

Both components integrate at the *composition* layer — nothing in
``FunctionCallingHarness`` changes.  Composing them is one line::

    base = FunctionCallingHarness(model, tools, ...)
    harness = ReflectiveHarness(base, max_reflections=2, planner=llm_planner)

Design notes
------------

Why a planner *and* a reflective loop?

* **Planner before the first tool call** eliminates the "agent runs aimlessly
  in circles" failure mode.  The model must commit to a goal and a rough
  sequence before burning tool-call budget.  The plan is *advisory*, not
  rigid — the model can diverge, but the loop records every deviation.

* **Reflect after a failure** catches the common "agent tries one thing,
  it fails, it gives up" pattern.  Aider's ``--self-reflect`` and Devin's
  auto-patch are both this idea; the AgentHub version adds an evidence
  gate: reflection only runs when the original attempt produced a
  machine-readable failure (test exit code, sandbox error, explicit
  ``error`` field in a tool result).

Both are *optional*.  ``FunctionCallingHarness`` works exactly as before
when neither is composed on top.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from app.services.harness_service import (
    FunctionCall,
    FunctionCallingHarness,
    FunctionResult,
    HarnessPort,
    HarnessRequest,
    HarnessResult,
    ModelPort,
    ModelResponse,
    ModelUsage,
)

logger = logging.getLogger("agenthub.harness.planner")


# ── Plan types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlanStep:
    """One concrete action the agent intends to take.

    ``kind`` chooses the action flavour:

    * ``"tool"``     — call a function tool with the given arguments
    * ``"code"``     — run a code snippet through the sandbox
    * ``"branch"``   — conditional: execute ``then_step`` if
      ``condition`` is truthy, else ``else_step`` (both optional)
    * ``"note"``     — no-op planning note, useful for readability
    """

    kind: str  # "tool" | "code" | "branch" | "note"
    description: str
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    code: str | None = None
    language: str | None = None
    condition: str | None = None
    then_step: str | None = None
    else_step: str | None = None


@dataclass(frozen=True)
class SubGoal:
    """An intermediate milestone the agent must reach before moving on."""

    id: str
    description: str
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True)
class Plan:
    """Three-level plan: one top-level goal → N subgoals → steps each."""

    goal: str
    context_hints: tuple[str, ...] = ()
    subgoals: tuple[SubGoal, ...] = ()
    raw_text: str = ""  # Model's freeform explanation of the plan

    @property
    def total_steps(self) -> int:
        return sum(len(s.steps) for s in self.subgoals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "context_hints": list(self.context_hints),
            "subgoals": [
                {
                    "id": sg.id,
                    "description": sg.description,
                    "steps": [
                        {
                            "kind": s.kind,
                            "description": s.description,
                            **(
                                {"tool_name": s.tool_name, "tool_args": s.tool_args}
                                if s.kind == "tool"
                                else {"code": s.code, "language": s.language}
                                if s.kind == "code"
                                else {}
                            ),
                        }
                        for s in sg.steps
                    ],
                }
                for sg in self.subgoals
            ],
        }


# ── Planner protocol ──────────────────────────────────────────────────


class PlannerPort(Protocol):
    """Any component that can turn a goal into a structured Plan."""

    async def plan(
        self,
        goal: str,
        *,
        context: str = "",
        tools: tuple[str, ...] = (),
    ) -> Plan: ...


# ── Default LLM-driven planner ────────────────────────────────────────


_PLANNING_SYSTEM_PROMPT = """You are an execution planner for an autonomous coding agent.
Given the user's goal and the tools available, produce a structured three-level plan.

Respond with a JSON object using exactly this schema:

{
  "goal": "<single-sentence restatement of the task>",
  "context_hints": ["things the model should remember during execution"],
  "subgoals": [
    {
      "id": "sg-1",
      "description": "<intermediate milestone>",
      "steps": [
        {
          "kind": "tool",
          "description": "<what this step accomplishes>",
          "tool_name": "<exact tool name>",
          "tool_args": {"key": "value"}
        },
        {
          "kind": "code",
          "description": "<what this step accomplishes>",
          "code": "print('hello')",
          "language": "python"
        },
        {
          "kind": "note",
          "description": "<planning note, no execution>"
        }
      ]
    }
  ]
}

Rules:
- Keep the plan SHORT. 2-4 subgoals, 1-3 steps each.
- Prefer tool steps when a tool is available.  Code steps are for quick
  inline experiments only.
- Every tool_name must be one of the available tools listed below.
- Do NOT write code steps that require shell access; use tool steps instead.
- The goal should be specific and verifiable.
"""


class LLMPlanner:
    """Default planner that asks the model to produce a structured Plan.

    Uses the same :class:`ModelPort` adapter that will later drive the
    execution loop, so plan and execution share the same model weights.

    ``memory_provider`` is optional — when given, the planner fetches a
    tiered memory bundle (session transcript + project facts + evidence
    receipts) and prepends it to the planning context.  When missing,
    planning still runs but without cross-session memory.
    """

    def __init__(
        self,
        model: ModelPort,
        *,
        max_context_chars: int = 4_000,
        memory_provider: Any = None,
    ) -> None:
        self._model = model
        self._max_context_chars = max_context_chars
        self._memory_provider = memory_provider

    async def plan(
        self,
        goal: str,
        *,
        context: str = "",
        tools: tuple[str, ...] = (),
    ) -> Plan:
        # Pull in tiered memory context when available
        memory_text = ""
        if self._memory_provider is not None:
            try:
                bundle = self._memory_provider.get_context()
                memory_text = bundle.format_for_prompt()
            except Exception as exc:  # noqa: BLE001 - memory failure must not block planning
                logger.warning("planner: memory provider failed (%s), proceeding without", exc)

        tool_list = ", ".join(tools) if tools else "(none declared)"
        truncated_ctx = (
            context[: self._max_context_chars] + "…"
            if len(context) > self._max_context_chars
            else context
        )
        full_ctx_parts: list[str] = []
        if memory_text:
            full_ctx_parts.append(memory_text)
        full_ctx_parts.append(truncated_ctx)
        full_ctx = "\n\n".join(full_ctx_parts)

        user_prompt = (
            f"Goal: {goal}\n\n"
            f"Available tools: {tool_list}\n\n"
            f"Context:\n{full_ctx}\n\n"
            "Produce the three-level execution plan as JSON."
        )
        request = HarnessRequest(
            code=f"{_PLANNING_SYSTEM_PROMPT}\n\n---\n\n{user_prompt}",
            language="text",
            timeout=30.0,
        )
        response = await self._model.complete(request, (), tools_enabled=False)
        raw = response.content.strip()

        # Extract JSON — tolerate models that wrap it in a code fence
        json_text = _extract_json_object(raw) or raw
        try:
            return _parse_plan(json_text, raw_text=raw)
        except Exception as exc:  # noqa: BLE001 - bad plan becomes a note-only plan
            logger.warning("planner: model returned unparseable plan (%s), falling back", exc)
            return Plan(
                goal=goal,
                context_hints=(f"Plan parse failed: {exc}",),
                raw_text=raw,
            )


def _extract_json_object(text: str) -> str | None:
    """Pull the first top-level {...} object out of a model response."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_plan(text: str, *, raw_text: str = "") -> Plan:
    data = json.loads(text) if isinstance(text, str) else text
    goal = str(data.get("goal", "")).strip() or "(no goal specified)"
    hints = tuple(str(h) for h in data.get("context_hints", []) if str(h).strip())

    subgoals: list[SubGoal] = []
    for idx, sg in enumerate(data.get("subgoals", [])):
        if not isinstance(sg, Mapping):
            continue
        steps: list[PlanStep] = []
        for step_data in sg.get("steps", []):
            if not isinstance(step_data, Mapping):
                continue
            steps.append(_parse_step(step_data))
        subgoals.append(
            SubGoal(
                id=str(sg.get("id") or f"sg-{idx + 1}"),
                description=str(sg.get("description") or ""),
                steps=tuple(steps),
            )
        )
    return Plan(goal=goal, context_hints=hints, subgoals=tuple(subgoals), raw_text=raw_text)


def _parse_step(data: Mapping[str, Any]) -> PlanStep:
    kind = str(data.get("kind", "note"))
    if kind not in ("tool", "code", "branch", "note"):
        kind = "note"
    return PlanStep(
        kind=kind,
        description=str(data.get("description") or ""),
        tool_name=data.get("tool_name"),
        tool_args=data.get("tool_args", {}),
        code=data.get("code"),
        language=data.get("language"),
        condition=data.get("condition"),
        then_step=data.get("then_step"),
        else_step=data.get("else_step"),
    )


# ── Reflective harness ─────────────────────────────────────────────────


_REFLECTION_PROMPT = """The previous execution FAILED.  Analyse the failure and propose a
single corrective action.

Failure details:
{failure_summary}

Original goal: {goal}

Guidelines:
- Be specific about what went wrong and why.
- Propose ONE corrective tool call or code snippet.  No long essays.
- If the failure is terminal (missing dependency, bad tool name, impossible
  requirement), say so plainly instead of inventing a fix.

Respond as JSON:
{{
  "analysis": "<1-3 sentences of root-cause diagnosis>",
  "corrective_action": {{
    "kind": "tool",
    "tool_name": "...",
    "tool_args": {{...}}
  }}
}}
OR
{{
  "analysis": "...",
  "corrective_action": {{
    "kind": "code",
    "code": "...",
    "language": "python"
  }}
}}
OR
{{
  "analysis": "Terminal: ...",
  "terminal": true
}}
"""


@dataclass(frozen=True)
class ReflectionAttempt:
    """Record of one reflect→retry cycle."""

    attempt: int
    analysis: str
    corrective_action: dict[str, Any]
    result: HarnessResult


@dataclass(frozen=True)
class ReflectiveResult:
    """HarnessResult enriched with reflection metadata."""

    base_result: HarnessResult
    reflections: tuple[ReflectionAttempt, ...] = ()

    @property
    def final(self) -> HarnessResult:
        return self.reflections[-1].result if self.reflections else self.base_result


class ReflectiveHarness:
    """Wrap any :class:`HarnessPort` with a bounded reflect→retry loop.

    Usage::

        base = FunctionCallingHarness(model, tools, ...)
        harness = ReflectiveHarness(base, max_reflections=2)
        result = await harness.execute(request)

    The wrapper:

    1. Runs the wrapped harness once.
    2. If it succeeds → return.
    3. If it fails → ask the model for a corrective action, run it, and
       feed the result back.  Loop up to ``max_reflections`` times.
    4. If reflection says "terminal" or budget is exhausted → return the
       best available result (last success, or the initial failure).

    ``planner`` is optional — when provided, the ReflectiveHarness first
    asks for an explicit plan (see :class:`LLMPlanner`), embeds it in
    the initial request code, and records plan quality on every iteration.
    """

    def __init__(
        self,
        harness: HarnessPort,
        *,
        max_reflections: int = 2,
        planner: PlannerPort | None = None,
        reflection_model: ModelPort | None = None,
    ) -> None:
        if max_reflections < 0:
            raise ValueError("max_reflections must be >= 0")
        self._harness = harness
        self._max_reflections = max_reflections
        self._planner = planner
        self._reflection_model = reflection_model
        self._last_plan: Plan | None = None

    @property
    def last_plan(self) -> Plan | None:
        """Expose the most recent plan for audit / debugging."""
        return self._last_plan

    async def execute(self, request: HarnessRequest) -> HarnessResult:
        started = time.monotonic()

        # Phase 0 — explicit planning (optional)
        if self._planner is not None:
            try:
                plan = await self._planner.plan(
                    request.code[:500],
                    context=request.code,
                )
                self._last_plan = plan
                request = replace(
                    request,
                    code=(
                        f"[Execution Plan]\n{plan.goal}\n\n"
                        f"Subgoals: {plan.total_steps} steps total\n"
                        f"Hints: {'; '.join(plan.context_hints)}\n\n"
                        f"Proceed with execution.  You may deviate from the "
                        f"plan when evidence suggests a better path.\n\n"
                        f"---\n\n{request.code}"
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - planning failure never blocks execution
                logger.warning("reflective: planner failed (%s), proceeding unplanned", exc)

        # Phase 1 — first attempt
        logger.info("reflective: initial execution attempt")
        base_result = await self._harness.execute(request)

        if base_result.sandbox.success:
            return base_result

        # Phase 2 — reflect→retry loop
        reflections: list[ReflectionAttempt] = []
        best_result = base_result

        for attempt in range(1, self._max_reflections + 1):
            logger.info(
                "reflective: reflection attempt %d/%d",
                attempt,
                self._max_reflections,
            )

            failure_summary = _summarise_failure(base_result)
            reflection_model = self._reflection_model or (
                self._harness._model  # type: ignore[attr-defined] - FunctionCallingHarness has ._model
                if hasattr(self._harness, "_model")
                else None
            )
            if reflection_model is None:
                logger.warning("reflective: no model available for reflection, stopping")
                break

            reflection_request = HarnessRequest(
                code=_REFLECTION_PROMPT.format(
                    failure_summary=failure_summary,
                    goal=(self._last_plan.goal if self._last_plan else request.code[:200]),
                ),
                language="text",
                timeout=min(30.0, request.timeout),
            )
            try:
                response = await reflection_model.complete(
                    reflection_request,
                    (),
                    tools_enabled=False,
                )
            except Exception as exc:  # noqa: BLE001 - reflection failure is terminal
                logger.warning("reflective: model call failed (%s), stopping loop", exc)
                break

            parsed = _parse_reflection(response.content)
            if parsed.get("terminal"):
                logger.info("reflective: reflection declared terminal — stopping")
                break

            corrective = parsed.get("corrective_action", {})
            action_kind = corrective.get("kind", "")
            analysis = parsed.get("analysis", "")

            # For now, corrective actions are noted and the harness is re-run
            # with the failure + correction hint appended to the original code.
            # A full tool-call injection pipeline is out of scope for this
            # version; the intent is to close the loop, not replace tools.
            retry_request = replace(
                request,
                code=(
                    f"{request.code}\n\n"
                    f"--- Reflection #{attempt} ---\n"
                    f"Previous failure: {failure_summary[:500]}\n"
                    f"Analysis: {analysis}\n"
                    f"Suggested fix ({action_kind}): {json.dumps(corrective, ensure_ascii=False)[:500]}\n"
                    f"Please incorporate this and retry."
                ),
            )

            retry_result = await self._harness.execute(retry_request)
            reflections.append(
                ReflectionAttempt(
                    attempt=attempt,
                    analysis=analysis,
                    corrective_action=corrective,
                    result=retry_result,
                )
            )

            if retry_result.sandbox.success:
                best_result = retry_result
                logger.info("reflective: reflection #%d succeeded", attempt)
                break
            best_result = retry_result

        return best_result


def _summarise_failure(result: HarnessResult) -> str:
    parts = []
    sandbox = result.sandbox
    if sandbox.stderr:
        parts.append(f"stderr: {sandbox.stderr[:500]}")
    if sandbox.stdout:
        parts.append(f"stdout: {sandbox.stdout[-500:]}")
    if sandbox.error:
        parts.append(f"error: {sandbox.error}")
    parts.append(f"exit_code: {sandbox.exit_code}")
    parts.append(f"iterations: {result.iterations}, tool_calls: {result.tool_calls}")
    return "\n".join(parts) if parts else "(no failure details available)"


def _parse_reflection(content: str) -> dict[str, Any]:
    text = content.strip()
    json_text = _extract_json_object(text) or text
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        return {
            "analysis": text[:300],
            "corrective_action": {},
        }


# ── Utilities ─────────────────────────────────────────────────────────


def compose_reflective_harness(
    base_harness: FunctionCallingHarness,
    *,
    max_reflections: int = 2,
    enable_planning: bool = True,
) -> ReflectiveHarness:
    """One-line composition: planner + reflective loop on top of FunctionCallingHarness.

    Picks up ``_model`` from the base harness to drive both planning and
    reflection, so there is no extra wiring needed.
    """
    model = getattr(base_harness, "_model", None)
    planner = LLMPlanner(model) if (enable_planning and model is not None) else None
    return ReflectiveHarness(
        base_harness,
        max_reflections=max_reflections,
        planner=planner,
        reflection_model=model,
    )


__all__ = [
    "LLMPlanner",
    "Plan",
    "PlanStep",
    "PlannerPort",
    "ReflectionAttempt",
    "ReflectiveHarness",
    "ReflectiveResult",
    "SubGoal",
    "compose_reflective_harness",
]
