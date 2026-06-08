from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.config import (
    ENABLE_REAL_LLM,
    ORCHESTRATOR_PREPROCESS_ENABLED,
    ORCHESTRATOR_PREPROCESS_MIN_LENGTH,
)
from app.services.adapter_manager import adapter_manager
from app.services.secret_service import decrypt_secret

logger = logging.getLogger("agenthub.orchestrator_preprocessor")

# ── Prompt for lightweight question pre-processing ───────────────────

PREPROCESS_SYSTEM = """你是一个技术需求分析助手。你的任务是将用户的原始问题转化为更清晰、结构化、适合AI执行的专业描述。

## 你的任务
1. **意图分类**: 判断用户问题的类型
   - greeting: 简单问候/闲聊（你好、谢谢、再见、今天怎么样）
   - factual: 事实查询/知识问答（什么是XXX、如何XXX、解释XXX）
   - technical_development: 技术开发（包含前后端/全栈/功能实现/CRUD等）
   - code_generation: 纯代码生成（写一个函数/脚本/组件）
   - architecture: 架构设计/技术方案（系统设计、技术选型）
   - deployment: 部署运维（发布、上线、CI/CD、容器化）
   - debugging: 调试修复（修bug、排查问题、错误分析）

2. **问题重述**: 将用户的问题用更清晰、结构化、专业化的语言重述一遍，补全隐含的技术上下文。

3. **子任务拆解**: 如果问题涉及多步骤（2-5步），将其拆解为独立的子任务，每个子任务明确：
   - id: 序号
   - title: 子任务简短标题
   - description: 具体要做什么（详细，让下游Agent能直接执行）
   - domain: 适合的Agent角色（architect/codegen/review/test/deploy/general）
   - depends_on: 依赖的前置子任务id列表（如 [1] 表示依赖任务1完成）

4. **约束提取**: 提取用户显式或隐式提到的技术约束（语言、框架、平台、性能、安全等）

5. **Agent 路由建议**: 为复杂任务提供推荐的 Agent 调用顺序和潜在的冲突点/失败点

## 输出格式
严格的JSON对象（不要markdown代码块，不要输出任何解释文字）：

{
  "intent_type": "technical_development",
  "is_simple": false,
  "clarified_question": "重述后的清晰问题（1-3句话）",
  "sub_tasks": [
    {
      "id": 1,
      "title": "需求分析与架构设计",
      "description": "分析博客网站需求，设计技术架构方案...",
      "domain": "architect",
      "depends_on": []
    },
    {
      "id": 2,
      "title": "代码实现",
      "description": "基于架构方案生成前后端代码...",
      "domain": "codegen",
      "depends_on": [1]
    }
  ],
  "constraints": ["使用React", "需要响应式设计"],
  "suggested_approach": "Architect→CodeGen→Review→Test 的顺序执行",
  "routing": {
    "execution_order": ["Architect", "CodeGen", "Review", "Test"],
    "parallel_opportunities": ["Review和Test可以在CodeGen完成后并行执行"],
    "potential_conflicts": ["Review可能提出修改建议需要CodeGen重新生成"],
    "fallback_agents": {"Architect": "可由系统AI直接分析替代", "CodeGen": "必须成功，是整个流程的关键节点"}
  }
}

## 规则
- 问候/闲聊/感谢 → is_simple=true, clarified_question="", sub_tasks=[], routing=null, suggested_approach="直接友好回复"
- 简单事实查询（≤1句话能回答） → is_simple=true, clarified_question=重述的问题, sub_tasks=[]
- 技术开发/代码生成/架构设计 → is_simple=false, 必须拆解sub_tasks（2-5个），必须提供routing
- depends_on 列表填前置任务的 id 数字
- routing.potential_conflicts 预判哪些Agent输出可能冲突（如Review可能否定CodeGen的方案）
- routing.fallback_agents 为每个关键Agent指定失败时的替代方案
- 只输出JSON，不要任何额外文字"""

# ── Simple question detection ─────────────────────────────────────────

