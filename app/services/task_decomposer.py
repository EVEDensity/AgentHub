from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.db.session import afetch_all
from app.schemas.dag import DAGConfig, DAGNode
from app.services.template_engine import template_engine

logger = logging.getLogger("agenthub.task_decomposer")

# ── Prompt template for LLM-driven task decomposition ──────────────────

DECOMPOSE_SYSTEM = """你是一个资深技术项目经理（PM），负责将用户需求拆解为可执行的子任务DAG。

## 你的职责
1. **分析用户意图**：判断需求类型（前端开发 / 后端API / 全栈功能 / bug修复 / 架构设计 / 部署 / 测试）
2. **拆解子任务**：将复杂需求分解为3-7个独立的子任务节点
3. **确定执行顺序**：标注节点间的依赖关系（id和dependencies字段）

## 可用Agent及其能力
{agent_capabilities}

## 输出格式
严格的JSON对象（不要markdown代码块），字段说明：
- analysis: 对用户需求的一句话分析（中文）
- execution_strategy: "sequential"（顺序执行）| "parallel"（并行）| "mixed"（混合）
- nodes: 子任务列表，每个包含：
  - id: "node_1", "node_2" ... 唯一标识
  - domain: agent的domain字段（如architect/codegen/review/test/deploy）
  - agent: agent的agent_id（如Architect/CodeGen/Review/Test/Deploy）
  - description: 子任务描述（中文，简洁明确）
  - dependencies: 依赖的node id列表（空数组表示可立即执行）
  - priority: 1（高）/2（中）/3（低）
  - estimated_effort: "low" / "medium" / "high"

## 规则
1. Architect节点通常无依赖，作为第一个节点
2. CodeGen依赖Architect完成后才能开始
3. Review和Test可以依赖CodeGen后并行执行
4. Deploy依赖Review和Test都完成后才能开始
5. 简单需求可以用2-3个节点，复杂需求用5-7个节点
6. 只生成真正需要的节点，不要为了凑数而加节点
7. **不要输出任何解释文字，只输出JSON**"""


