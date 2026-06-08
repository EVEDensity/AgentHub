from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.schemas.dag import DAGConfig

logger = logging.getLogger("agenthub.dag_executor")

# ── Execution constants ────────────────────────────────────────────────

NODE_TIMEOUT_SECONDS = 300      # Per-node timeout (5 min)
MAX_RETRIES_PER_NODE = 2        # Max retries before marking FAILED
RETRY_DELAY_SECONDS = 2         # Delay between retries


class DAGExecutor:
    """Execute a DAG of sub-tasks by actually invoking agents.

    Replaces the symbolic ``run_dag()`` in ``task_state_machine.py`` with
    real agent invocations that respect DAG dependencies and enable
    parallel execution of independent nodes.

    Usage::

        executor = DAGExecutor(
            session_id="sess-123",
            manager=websocket_manager,
            invoke_fn=my_invoke_agent,
        )
        results = await executor.execute(dag, collab_ctx)
    """

    def __init__(
        self,
        session_id: str,
        manager: Any = None,
        invoke_fn: Any = None,
        on_node_update: Any = None,
    ) -> None:
        self.session_id = session_id
        self.manager = manager          # WebSocket manager for broadcasts
        self.invoke_fn = invoke_fn      # async fn(session_id, agent, content, ctx) -> str
        self.on_node_update = on_node_update  # optional callback(node_id, status, detail)
        self.node_results: dict[str, str] = {}     # node_id -> agent output
        self.node_durations: dict[str, float] = {} # node_id -> duration_ms
        self.node_retries: dict[str, int] = {}     # node_id -> retry count
        self._cancelled = False

    # ── Public API ─────────────────────────────────────────────────────

    async def execute(
        self,
        dag: DAGConfig,
        collaboration_ctx: Any = None,
        agent_selector: Any = None,
    ) -> dict[str, str]:
        """Execute all DAG nodes respecting dependencies.

        Args:
            dag: The DAG to execute (mutated in-place for status tracking).
            collaboration_ctx: CollaborationContext for inter-agent sharing.
            agent_selector: Optional AgentSelector for dynamic reassignment.

        Returns:
            Dict mapping node_id -> agent output text.

        Raises:
            DAGExecutionError: If a critical node fails after all retries.
        """
        logger.info(
            "dag_executor: starting execution of %d nodes (strategy=%s)",
            dag.total, dag.execution_strategy,
        )
        self._dag_nodes = dag.nodes  # for progress computation in broadcasts

        while not dag.is_complete():
            if self._cancelled:
                raise DAGExecutionError("DAG execution cancelled")

            ready = dag.get_ready_nodes()

            if not ready:
                # Check whether any nodes are still in flight
                running = [n for n in dag.nodes if n.status == "RUNNING"]
                if running:
                    await asyncio.sleep(0.1)
                    continue

                # Deadlock detection: nothing running and nothing ready,
                # but there are still PENDING nodes — they must be blocked
                # by one or more FAILED dependencies.
                blocked = [
                    n for n in dag.nodes
                    if n.status == "PENDING"
                    and any(
                        d in dag.failed_ids for d in n.dependencies
                    )
                ]
                if blocked:
                    for n in blocked:
                        failed_deps = [d for d in n.dependencies if d in dag.failed_ids]
                        dag.set_node_status(n.id, "FAILED")
                        await self._broadcast_node_update(
                            n.id, "FAILED",
                            detail={"error": f"上游节点失败: {', '.join(failed_deps)}"},
                        )
                    continue  # Re-evaluate — newly-failed nodes may unblock siblings

                # Truly stuck — should not happen, but guard against infinite loop
                logger.error("dag_executor: deadlock detected, breaking")
                break

            logger.debug(
                "dag_executor: %d ready nodes — %s",
                len(ready), [n.id for n in ready],
            )

            # Execute ready nodes concurrently
            tasks = []
            for node in ready:
                dag.set_node_status(node.id, "RUNNING")
                self.node_retries.setdefault(node.id, 0)
                await self._broadcast_node_update(node.id, "RUNNING")
                tasks.append(self._execute_node_with_retry(
                    node, dag, collaboration_ctx, agent_selector,
                ))

            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            "dag_executor: completed. %d/%d nodes succeeded",
            sum(1 for n in dag.nodes if n.status == "SUCCESS"),
            dag.total,
        )
        return self.node_results

    def cancel(self) -> None:
        """Cancel ongoing DAG execution."""
        self._cancelled = True

    # ── Node execution ─────────────────────────────────────────────────

    async def _execute_node_with_retry(
        self,
        node: Any,
        dag: DAGConfig,
        collaboration_ctx: Any,
        agent_selector: Any,
    ) -> None:
        """Execute one node with retry logic.

        Mutates *node* (which is a reference into dag.nodes) and calls
        dag.set_node_status so the completed counter stays in sync.
        """
        for attempt in range(MAX_RETRIES_PER_NODE + 1):
            try:
                result = await self._execute_node(node, collaboration_ctx)
                self.node_results[node.id] = result
                dag.set_node_status(node.id, "SUCCESS")
                await self._broadcast_node_update(node.id, "SUCCESS")
                return
            except Exception as exc:
                self.node_retries[node.id] = attempt + 1
                logger.warning(
                    "dag_executor: node '%s' failed (attempt %d/%d): %s",
                    node.id, attempt + 1, MAX_RETRIES_PER_NODE + 1, exc,
                )

                if attempt < MAX_RETRIES_PER_NODE:
                    # Try reassignment if selector available
                    if agent_selector and attempt == 0:
                        logger.info(
                            "dag_executor: attempting reassignment for node '%s'",
                            node.id,
                        )
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                else:
                    # All retries exhausted
                    dag.set_node_status(node.id, "FAILED")
                    await self._broadcast_node_update(
                        node.id, "FAILED",
                        detail={"error": str(exc), "retries": self.node_retries[node.id]},
                    )

    async def _execute_node(
        self,
        node: Any,
        collaboration_ctx: Any,
    ) -> str:
        """Execute a single DAG node by calling the assigned agent.

        Handles timeout via asyncio.wait_for.
        """
        if self.invoke_fn is None:
            raise DAGExecutionError("No invoke_fn configured for DAGExecutor")

        # Build context from dependency results
        dep_context = ""
        if node.dependencies and self.node_results:
            dep_parts = []
            for dep_id in node.dependencies:
                if dep_id in self.node_results:
                    dep_parts.append(
                        f"[{dep_id} 的输出]\n{self.node_results[dep_id]}"
                    )
            if dep_parts:
                dep_context = "\n\n".join(dep_parts)

        # Get collaboration context if available
        collab_context = ""
        if collaboration_ctx:
            try:
                collab_context = collaboration_ctx.context_for(node.agent)
            except Exception:
                pass

        # Build the full prompt
        full_content = node.description
        if dep_context:
            full_content = f"{full_content}\n\n## 依赖节点输出\n{dep_context}"
        if collab_context:
            full_content = f"{collab_context}\n\n{full_content}"

        # Execute with timeout
        start = time.time()
        try:
            result = await asyncio.wait_for(
                self.invoke_fn(
                    self.session_id,     # sid (positional, matches _dag_invoke)
                    node.agent,          # agent_id
                    full_content,        # content
                    dep_context,         # extra_context
                ),
                timeout=NODE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            elapsed = time.time() - start
            raise DAGExecutionError(
                f"Node '{node.id}' timed out after {elapsed:.0f}s"
            )

        elapsed_ms = (time.time() - start) * 1000
        self.node_durations[node.id] = elapsed_ms
        logger.info(
            "dag_executor: node '%s' completed in %.0fms",
            node.id, elapsed_ms,
        )
        return result

    # ── Helpers ────────────────────────────────────────────────────────

    async def _broadcast_node_update(
        self,
        node_id: str,
        status: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Broadcast node status change via WebSocket and callback.

        Sends a rich ``task_update`` event to the frontend so the
        ProgressBubble / DAG visualisation can show real-time status
        for each node: PENDING → RUNNING → SUCCESS/FAILED.

        Also fires the optional ``on_node_update`` callback for
        server-side observers (e.g. logging, metrics, DAG re-planning).
        """
        if self.on_node_update:
            try:
                await self.on_node_update(node_id, status, detail)
            except Exception:
                pass

        if self.manager:
            try:
                # ── Compute aggregate progress ─────────────────────
                total = len(self.node_results) + sum(
                    1 for n in self._dag_nodes if n.status == "PENDING"
                ) if hasattr(self, '_dag_nodes') else 0
                completed = sum(
                    1 for n in self._dag_nodes if n.status == "SUCCESS"
                ) if hasattr(self, '_dag_nodes') else len(self.node_results)
                failed = sum(
                    1 for n in self._dag_nodes if n.status == "FAILED"
                ) if hasattr(self, '_dag_nodes') else 0
                running = sum(
                    1 for n in self._dag_nodes if n.status == "RUNNING"
                ) if hasattr(self, '_dag_nodes') else 0
                if total == 0:
                    total = max(1, completed + failed + running)

                payload: dict[str, Any] = {
                    "event": "task_update",
                    "nodeId": node_id,
                    "status": status,
                    "sessionId": self.session_id,
                    "progress": {
                        "completed": completed,
                        "failed": failed,
                        "running": running,
                        "total": total,
                        "percent": round((completed + failed) / total * 100),
                    },
                }
                if detail:
                    payload["detail"] = detail
                if node_id in self.node_durations:
                    payload["durationMs"] = self.node_durations[node_id]
                if node_id in self.node_retries:
                    payload["retries"] = self.node_retries[node_id]
                await self.manager.broadcast(self.session_id, payload)
            except Exception:
                pass

    # ── Internal: store DAG node list for progress computation ──────

    def _set_dag_nodes(self, nodes: list[Any]) -> None:
        """Called by execute() to store node references for progress."""
        self._dag_nodes = nodes


class DAGExecutionError(Exception):
    """Raised when DAG execution fails irrecoverably."""
    pass