# Greeting patterns that should skip pre-processing entirely
_SIMPLE_GREETING_PATTERNS = [
    r'^(你好|hi|hello|hey|嗨|早上好|下午好|晚上好|晚安|再见|bye|谢谢|thanks?|thank\s*you|3q|ok|好的|嗯|哦|知道了)[\s!！。.,，]*$',
    r'^(今天|最近|最近怎么样|how\s*are\s*you|what\'?s?\s*up|干嘛呢|在吗|在不在)[\s?!！。.,，]*$',
    r'^(天气|时间|日期|星期几|几点了)[\s?!！。.,，]*$',
    r'^(你是谁|你的名字|你能做什么|你会什么|介绍一下你自己)[\s?!！。.,，]*$',
    # Multi-person greetings — "大家好", "各位好", "大家早上好" etc.
    # These are often combined with emoji (🤩👋😊🎉👍 etc.)
    r'^(大家|各位|大伙|朋友们|伙伴们|同学们|大家好|各位好|大家早上好|大家下午好|大家晚上好|大家好呀|大家好啊|大家早|大家晚安|各位早|各位晚安|hi\s*all|hello\s*all|hey\s*all|hello\s*everyone|hi\s*everyone|hey\s*everyone).*$',
    # Emoji-only or emoji-heavy greetings (short messages dominated by emoji)
    # Matches 1-6 emoji/symbol chars with optional whitespace/punctuation.
    r'^[\U0001F000-\U0001FFFF☀-➿⭐❤\s!！。.,，]{1,12}$',
]

# Technical keywords that indicate complexity (even for shorter messages)
_TECH_KEYWORDS = [
    '开发', '实现', '写', '代码', '生成', '创建', '设计', '架构',
    '部署', '发布', '上线', '测试', '审查', '修复', 'bug', '错误',
    '优化', '重构', '配置', '安装', '集成', '迁移', '升级',
    'api', '接口', '页面', '组件', '模块', '功能', '系统', '数据库',
    '前端', '后端', '全栈', 'react', 'vue', 'angular', 'node',
    'python', 'java', 'go', 'rust', 'docker', 'k8s', 'ci/cd',
    'develop', 'implement', 'create', 'build', 'design', 'deploy',
    'code', 'function', 'feature', 'component', 'module',
    'crud', 'rest', 'graphql', 'sql', 'nosql', 'redis',
    '帮我', '做个', '写个', '搞个', '弄个',
]


