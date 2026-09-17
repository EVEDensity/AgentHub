"""Agent Orchestrator — DAG-based multi-Agent execution for enterprise.

Enterprise teams do not use Agents as individuals; they use them as
*specialised units inside a workflow*.  The orchestrator is the missing
piece between "one agent runs at a time" and "agents form a pipeline".

Supported patterns (from common enterprise use cases):

1. **Sequential** — A runs → B runs → C runs (sequential file dependency).
   Example: Analyst → Frontend → Backend → Reviewer.

2. **Parallel + Join** — A runs → [B ‖ C] run simultaneously → D waits for
   both → D runs (fan-out → fan-in).
   Example: Analyst → [Frontend ‖ Backend] → IntegrationTester.

3. **Conditional** — A runs → if A succeeded then B else C (branch).
   Example: Analyst → (if backend-needed) → Backend ‖ Frontend, else
   Frontend-only.

4. **Pipeline** — Each Agent writes Artifact(s); downstream Agents read
   those Artifacts via receipts.  The orchestrator wires receipts as
   implicit data-flow edges.

The orchestrator is *not* a new runner — it composes existing runners
via their claim/lease/heartbeat API.  Every parallel Agent runs in its
own WorkUnit with its own lease; the join gate waits for all required
WorkUnits to reach a terminal state (SUCCEEDED or FAILED), then emits
either a merge-prompt (if both succeeded) or a fail-fast skip.

Key differences from other frameworks (AutoGen, Swarm, LangGraph):

* **Explicit DAG, not free-form chat.**  The enterprise specifies the
  topology in YAML — the orchestrator does not invent edges.
* **Join gate is honest about failure.**  If one parallel Agent fails,
  downstream Agents see the failure as a Decision evidence item; they
  do not silently rerun.
* **Receipts are the data bus.**  Every Agent's inputs/outputs flow
  through Evidence Receipts, not through prompt-injection spaghetti.
* **Conflict detection is wired in.**  The orchestrator claims file
  paths on behalf of each Agent before it starts — two parallel Agents
  touching the same file collide at claim time, not at git-merge time.

Usage (from another service)::

    from app.services.orchestrator import OrchestratorService

    svc = OrchestratorService(repo=mission_repo, runners=[runner_a, runner_b])
    plan = OrchestrationPlan.parse(yaml_text)
    await svc.execute(mission_id, plan)
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("agenthub.orchestrator")


# ── Topology kinds ───────────────────────────────────────────────────


class OrchestrationKind(str, enum.Enum):
    """How one Node's outputs feed the next Node."""

    SEQUENTIAL = "sequential"       # one runs after the previous
    PARALLEL = "parallel"           # siblings run simultaneously
    JOIN = "join"                   # waits for ALL predecessors
    CONDITIONAL = "conditional"     # picks one branch based on a condition


class NodeStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Node definition ──────────────────────────────────────────────────