class ArchitectTaskDecomposer:
    """LLM-driven task decomposition engine.

    Uses the Architect agent's LLM to analyze user intent and produce
    a structured DAG of sub-tasks. Falls back to keyword-based template
    matching when the LLM response cannot be parsed.

    Usage::

        decomposer = ArchitectTaskDecomposer()
        dag = await decomposer.decompose(
            content="帮我开发用户登录页面",
            session_id="sess-123",
            agents=available_agents,
        )
    """

    # Max tokens for the decomposition response (compact JSON)
    MAX_DECOMPOSE_TOKENS = 2000
    # Timeout for the LLM decomposition call
    DECOMPOSE_TIMEOUT = 30.0

    def __init__(self) -> None:
        self._agent_capability_cache: str | None = None
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0  # 5 min

    # ── Public API ─────────────────────────────────────────────────────

    async def decompose(
        self,
        content: str,
        session_id: str,
        agents: list[dict[str, Any]],
        history: list[dict[str, Any]] | None = None,
    ) -> DAGConfig:
        """Decompose a user request into a DAG of sub-tasks.

        Args:
            content: The user's original message text.
            session_id: Current session ID for logging/context.
            agents: List of agent dicts from agent_registry.
            history: Optional conversation history for context.

        Returns:
            A validated DAGConfig ready for execution.
        """
        # Try LLM-based decomposition first
        try:
            dag = await self._llm_decompose(content, session_id, agents, history)
            if dag and dag.nodes:
                template_engine.validate(dag)
                logger.info(
                    "task_decomposer: LLM decomposition produced %d nodes (strategy=%s)",
                    len(dag.nodes), dag.execution_strategy,
                )
                return dag
        except Exception as exc:
            logger.warning(
                "task_decomposer: LLM decomposition failed (%s), falling back to template",
                exc,
            )

        # Fallback to keyword-based template matching
        dag, _ = await template_engine.match_template(content)
        logger.info(
            "task_decomposer: fallback template matched %d nodes",
            len(dag.nodes),
        )
        return dag

    # ── LLM-driven decomposition ───────────────────────────────────────

    async def _llm_decompose(
        self,
        content: str,
        session_id: str,
        agents: list[dict[str, Any]],
        history: list[dict[str, Any]] | None,
    ) -> DAGConfig | None:
        """Call the Architect LLM to produce a DAG from user intent."""
        # Build the agent capability summary for the prompt
        capabilities = await self._build_capability_summary(agents)

        # Build the user prompt
        prompt = DECOMPOSE_SYSTEM.format(agent_capabilities=capabilities)

        # Add historical context if available
        hist_context = await self._build_historical_context(content)
        if hist_context:
            prompt += f"\n\n## 历史参考数据\n{hist_context}"

        prompt += f"\n\n## 用户需求\n{content}"

        # Resolve the Architect agent for model selection
        architect = self._find_architect(agents)
        if not architect:
            return None

        # Call the Architect's LLM
        raw_response = await self._call_llm(architect, prompt)
        if not raw_response:
            return None

        # Parse the JSON response into a DAGConfig
        return self._parse_response(raw_response, content)

    # ── Internal helpers ───────────────────────────────────────────────

    async def _build_capability_summary(self, agents: list[dict[str, Any]]) -> str:
        """Build a concise agent capability description for the prompt."""
        lines: list[str] = []
        for a in agents:
            agent_id = a.get("agent_id", "unknown")
            domain = a.get("domain", "unknown")
            duty = a.get("duty_note", "")
            risk = a.get("risk_level", "L1")
            status = a.get("status", "sleeping")
            tags_raw = a.get("capability_tags", "[]")
            try:
                tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
            except (json.JSONDecodeError, TypeError):
                tags = []
            tag_str = ", ".join(tags[:6]) if tags else "无"

            status_icon = {"online": "✓", "sleeping": "~", "offline": "✗"}.get(status, "?")
            lines.append(
                f"- **{agent_id}** [{status_icon}] domain={domain}, risk={risk}\n"
                f"  职责: {duty}\n  能力标签: {tag_str}"
            )
        return "\n".join(lines)

    async def _build_historical_context(self, content: str) -> str | None:
        """Query task_execution_history for relevant past performance."""
        try:
            # Simple keyword extraction for task type matching
            task_type = self._classify_task_type(content)
            rows = await afetch_all(
                """SELECT assigned_agent,
                          SUM(CASE WHEN success THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as success_rate,
                          AVG(duration_ms)::INTEGER as avg_duration_ms,
                          COUNT(*) as total_runs
                   FROM task_execution_history
                   WHERE task_type = $1
                   GROUP BY assigned_agent
                   ORDER BY success_rate DESC""",
                task_type,
            )
            if not rows:
                return None
            lines = [f"任务类型 '{task_type}' 的历史执行数据:"]
            for r in rows[:10]:
                lines.append(
                    f"  {r['assigned_agent']}: 成功率={r['success_rate']:.0%}, "
                    f"平均耗时={r['avg_duration_ms']}ms, 总次数={r['total_runs']}"
                )
            return "\n".join(lines)
        except Exception:
            return None

    def _find_architect(self, agents: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Find the Architect agent from the agent list."""
        for a in agents:
            if a.get("agent_id") == "Architect":
                return a
        # Fallback: any agent with domain=architect
        for a in agents:
            if a.get("domain") == "architect":
                return a
        return None

    async def _call_llm(
        self, agent: dict[str, Any], prompt: str
    ) -> str | None:
        """Call the agent's LLM adapter with a system prompt and return the response."""
        try:
            from app.services.adapter_manager import adapter_manager
            from app.services.secret_service import decrypt_secret

            adapter_type = agent.get("adapter_type", "mock")
            adapter = adapter_manager.get_adapter(adapter_type)
            model = agent.get("base_model_name") or adapter.default_model
            api_key_enc = agent.get("api_key", "")
            api_key = decrypt_secret(api_key_enc) if api_key_enc else ""
            base_url = agent.get("base_url") or ""

            # Build messages: system prompt only, user content inline
            messages = [
                {"role": "system", "content": prompt},
            ]

            # Use execute_prompt with the messages
            start = time.time()
            raw = await adapter.execute_prompt(
                prompt=prompt,
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=0.3,  # Low temp for structured output
                max_tokens=self.MAX_DECOMPOSE_TOKENS,
            )
            elapsed = time.time() - start

            logger.info(
                "task_decomposer: LLM call completed in %.1fs (adapter=%s, model=%s)",
                elapsed, adapter_type, model,
            )
            return raw
        except Exception as exc:
            logger.warning("task_decomposer: LLM call failed: %s", exc)
            return None

    def _parse_response(self, raw: str, content: str) -> DAGConfig | None:
        """Parse the LLM response into a DAGConfig."""
        # Try to extract JSON from the response (handle markdown code blocks)
        json_str = raw.strip()

        # Remove markdown code fences if present
        code_fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
        if code_fence_match:
            json_str = code_fence_match.group(1).strip()

        # Try to find a JSON object
        brace_match = re.search(r'\{.*\}', json_str, re.DOTALL)
        if brace_match:
            json_str = brace_match.group(0)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("task_decomposer: failed to parse JSON from response")
            return None

        # Extract fields
        analysis = data.get("analysis", "")
        execution_strategy = data.get("execution_strategy", "sequential")
        raw_nodes = data.get("nodes", [])

        if not raw_nodes:
            return None

        # Build DAGNode list
        nodes: list[DAGNode] = []
        for i, n in enumerate(raw_nodes):
            node = DAGNode(
                id=n.get("id", f"node_{i+1}"),
                domain=n.get("domain", "architect"),
                agent=n.get("agent", "Architect"),
                description=n.get("description", f"执行子任务 {i+1}"),
                dependencies=n.get("dependencies", []),
                status="PENDING",
                priority=n.get("priority", 1),
                estimated_effort=n.get("estimated_effort", "medium"),
            )
            nodes.append(node)

        return DAGConfig(
            total=len(nodes),
            completed=0,
            nodes=nodes,
            execution_strategy=execution_strategy,
            analysis=analysis,
        )

    @staticmethod
    def _classify_task_type(content: str) -> str:
        """Classify the user intent into a task type for history lookup."""
        content_lower = content.lower()
        if any(kw in content_lower for kw in ["前端", "页面", "ui", "组件", "frontend", "react", "vue"]):
            return "frontend"
        if any(kw in content_lower for kw in ["后端", "api", "接口", "backend", "fastapi", "数据库"]):
            return "backend"
        if any(kw in content_lower for kw in ["全栈", "fullstack", "前后端"]):
            return "fullstack"
        if any(kw in content_lower for kw in ["测试", "test", "bug", "fix", "修复"]):
            return "bugfix"
        if any(kw in content_lower for kw in ["部署", "deploy", "发布", "上线"]):
            return "deployment"
        if any(kw in content_lower for kw in ["架构", "设计", "architecture", "review", "审查"]):
            return "architecture"
        return "general"


# ── Module-level singleton ────────────────────────────────────────────

task_decomposer = ArchitectTaskDecomposer()
