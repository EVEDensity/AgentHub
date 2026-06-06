from __future__ import annotations

import json
import logging
from typing import Any

from app.db.session import afetch_all

logger = logging.getLogger("agenthub.agent_selector")

# ── Selection weights for the multi-dimensional scoring model ──────────

SELECTION_WEIGHTS = {
    "capability_match": 0.30,    # domain + capability_tags 匹配度
    "success_rate": 0.30,        # task_execution_history 成功率
    "response_time": 0.15,       # EMA 延迟（复用 _RUNTIME 数据）
    "availability": 0.10,        # agent status (online/offline/sleeping)
    "load_balance": 0.10,        # 当前活跃任务数
    "risk_match": 0.05,          # 任务风险等级匹配
}


class AgentSelector:
    """Multi-dimensional agent selection engine.

    Ranks candidate agents for a given sub-task by combining capability
    match, historical success rate, latency, availability, and load.

    Usage::

        selector = AgentSelector()
        best_agent, confidence = await selector.select(
            task_description="生成React登录组件",
            task_type="frontend",
            candidates=available_agents,
            risk_level="L2",
        )
    """

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or dict(SELECTION_WEIGHTS)

    # ── Public API ─────────────────────────────────────────────────────

    async def select(
        self,
        task_description: str,
        task_type: str,
        candidates: list[dict[str, Any]],
        risk_level: str = "L1",
    ) -> tuple[str | None, float]:
        """Select the best agent for a task.

        Args:
            task_description: Human-readable task description.
            task_type: Classified task type (frontend/backend/fullstack/...).
            candidates: List of agent dicts from agent_registry.
            risk_level: Required risk level for this task.

        Returns:
            Tuple of (agent_id, confidence_score) where confidence is 0.0-1.0.
            Returns (None, 0.0) if no suitable agent found.
        """
        if not candidates:
            return None, 0.0

        # Filter by availability
        available = [a for a in candidates if a.get("status") != "offline"]
        if not available:
            # Last resort: include offline agents with a penalty
            available = candidates

        # Score each candidate across all dimensions
        scores: list[tuple[str, float]] = []
        for agent in available:
            score = await self._score_agent(agent, task_description, task_type, risk_level)
            scores.append((agent["agent_id"], score))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        if not scores:
            return None, 0.0

        best_id, best_score = scores[0]
        # Normalize to 0-1 range
        confidence = min(1.0, max(0.0, best_score))

        logger.info(
            "agent_selector: selected '%s' with confidence %.2f (task_type=%s, candidates=%d)",
            best_id, confidence, task_type, len(candidates),
        )

        return best_id, confidence

    async def select_top_k(
        self,
        task_description: str,
        task_type: str,
        candidates: list[dict[str, Any]],
        k: int = 3,
        risk_level: str = "L1",
    ) -> list[tuple[str, float]]:
        """Return top-k ranked agents with scores."""
        if not candidates:
            return []

        scored = []
        for agent in candidates:
            score = await self._score_agent(agent, task_description, task_type, risk_level)
            scored.append((agent["agent_id"], min(1.0, max(0.0, score))))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    # ── Scoring dimensions ─────────────────────────────────────────────

    async def _score_agent(
        self,
        agent: dict[str, Any],
        task_description: str,
        task_type: str,
        risk_level: str,
    ) -> float:
        """Compute weighted multi-dimensional score for one agent."""
        agent_id = agent.get("agent_id", "unknown")

        dimensions = {
            "capability_match": self._score_capability(agent, task_description),
            "success_rate": await self._score_success_rate(agent_id, task_type),
            "response_time": self._score_response_time(agent),
            "availability": self._score_availability(agent),
            "load_balance": self._score_load(agent_id),
            "risk_match": self._score_risk_match(agent, risk_level),
        }

        total = sum(
            self.weights[dim] * score
            for dim, score in dimensions.items()
        )

        return total

    # ── Dimension scorers ──────────────────────────────────────────────

    @staticmethod
    def _score_capability(agent: dict[str, Any], task: str) -> float:
        """Score based on domain + capability_tags + duty_note keyword overlap."""
        domain = (agent.get("domain") or "").lower()
        tags_raw = agent.get("capability_tags", "[]")
        duty = (agent.get("duty_note") or "").lower()
        task_lower = task.lower()

        try:
            tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        except (json.JSONDecodeError, TypeError):
            tags = []

        # Count keyword hits
        hits = 0
        total_keywords = 0

        # Check domain keywords
        domain_keywords = {
            "architect": ["架构", "设计", "方案", "技术选型", "architecture"],
            "codegen": ["代码", "生成", "实现", "开发", "编写", "code", "implement"],
            "review": ["审查", "review", "检查", "审计", "安全"],
            "test": ["测试", "test", "验证", "用例"],
            "deploy": ["部署", "deploy", "发布", "上线", "环境"],
            "orchestrator": ["协调", "调度", "分配", "编排"],
        }

        kw = domain_keywords.get(domain, [])
        total_keywords += len(kw)
        hits += sum(1 for k in kw if k in task_lower)

        # Check capability_tags
        for tag in tags:
            tag_lower = tag.lower()
            total_keywords += 1
            if any(word in task_lower for word in tag_lower.split()):
                hits += 1

        # Check duty_note keywords
        duty_words = [w for w in duty.split() if len(w) >= 2]
        total_keywords += min(len(duty_words), 10)
        hits += sum(1 for w in duty_words[:10] if w in task_lower)

        if total_keywords == 0:
            return 0.5  # Neutral score
        return min(1.0, hits / max(1, total_keywords))

    @staticmethod
    async def _score_success_rate(agent_id: str, task_type: str) -> float:
        """Score based on historical success rate from task_execution_history."""
        try:
            rows = await afetch_all(
                """SELECT SUM(CASE WHEN success THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as rate
                   FROM task_execution_history
                   WHERE assigned_agent = $1 AND task_type = $2""",
                agent_id, task_type,
            )
            if rows and rows[0]["rate"] is not None:
                return float(rows[0]["rate"])
        except Exception:
            pass

        # No history: neutral score with slight optimism
        return 0.60

    @staticmethod
    def _score_response_time(agent: dict[str, Any]) -> float:
        """Score based on EMA latency from the runtime tracker."""
        try:
            from app.services.agent_service import _RUNTIME

            adapter = agent.get("adapter_type", "")
            model = agent.get("base_model_name", "")
            base_url = agent.get("base_url", "")
            key = f"{adapter}:{model}:{base_url}"
            state = _RUNTIME.get(key)
            if state and state.get("latency", 0) > 0:
                latency = state["latency"]
                # Score: 1000ms = 1.0, 10000ms = 0.1
                return max(0.1, min(1.0, 1000.0 / max(100.0, latency)))
        except Exception:
            pass
        return 0.50  # Unknown latency

    @staticmethod
    def _score_availability(agent: dict[str, Any]) -> float:
        """Score based on agent status."""
        status = agent.get("status", "sleeping")
        return {"online": 1.0, "sleeping": 0.7, "offline": 0.15}.get(status, 0.5)

    @staticmethod
    def _score_load(agent_id: str) -> float:
        """Score inversely proportional to current task load."""
        # Simple heuristic: count active tasks in the session
        # In a full implementation this would query a task queue
        try:
            from app.services.agent_service import _RUNTIME

            # Use runtime state as a proxy for load
            # Higher ok count = more experienced (slightly better score)
            total_calls = 0
            for key, state in _RUNTIME.items():
                if agent_id.lower() in key.lower():
                    total_calls += state.get("ok", 0) + state.get("fail", 0)

            if total_calls == 0:
                return 0.60
            # Lightly penalize very busy agents, but not heavily
            return max(0.3, 1.0 - total_calls * 0.005)
        except Exception:
            pass
        return 0.50

    @staticmethod
    def _score_risk_match(agent: dict[str, Any], task_risk: str) -> float:
        """Score based on risk level compatibility."""
        agent_risk = agent.get("risk_level", "L1")
        risk_levels = {"L1": 1, "L2": 2, "L3": 3}
        agent_r = risk_levels.get(agent_risk, 1)
        task_r = risk_levels.get(task_risk, 1)

        # Prefer agents whose risk level >= task risk level
        # (higher-risk agents can handle lower-risk tasks, but not vice versa)
        if agent_r >= task_r:
            return 1.0
        else:
            return max(0.1, agent_r / max(1, task_r))


# ── Module-level singleton ────────────────────────────────────────────

agent_selector = AgentSelector()
