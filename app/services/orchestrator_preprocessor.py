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
from app.services.context_compaction import build_preprocess_context
from app.services.secret_service import decrypt_secret

logger = logging.getLogger("agenthub.orchestrator_preprocessor")

# ── Prompt for lightweight question pre-processing ───────────────────

PREPROCESS_SYSTEM = """你是一个资深技术架构师兼PM。你的任务是将用户的原始问题转化为结构化、可执行的专业方案。

## 你的任务

### 1. 意图分类
判断用户问题的类型：
- greeting: 简单问候/闲聊（你好、谢谢、再见、今天怎么样）
- factual: 事实查询/知识问答（什么是XXX、如何XXX、解释XXX）
- technical_development: 技术开发（前后端/全栈/功能实现/CRUD等）
- code_generation: 纯代码生成（写一个函数/脚本/组件）
- architecture: 架构设计/技术方案（系统设计、技术选型）
- deployment: 部署运维（发布、上线、CI/CD、容器化）
- debugging: 调试修复（修bug、排查问题、错误分析）

### 2. 需求提取（仅复杂任务）
从用户问题中提取：
- requirements: 功能需求列表（用户到底要什么功能？）
- non_functional_requirements: 非功能性需求（性能、安全、可扩展性、响应式等）

### 3. 方案探索与对比（仅复杂任务 — 核心能力）
基于需求，提出 2-3 个明显不同的技术方案。每个方案包含：
- id: "方案A" / "方案B" / "方案C"
- name: 方案简短名称（如 "React + FastAPI + PostgreSQL"）
- tech_stack: 技术栈列表
- architecture: 架构简述（1-2句话）
- pros: 优点列表（3-5个）
- cons: 缺点列表（3-5个）
- estimated_effort: 预估工作量（如 "2-3天"）
- risk_level: low / medium / high
- score: 综合评分（0-100，基于开发效率、性能、可维护性、学习成本等维度）

方案必须有实质差异（如不同的前端框架、不同的后端语言、不同的数据库策略），不能只是同一方案的微调。

### 4. 方案推荐
- recommended_solution_id: 推荐哪个方案（填方案id如"方案A"）
- recommendation_reason: 推荐理由（2-3句话，说明为什么这个方案最适合当前需求）

### 5. 子任务拆解
将推荐方案拆解为2-5个子任务：
- id: 序号
- title: 子任务简短标题
- description: 具体做什么（详细，包含技术栈信息，让下游Agent能直接执行）
- domain: 适合的Agent角色（architect/codegen/review/test/deploy/general）
- depends_on: 依赖的前置子任务id列表

### 6. 约束提取 & Agent路由
- constraints: 技术约束列表
- routing: 执行顺序、并行机会、潜在冲突、失败降级方案

## 输出格式
严格的JSON对象（不要markdown代码块，不要输出任何解释文字）：

{
  "intent_type": "technical_development",
  "is_simple": false,
  "clarified_question": "重述后的清晰问题（1-3句话）",
  "requirements": ["用户注册/登录", "权限管理(RBAC)", "用户列表CRUD", "用户资料编辑"],
  "non_functional_requirements": ["响应式设计", "JWT认证", "密码bcrypt加密", "API限流"],
  "solutions": [
    {
      "id": "方案A",
      "name": "React + FastAPI + PostgreSQL",
      "tech_stack": ["React 18", "FastAPI", "PostgreSQL", "Ant Design", "SQLAlchemy"],
      "architecture": "前后端分离架构，React SPA通过RESTful API与FastAPI通信，PostgreSQL存储用户数据",
      "pros": ["开发效率高，Python全栈统一", "FastAPI自动生成API文档", "Ant Design组件丰富，UI开发快", "PostgreSQL成熟稳定"],
      "cons": ["需要单独部署数据库", "React状态管理需额外配置", "SEO不友好（SPA通病）"],
      "estimated_effort": "2-3天",
      "risk_level": "low",
      "score": 92
    },
    {
      "id": "方案B",
      "name": "Next.js + Prisma + SQLite",
      "tech_stack": ["Next.js 14", "Prisma ORM", "SQLite", "Tailwind CSS", "NextAuth.js"],
      "architecture": "Next.js全栈应用，API Routes处理后端逻辑，Prisma管理数据库，单体部署",
      "pros": ["单一项目结构，部署简单", "TypeScript全栈类型安全", "Tailwind CSS快速样式", "文件数据库零配置"],
      "cons": ["SQLite不适合高并发", "Prisma学习曲线陡峭", "Next.js服务端渲染增加复杂度"],
      "estimated_effort": "2-3天",
      "risk_level": "medium",
      "score": 78
    }
  ],
  "recommended_solution_id": "方案A",
  "recommendation_reason": "方案A使用成熟的Python+PostgreSQL技术栈，长期可维护性好，与当前AgentHub平台的Python生态一致，便于集成和扩展。方案B虽然部署简单但SQLite不适合生产环境。",
  "sub_tasks": [
    {"id": 1, "title": "数据库模型设计", "description": "使用SQLAlchemy设计User/Role/Permission模型...", "domain": "architect", "depends_on": []},
    {"id": 2, "title": "后端API实现", "description": "使用FastAPI实现用户CRUD API...", "domain": "codegen", "depends_on": [1]}
  ],
  "constraints": ["使用React", "需要响应式设计", "JWT认证"],
  "suggested_approach": "Architect→CodeGen→Review→Test 的顺序执行",
  "routing": {
    "execution_order": ["Architect", "CodeGen", "Review", "Test"],
    "parallel_opportunities": ["Review和Test可以在CodeGen完成后并行执行"],
    "potential_conflicts": ["Review可能提出修改建议需要CodeGen重新生成"],
    "fallback_agents": {"Architect": "可由系统AI直接分析替代", "CodeGen": "必须成功，是整个流程的关键节点"}
  }
}

## 规则
- 问候/闲聊/感谢 → is_simple=true, clarified_question="", solutions=[], sub_tasks=[], routing=null
- 简单事实查询 → is_simple=true, clarified_question=重述的问题, solutions=[], sub_tasks=[]
- 技术开发/代码生成/架构设计 → is_simple=false, 必须提供 requirements+solutions（2-3个）+sub_tasks（2-5个）+routing
- solutions 数组必须有 2-3 个方案，方案间必须有实质差异
- recommended_solution_id 必须对应 solutions 中某个方案的 id
- depends_on 列表填前置任务的 id 数字
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


# ── Compound-task heuristic keywords ───────────────────────────────────
# When a user message (even without @Orchestrator) matches these patterns,
# the system may auto-decompose it into DAG sub-tasks to avoid timeouts
# on overly ambitious single-shot LLM calls.

_COMPOUND_KEYWORDS = [
    "完整", "整个", "全部", "所有", "整套",
    "前后端", "全栈", "前端和后端", "后端和前端",
    "多个文件", "多文件", "批量", "一起",
    "同时生成", "同时创建", "一整套",
]


def should_decompose(content: str) -> bool:
    """Determine whether a user message should be auto-decomposed into DAG sub-tasks.

    Returns True when the message is long enough AND contains compound-task
    indicators, suggesting it's too large for a single LLM call to handle
    within the per-request timeout.

    Only used when the user does NOT explicitly invoke @Orchestrator.
    """
    from app.config import AGENTHUB_AUTO_DECOMPOSE, AGENTHUB_AUTO_DECOMPOSE_MIN_LENGTH

    if not AGENTHUB_AUTO_DECOMPOSE:
        return False

    text = (content or "").strip()
    if len(text) < AGENTHUB_AUTO_DECOMPOSE_MIN_LENGTH:
        return False

    # Check for compound-task keywords
    text_lower = text.lower()
    for kw in _COMPOUND_KEYWORDS:
        if kw in text_lower:
            return True

    # Heuristic: very long messages (>1500 chars) are likely compound
    if len(text) > 1500:
        return True

    # Check for multiple file-creation patterns
    file_patterns = re.findall(r"(?:创建|生成|写入|新建|写|编写)\S*(?:文件|代码|页面|组件|模块)", text)
    if len(file_patterns) >= 2:
        return True

    return False


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

    MAX_PREPROCESS_TOKENS = 1200
    PREPROCESS_TIMEOUT = 25.0  # seconds — slightly longer for solution exploration

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
            "requirements": data.get("requirements", []),
            "non_functional_requirements": data.get("non_functional_requirements", []),
            "solutions": data.get("solutions", []),
            "recommended_solution_id": data.get("recommended_solution_id", ""),
            "recommendation_reason": data.get("recommendation_reason", ""),
            "sub_tasks": data.get("sub_tasks", []),
            "constraints": data.get("constraints", []),
            "suggested_approach": data.get("suggested_approach", ""),
            "routing": data.get("routing", None),
        }

    # ── Prompt formatting ──────────────────────────────────────────────

    def format_for_prompt(self, preprocessed: dict[str, Any]) -> str:
        """Format a pre-processing result as a compact block for the main prompt."""
        return build_preprocess_context(preprocessed)

    # ── DAG construction from preprocess result ─────────────────────────

    def build_dag_from_preprocess(
        self,
        preprocess_result: dict[str, Any],
        content: str = "",
        solution_context: dict[str, Any] | None = None,
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

        Args:
            preprocess_result: Parsed pre-processing result dict.
            content: Original user message (fallback for analysis field).
            solution_context: Optional dict with the user-selected solution
                (id, name, tech_stack, architecture).  Injected into node
                descriptions so downstream agents know the tech stack.

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

        # ── Build solution context prefix for node descriptions ──────
        sol_prefix = ""
        if solution_context and isinstance(solution_context, dict):
            sol_name = solution_context.get("name", "")
            sol_stack = solution_context.get("tech_stack", [])
            if sol_name or sol_stack:
                parts = []
                if sol_name:
                    parts.append(f"技术方案: {sol_name}")
                if sol_stack:
                    parts.append(f"技术栈: {', '.join(sol_stack)}")
                sol_prefix = "；".join(parts) + "。\n"

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

            # Prepend solution context to node description
            full_desc = sol_prefix + (f"{title}: {desc}" if title else desc)

            # Normalize depends_on to string IDs ("1" → "n1")
            deps_raw = st.get("depends_on", [])
            dep_ids: list[str] = []
            for d in deps_raw:
                dep_ids.append(f"n{d}" if isinstance(d, int) else f"n{int(d)}" if str(d).isdigit() else str(d))

            nodes.append(DAGNode(
                id=f"n{sid}",
                domain=domain,
                agent=agent_id,
                description=full_desc,
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
            solution_context=solution_context,
        )


# ── Module-level singleton ────────────────────────────────────────────

orchestrator_preprocessor = OrchestratorPreprocessor()