class OrchestratorPreprocessor:
    """Lightweight pre-processor that analyzes and restructures user questions.

    Runs before the main LLM call when the Orchestrator is the target agent.
    Uses a fast LLM call (low max_tokens, low temperature) to produce:
    - Intent classification
    - Clarified / rephrased question
    - Sub-task decomposition
    - Constraint extraction

    Simple questions (greetings, short queries) skip pre-processing.
    """

    MAX_PREPROCESS_TOKENS = 600
    PREPROCESS_TIMEOUT = 20.0  # seconds — keep it fast

    def __init__(self) -> None:
        pass

    # ── Public API ─────────────────────────────────────────────────────

    async def process(
        self,
        content: str,
        agent: dict[str, Any],
        user_id: str,
    ) -> dict[str, Any] | None:
        """Pre-process a user question for the Orchestrator.

        Args:
            content: The user's raw message text.
            agent: Agent dict from agent_registry (the Orchestrator).
            user_id: Current user ID.

        Returns:
            Parsed pre-processing result dict, or None if skipped/failed.
        """
        # ── Guard: config disabled ──────────────────────────────────
        if not ORCHESTRATOR_PREPROCESS_ENABLED:
            return None

        # ── Guard: simple question → return synthetic result ──────────
        # Don't return None — return a marked "simple" result so downstream
        # code (build_prompt) knows to disable tools and use a minimal prompt.
        # This prevents the main LLM from receiving a massive prompt with
        # aggressive tool-calling instructions for trivial greetings.
        if not self._is_complex(content):
            raw_simple = self._classify_simple(content)
            logger.debug(
                "orchestrator_preprocessor: simple question detected (%d chars) type=%s",
                len(content), raw_simple.get("intent_type", "?"),
            )
            return raw_simple

        # ── Guard: real LLM required ────────────────────────────────
        if not ENABLE_REAL_LLM:
            logger.debug("orchestrator_preprocessor: real LLM disabled, skipping")
            return None

        # ── Guard: must have valid agent with adapter ───────────────
        adapter_type = agent.get("adapter_type", "").lower()
        if not adapter_type or adapter_type == "mock":
            logger.debug("orchestrator_preprocessor: mock adapter, skipping")
            return None

        try:
            result = await self._call_llm(agent, content)
            if result:
                logger.info(
                    "orchestrator_preprocessor: intent=%s is_simple=%s sub_tasks=%d",
                    result.get("intent_type", "?"),
                    result.get("is_simple", True),
                    len(result.get("sub_tasks", [])),
                )
            return result
        except Exception as exc:
            logger.warning(
                "orchestrator_preprocessor: LLM call failed, skipping: %s", exc
            )
            return None

    # ── Simple message classification (no LLM call) ─────────────────────

    def _classify_simple(self, content: str) -> dict[str, Any]:
        """Classify a simple message without calling the LLM.

        Returns a synthetic preprocess result with is_simple=True so
        downstream code can disable tools and use a minimal prompt.
        """
        stripped = content.strip()

        # Detect greeting
        for pattern in _SIMPLE_GREETING_PATTERNS:
            if re.match(pattern, stripped, re.IGNORECASE):
                return {
                    "intent_type": "greeting",
                    "is_simple": True,
                    "clarified_question": "",
                    "sub_tasks": [],
                    "constraints": [],
                    "suggested_approach": "简短友好回复，不调用任何工具",
                    "_no_tools": True,
                }

        # Short content without technical keywords → likely small talk
        stripped_lower = stripped.lower()
        tech_hits = sum(1 for kw in _TECH_KEYWORDS if kw in stripped_lower)
        if tech_hits == 0 and len(stripped) < 50:
            return {
                "intent_type": "conversational",
                "is_simple": True,
                "clarified_question": stripped,
                "sub_tasks": [],
                "constraints": [],
                "suggested_approach": "直接回复，不调用工具",
                "_no_tools": True,
            }

        # Short, no tech keywords, but longer than greeting
        return {
            "intent_type": "factual",
            "is_simple": True,
            "clarified_question": stripped,
            "sub_tasks": [],
            "constraints": [],
            "suggested_approach": "直接简洁回复",
            "_no_tools": False,
        }

    # ── Complexity detection ───────────────────────────────────────────

    def _is_complex(self, content: str) -> bool:
        """Quick heuristic to determine if a question needs pre-processing.

        Returns False for simple greetings / short questions, True otherwise.
        Tech keywords are checked FIRST so short but technical messages
        (e.g. "帮我用React写个登录页面") still trigger LLM pre-processing.

        Conversational messages without tech keywords are treated as simple
        even if they're moderately long — the main LLM handles them fine.
        """
        stripped = content.strip()

        # Check against greeting patterns first (very short messages)
        for pattern in _SIMPLE_GREETING_PATTERNS:
            if re.match(pattern, stripped, re.IGNORECASE):
                return False

        # Contains technical keywords → complex even if short
        stripped_lower = stripped.lower()
        tech_hits = sum(1 for kw in _TECH_KEYWORDS if kw in stripped_lower)
        if tech_hits >= 1:
            return True

        # Too short → likely simple
        if len(stripped) < ORCHESTRATOR_PREPROCESS_MIN_LENGTH:
            return False

        # Only flag as complex for longer messages without tech keywords
        # when they're substantial enough to potentially contain implicit
        # technical requests (≥100 chars).  Conversational messages in the
        # 30-100 char range go directly to the main LLM without preprocessing.
        return len(stripped) >= 100

    # ── LLM invocation ─────────────────────────────────────────────────

    async def _call_llm(
        self, agent: dict[str, Any], content: str
    ) -> dict[str, Any] | None:
        """Call the agent's LLM adapter for pre-processing."""
        adapter_type = agent.get("adapter_type", "mock")
        adapter = adapter_manager.get_adapter(adapter_type)
        model = agent.get("base_model_name") or adapter.default_model
        api_key_enc = agent.get("api_key", "")
        api_key = decrypt_secret(api_key_enc) if api_key_enc else ""
        base_url = agent.get("base_url") or ""

        # Build the full prompt: system instructions + user question
        prompt = (
            f"{PREPROCESS_SYSTEM}\n\n"
            f"## 用户问题\n{content}"
        )

        start = time.time()
        raw = await adapter.execute_prompt(
            prompt=prompt,
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.2,
            max_tokens=self.MAX_PREPROCESS_TOKENS,
        )
        elapsed = time.time() - start
        logger.debug(
            "orchestrator_preprocessor: LLM call %.1fs (adapter=%s model=%s)",
            elapsed, adapter_type, model,
        )

        return self._parse_response(raw)

    # ── Response parsing ───────────────────────────────────────────────

    def _parse_response(self, raw: str) -> dict[str, Any] | None:
        """Parse the LLM JSON response into a structured dict."""
        if not raw or not raw.strip():
            return None

        # Remove markdown code fences
        json_str = raw.strip()
        code_fence = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', json_str, re.DOTALL)
        if code_fence:
            json_str = code_fence.group(1).strip()

        # Find the JSON object
        brace_match = re.search(r'\{.*\}', json_str, re.DOTALL)
        if brace_match:
            json_str = brace_match.group(0)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(
                "orchestrator_preprocessor: JSON parse failed, raw=%s",
                raw[:200],
            )
            return None

        # Validate required fields
        if not isinstance(data, dict):
            return None
        if "intent_type" not in data and "is_simple" not in data:
            return None

        # If LLM says it's simple, return a minimal result
        if data.get("is_simple", False):
            return {
                "intent_type": data.get("intent_type", "factual"),
                "is_simple": True,
                "clarified_question": data.get("clarified_question", ""),
                "sub_tasks": [],
                "constraints": [],
                "suggested_approach": data.get("suggested_approach", ""),
                "routing": None,
            }

        return {
            "intent_type": data.get("intent_type", "technical_development"),
            "is_simple": False,
            "clarified_question": data.get("clarified_question", ""),
            "sub_tasks": data.get("sub_tasks", []),
            "constraints": data.get("constraints", []),
            "suggested_approach": data.get("suggested_approach", ""),
            "routing": data.get("routing", None),
        }

    # ── Prompt formatting ──────────────────────────────────────────────

    def format_for_prompt(self, preprocessed: dict[str, Any]) -> str:
        """Format a pre-processing result as a Markdown block for the main prompt.

        This is injected before the user's original question in build_prompt().
        Includes agent routing suggestions, potential conflicts, and fallback
        strategies to guide the Orchestrator's dispatch decisions.
        """
        if not preprocessed or preprocessed.get("is_simple"):
            return ""

        lines: list[str] = []

        # Clarified question
        clarified = preprocessed.get("clarified_question", "").strip()
        if clarified:
            lines.append(f"**问题重述**: {clarified}")

        # Sub-tasks with dependency info
        sub_tasks = preprocessed.get("sub_tasks", [])
        if sub_tasks:
            lines.append("\n**子任务拆解**:")
            for st in sub_tasks:
                sid = st.get("id", "?")
                title = st.get("title", "")
                desc = st.get("description", "")
                domain = st.get("domain", "general")
                deps = st.get("depends_on", [])
                domain_labels = {
                    "architect": "Architect（架构设计）",
                    "codegen": "CodeGen（代码生成）",
                    "review": "Review（代码审查）",
                    "test": "Test（测试验证）",
                    "deploy": "Deploy（部署发布）",
                    "general": "通用",
                }
                domain_label = domain_labels.get(domain, domain)
                dep_str = f" (依赖: 任务{', '.join(map(str, deps))})" if deps else ""
                lines.append(f"  {sid}. {domain_label}: {title}{dep_str}")
                if desc:
                    lines.append(f"     → {desc}")

        # Constraints
        constraints = preprocessed.get("constraints", [])
        if constraints:
            lines.append(f"\n**技术约束**: {', '.join(constraints)}")

        # Suggested approach
        approach = preprocessed.get("suggested_approach", "").strip()
        if approach:
            lines.append(f"\n**建议路径**: {approach}")

        # ── Agent routing guidance ──────────────────────────────────────
        routing = preprocessed.get("routing")
        if routing and isinstance(routing, dict):
            # Execution order
            exec_order = routing.get("execution_order", [])
            if exec_order:
                arrows = " → ".join(exec_order)
                lines.append(f"\n**Agent 调用顺序**: {arrows}")

            # Parallel opportunities
            parallel = routing.get("parallel_opportunities", [])
            if parallel:
                if isinstance(parallel, list):
                    for p in parallel:
                        lines.append(f"**并行机会**: {p}")
                elif isinstance(parallel, str):
                    lines.append(f"**并行机会**: {parallel}")

            # Potential conflicts
            conflicts = routing.get("potential_conflicts", [])
            if conflicts:
                if isinstance(conflicts, list):
                    lines.append(f"\n**⚠️ 潜在冲突**:")
                    for c in conflicts:
                        lines.append(f"  - {c}")
                elif isinstance(conflicts, str):
                    lines.append(f"\n**⚠️ 潜在冲突**: {conflicts}")

            # Fallback agents
            fallbacks = routing.get("fallback_agents", {})
            if fallbacks and isinstance(fallbacks, dict):
                lines.append(f"\n**🔄 失败降级方案**:")
                for agent_name, fallback_plan in fallbacks.items():
                    lines.append(f"  - {agent_name} 失败时: {fallback_plan}")

        return "\n".join(lines) if lines else ""

    # ── DAG construction from preprocess result ─────────────────────────

    def build_dag_from_preprocess(
        self,
        preprocess_result: dict[str, Any],
        content: str = "",
    ) -> Any | None:
        """Build a DAGConfig from the preprocessor's sub-task decomposition.

        This bridges the gap between the Orchestrator's NL-driven analysis
        and the DAG execution engine.  When the preprocessor identifies
        sub-tasks with routing suggestions, we construct a real DAG so
        that:

        - Independent nodes run in parallel (via DAGExecutor).
        - Dependencies are enforced (a node waits for its ``depends_on``).
        - Node status is broadcast to the frontend in real time.
        - Failures trigger retries and fallback chains.

        Returns a ``DAGConfig`` or ``None`` if the result isn't suitable
        for DAG construction (e.g. is_simple=True or no sub_tasks).
        """
        if not preprocess_result or preprocess_result.get("is_simple"):
            return None

        sub_tasks = preprocess_result.get("sub_tasks", [])
        if not sub_tasks or len(sub_tasks) < 2:
            return None

        routing = preprocess_result.get("routing") or {}
        execution_order = routing.get("execution_order", [])
        parallel_hints = routing.get("parallel_opportunities", [])

        # ── Map domain → agent_id ────────────────────────────────────
        DOMAIN_TO_AGENT: dict[str, str] = {
            "architect": "Architect",
            "codegen": "CodeGen",
            "review": "Review",
            "test": "Test",
            "deploy": "Deploy",
            "general": "Orchestrator",
        }

        from app.schemas.dag import DAGConfig, DAGNode

        nodes: list[DAGNode] = []
        for st in sub_tasks:
            sid = st.get("id", len(nodes) + 1)
            domain = st.get("domain", "general")
            agent_id = DOMAIN_TO_AGENT.get(domain, "Orchestrator")
            title = st.get("title", "")
            desc = st.get("description", "")

            # Normalize depends_on to string IDs ("1" → "n1")
            deps_raw = st.get("depends_on", [])
            dep_ids: list[str] = []
            for d in deps_raw:
                dep_ids.append(f"n{d}" if isinstance(d, int) else f"n{int(d)}" if str(d).isdigit() else str(d))

            nodes.append(DAGNode(
                id=f"n{sid}",
                domain=domain,
                agent=agent_id,
                description=f"{title}: {desc}" if title else desc,
                dependencies=dep_ids,
                status="PENDING",
                priority=1,
                estimated_effort="medium",
            ))

        # ── Determine execution strategy ─────────────────────────────
        strategy = "sequential"
        if parallel_hints:
            strategy = "mixed"
        elif execution_order and len(execution_order) > 1:
            # Check if any nodes share the same dependency set → parallel
            dep_groups: dict[tuple, int] = {}
            for n in nodes:
                key = tuple(sorted(n.dependencies))
                dep_groups[key] = dep_groups.get(key, 0) + 1
            if any(v >= 2 for v in dep_groups.values()):
                strategy = "mixed"

        analysis = preprocess_result.get("clarified_question", content)
        approach = preprocess_result.get("suggested_approach", "")
        if approach:
            analysis = f"{analysis}\n建议路径: {approach}"

        return DAGConfig(
            total=len(nodes),
            completed=0,
            nodes=nodes,
            execution_strategy=strategy,
            analysis=analysis,
        )


# ── Module-level singleton ────────────────────────────────────────────

orchestrator_preprocessor = OrchestratorPreprocessor()