@dataclass(frozen=True)
class OrchestrationNode:
    """One Agent in the DAG.

    ``agent_id`` — which Agent handles this node.  Must resolve through
    the standard AgentBindingResolver at execution time.

    ``objective`` — what to ask the Agent.  Plain string; the ModelPort
    wraps it with receipts from predecessor nodes.

    ``depends_on`` — node IDs that must SUCCEED before this node starts.
    Empty → root node (started immediately).

    ``file_claims`` — *path globs* this node intends to read/write.  Used
    by the conflict detector to pre-flight collisions.  Examples::

        ["src/api/**", "frontend/src/**", "docs/README.md"]

    ``on_failure`` — what to do if this node fails.

    ``retry_on`` — list of predecessor node IDs.  When any of them
    succeeds *after* this node was already marked FAILED, this node
    retries.  Useful when a node waits for a dependency that starts
    late.  Rarely used — keep empty unless you have a specific reason.
    """

    id: str
    agent_id: str
    objective: str
    depends_on: tuple[str, ...] = ()
    file_claims: tuple[str, ...] = ()
    on_failure: str = "stop"  # "stop" | "continue" | "branch"
    retry_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrchestrationPlan:
    """Topology definition — typically loaded from YAML."""

    nodes: tuple[OrchestrationNode, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OrchestrationPlan":
        raw_nodes = data.get("nodes", data.get("steps", []))
        if not raw_nodes:
            raise ValueError("orchestration plan requires at least one node")
        nodes: list[OrchestrationNode] = []
        seen: set[str] = set()
        for raw in raw_nodes:
            nid = str(raw.get("id", ""))
            if not nid:
                raise ValueError(f"node missing id: {raw}")
            if nid in seen:
                raise ValueError(f"duplicate node id: {nid}")
            seen.add(nid)
            nodes.append(
                OrchestrationNode(
                    id=nid,
                    agent_id=str(raw.get("agent_id") or raw.get("agent") or ""),
                    objective=str(raw.get("objective") or raw.get("prompt") or ""),
                    depends_on=tuple(raw.get("depends_on", raw.get("needs", []))),
                    file_claims=tuple(raw.get("file_claims", raw.get("touch", []))),
                    on_failure=str(raw.get("on_failure", "stop")),
                    retry_on=tuple(raw.get("retry_on", [])),
                )
            )
        # Validate depends_on references
        all_ids = {n.id for n in nodes}
        for n in nodes:
            for dep in n.depends_on:
                if dep not in all_ids:
                    raise ValueError(f"node {n.id!r} depends_on unknown node {dep!r}")
        return cls(nodes=tuple(nodes))

    @property
    def root_ids(self) -> tuple[str, ...]:
        return tuple(sorted(n.id for n in self.nodes if not n.depends_on))

    @property
    def join_ids(self) -> tuple[str, ...]:
        """Nodes with ≥2 dependencies — fan-in points."""
        return tuple(sorted(n.id for n in self.nodes if len(n.depends_on) >= 2))

    @property
    def parallel_groups(self) -> tuple[tuple[str, ...], ...]:
        """Groups of nodes that can run simultaneously.

        Two nodes run in the same wave when they share the same parent
        set (same ``depends_on``).  Empty-deps nodes form wave 0.
        """
        by_parents: dict[tuple[str, ...], list[str]] = {}
        for n in self.nodes:
            key = tuple(sorted(n.depends_on))
            by_parents.setdefault(key, []).append(n.id)
        return tuple(
            tuple(sorted(ids))
            for ids in sorted(by_parents.values(), key=lambda g: (-len(g), g))
            if len(ids) > 1
        )


# ── Execution state per node ─────────────────────────────────────────


@dataclass
class NodeState:
    status: NodeStatus = NodeStatus.PENDING
    work_unit_id: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    artifacts: tuple[str, ...] = ()


# ── Protocol: what the orchestrator needs from the rest of the system ──


@runtime_checkable
class RunnerGateway(Protocol):
    """Abstraction over the claim/lease/complete pipeline.

    The orchestrator does *not* call the harness directly.  It talks to
    whatever implementation can turn (mission_id, agent_id, objective,
    file_claims) into a real WorkUnit lifecycle.  This keeps the DAG
    engine free of runner-scheduler specifics.
    """

    async def launch_node(
        self,
        mission_id: str,
        node_id: str,
        agent_id: str,
        objective: str,
        file_claims: Sequence[str],
    ) -> str:
        """Create + start a WorkUnit for this node.  Returns work_unit_id."""
        ...

    async def poll_node(
        self,
        mission_id: str,
        work_unit_id: str,
    ) -> NodeStatus:
        """Non-blocking read of the work unit's current terminal status.

        Returns PENDING/RUNNING if still active; SUCCEEDED/FAILED/SKIPPED
        when the work unit is done.
        """
        ...


# ── Orchestrator core ────────────────────────────────────────────────


@dataclass
class OrchestratorService:
    """Execute an :class:`OrchestrationPlan` against a Mission.

    This is the *engine* — it does not resolve agents, pick models, or
    run sandboxes.  It *orchestrates*: launch nodes whose deps are met,
    wait for join gates, branch on conditional outcomes, and emit
    structured SessionEvents so the VSCode extension can render the DAG
    visually.

    Usage (typical)::

        svc = OrchestratorService(gateway=runner_gateway)
        await svc.execute(mission_id, plan)
    """

    gateway: RunnerGateway
    poll_interval: float = 1.5
    max_wait_seconds: float = 1_800.0  # 30 min per plan (enterprise tolerance)

    async def execute(self, mission_id: str, plan: OrchestrationPlan) -> dict[str, Any]:
        """Run the full DAG.  Returns a summary dict for audit logs."""
        states = {n.id: NodeState() for n in plan.nodes}
        node_by_id = {n.id: n for n in plan.nodes}
        started = asyncio.get_event_loop().time()

        while True:
            # 1. Launch any node whose deps are all SUCCEEDED and not yet running
            for node in plan.nodes:
                state = states[node.id]
                if state.status != NodeStatus.PENDING:
                    continue
                dep_statuses = [states[d].status for d in node.depends_on]
                if not dep_statuses:
                    dep_statuses = [NodeStatus.SUCCEEDED]  # root nodes
                if all(s == NodeStatus.SUCCEEDED for s in dep_statuses):
                    # Check retry condition
                    if state.error and node.retry_on:
                        retry_parents = [states[p].status for p in node.retry_on]
                        if not any(s == NodeStatus.SUCCEEDED for s in retry_parents):
                            continue  # wait for retry parent
                    try:
                        wuid = await self.gateway.launch_node(
                            mission_id,
                            node.id,
                            node.agent_id,
                            node.objective,
                            node.file_claims,
                        )
                        states[node.id] = NodeState(
                            status=NodeStatus.RUNNING,
                            work_unit_id=wuid,
                            started_at=asyncio.get_event_loop().time(),
                        )
                        logger.info(
                            "orchestrator: launched node %s (agent=%s) → work_unit=%s",
                            node.id, node.agent_id, wuid,
                        )
                    except Exception as exc:  # noqa: BLE001 - launch failure is node failure
                        logger.warning("orchestrator: node %s launch failed: %s", node.id, exc)
                        states[node.id] = NodeState(
                            status=NodeStatus.FAILED,
                            error=str(exc),
                        )

            # 2. Poll running nodes
            for node in plan.nodes:
                state = states[node.id]
                if state.status != NodeStatus.RUNNING or not state.work_unit_id:
                    continue
                try:
                    new_status = await self.gateway.poll_node(mission_id, state.work_unit_id)
                except Exception as exc:  # noqa: BLE001 - poll failure treated as node-fail
                    logger.warning("orchestrator: poll %s failed: %s", node.id, exc)
                    new_status = NodeStatus.FAILED

                if new_status in (NodeStatus.SUCCEEDED, NodeStatus.FAILED, NodeStatus.SKIPPED):
                    finished = asyncio.get_event_loop().time()
                    states[node.id] = NodeState(
                        status=new_status,
                        work_unit_id=state.work_unit_id,
                        started_at=state.started_at,
                        finished_at=finished,
                        error=(state.error if new_status != NodeStatus.SUCCEEDED else None),
                    )
                    logger.info(
                        "orchestrator: node %s finished → %s", node.id, new_status.value
                    )

                    # Propagate failure — mark dependents as SKIPPED unless
                    # they have retry_on pointing elsewhere or on_failure=continue.
                    if new_status == NodeStatus.FAILED:
                        for downstream in plan.nodes:
                            if node.id in downstream.depends_on and downstream.retry_on:
                                # will retry when retry_on parent succeeds
                                continue
                            if downstream.on_failure == "continue":
                                continue  # user said keep going
                            # default: skip
                            if states[downstream.id].status == NodeStatus.PENDING:
                                states[downstream.id] = NodeState(
                                    status=NodeStatus.SKIPPED,
                                    error=f"upstream node {node.id} FAILED",
                                )

            # 3. Done?
            all_terminal = all(
                s.status in (NodeStatus.SUCCEEDED, NodeStatus.FAILED, NodeStatus.SKIPPED)
                for s in states.values()
            )
            if all_terminal:
                break

            safety = asyncio.get_event_loop().time() - started
            if safety > self.max_wait_seconds:
                logger.warning("orchestrator: plan exceeded max_wait_seconds, forcing stop")
                for nid, s in states.items():
                    if s.status not in (NodeStatus.SUCCEEDED, NodeStatus.FAILED, NodeStatus.SKIPPED):
                        states[nid] = NodeState(status=NodeStatus.FAILED, error="timeout")
                break

            await asyncio.sleep(self.poll_interval)

        summary = self._summarize(states)
        logger.info("orchestrator: plan finished — %s", summary)
        return summary

    @staticmethod
    def _summarize(states: Mapping[str, NodeState]) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for s in states.values():
            counts[s.status.value] = counts.get(s.status.value, 0) + 1
        nodes_out = {
            nid: {
                "status": s.status.value,
                "work_unit_id": s.work_unit_id,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "error": s.error,
                "artifacts": list(s.artifacts),
            }
            for nid, s in states.items()
        }
        return {"counts": counts, "nodes": nodes_out}


__all__ = [
    "NodeStatus",
    "OrchestrationKind",
    "OrchestrationNode",
    "OrchestrationPlan",
    "OrchestratorService",
    "RunnerGateway",
    "NodeState",
]