from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional

logger = logging.getLogger("agenthub.tool_registry")


@dataclass
class ToolParameter:
    """A single parameter within a tool definition."""

    name: str
    type: str  # "string" | "number" | "boolean" | "array" | "object"
    required: bool
    description: str
    default: Any = None
    enum: list[str] | None = None


@dataclass
class ToolExample:
    """An example usage of a tool."""

    user_question: str
    parameters: dict[str, Any]


@dataclass
class ToolDefinition:
    """Complete metadata for a callable tool.

    Mirrors the user-specified fixed fields:
      name, description, category, parameters, return_type, examples
    """

    name: str  # unique tool identifier (snake_case)
    description: str  # functional description for enablement decisions
    category: str  # "search" | "file" | "code" | "memory" | "system" | "integration"
    parameters: list[ToolParameter]
    return_type: str  # human-readable return data format description
    examples: list[ToolExample]
    risk_level: str = "L1"  # L1=low, L2=medium, L3=high (requires confirmation)
    handler: Callable[..., Awaitable[dict]] | None = None  # async executor
    is_concurrency_safe: bool = True  # can this tool run in parallel with other safe tools?
    requires_user_confirmation: bool = False  # always ask user before execution

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for prompt generation and API responses."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "required": p.required,
                    "description": p.description,
                    **({"default": p.default} if p.default is not None else {}),
                    **({"enum": p.enum} if p.enum else {}),
                }
                for p in self.parameters
            ],
            "return_type": self.return_type,
            "examples": [
                {"user_question": e.user_question, "parameters": e.parameters}
                for e in self.examples
            ],
            "risk_level": self.risk_level,
            "is_concurrency_safe": self.is_concurrency_safe,
            "requires_user_confirmation": self.requires_user_confirmation,
        }


class ToolRegistry:
    """Central registry for all available tools.

    Singleton pattern — import the module-level ``tool_registry`` instance.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # ── Registration ──────────────────────────────────────────────────

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool definition. Overwrites if name already exists."""
        if tool.name in self._tools:
            logger.warning("tool_registry: overwriting tool '%s'", tool.name)
        self._tools[tool.name] = tool
        logger.info("tool_registry: registered tool '%s' (category=%s risk=%s)",
                     tool.name, tool.category, tool.risk_level)

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry. Returns True if it existed."""
        existed = self._tools.pop(name, None) is not None
        if existed:
            logger.info("tool_registry: unregistered tool '%s'", name)
        return existed

    # ── Lookup ────────────────────────────────────────────────────────

    def get(self, name: str) -> ToolDefinition | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def list_all(self) -> list[ToolDefinition]:
        """Return all registered tools."""
        return list(self._tools.values())

    def list_by_category(self, category: str) -> list[ToolDefinition]:
        """Return tools matching a category."""
        return [t for t in self._tools.values() if t.category == category]

    def list_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def count(self) -> int:
        return len(self._tools)

    def get_concurrency_safety(self, name: str) -> bool:
        """Return whether a tool is safe for concurrent execution."""
        tool = self.get(name)
        return tool.is_concurrency_safe if tool else True

    # ── Prompt generation ─────────────────────────────────────────────

    def build_prompt_section(self, tool_names: list[str] | None = None) -> str:
        """Generate the tool-definitions block for injection into the agent prompt.

        Args:
            tool_names: If provided, only include these tools. None = all tools.

        Returns a markdown-style text block listing every tool's metadata.
        """
        tools = self.list_all()
        if tool_names is not None:
            tools = [t for t in tools if t.name in tool_names]
        if not tools:
            return ""

        blocks: list[str] = []
        for i, t in enumerate(tools, 1):
            d = t.to_dict()
            params_lines: list[str] = []
            for p in d["parameters"]:
                req = "必填" if p["required"] else "选填"
                extra = ""
                if "default" in p and p["default"] is not None:
                    extra += f"，默认值: {json.dumps(p['default'], ensure_ascii=False)}"
                if "enum" in p and p["enum"]:
                    extra += f"，可选值: {json.dumps(p['enum'], ensure_ascii=False)}"
                params_lines.append(
                    f"  - {p['name']} ({p['type']}, {req}{extra}): {p['description']}"
                )

            examples_lines: list[str] = []
            for ex in d["examples"]:
                examples_lines.append(
                    f"  · 用户提问: \"{ex['user_question']}\"\n"
                    f"    调用参数: {json.dumps(ex['parameters'], ensure_ascii=False)}"
                )

            block = (
                f"### 工具 {i}: {d['name']}\n"
                f"- 分类: {d['category']}\n"
                f"- 风险等级: {d['risk_level']}\n"
                f"- 描述: {d['description']}\n"
                f"- 参数:\n"
                + "\n".join(params_lines) + "\n"
                f"- 返回类型: {d['return_type']}\n"
                f"- 使用示例:\n"
                + "\n".join(examples_lines)
            )
            blocks.append(block)

        return "\n\n".join(blocks)

    def build_calling_instructions(self) -> str:
        """Return the strict routing + tool-calling instructions.

        The agent MUST adhere to exactly one of three modes per response.
        Mixed output (JSON + natural language in the same turn) is forbidden.
        """
        return (
            "## 工具调用能力 — 路由规则\n\n"
            "请根据用户问题类型，严格在「联网检索」与「直接回答」两种模式间切换，禁止混合输出。\n\n"
            "# Tool Definition\n"
            "## web_search\n"
            "- **用途**: 获取实时资讯、新闻动态、时效性数据及大模型知识库未覆盖的最新信息\n"
            "- **参数**:\n"
            "  - `query` (string, 必填): 精准提炼的搜索关键词\n"
            "  - `max_results` (number, 选填): 返回条数，默认5\n"
            "  - `language` (string, 选填): 语言代码，默认\"zh\"\n"
            "  - `allowed_domains` (array, 选填): 只返回指定域名结果，如 [\"python.org\"]\n"
            "  - `blocked_domains` (array, 选填): 排除指定域名结果，如 [\"csdn.net\"]\n\n"
            "## skill_list\n"
            "- **用途**: 列出所有可用的技能(Skills)，包括用户级和项目级技能\n"
            "- **参数**:\n"
            "  - `source` (string, 选填): 过滤来源: 'all'(默认), 'user', 'project'\n\n"
            "## skill_load\n"
            "- **用途**: 加载指定技能的完整 SKILL.md 文档，了解技能的功能、触发条件和脚本用法\n"
            "- **参数**:\n"
            "  - `name` (string, 必填): 技能名称，例如 'anysearch'\n\n"
            "## command_execute\n"
            "- **用途**: 在指定目录中执行 shell 命令（Python/Bash/Node.js）。⚠️ 此工具需要用户确认\n"
            "- **参数**:\n"
            "  - `command` (string, 必填): 完整的 shell 命令\n"
            "  - `cwd` (string, 选填): 工作目录路径\n"
            "  - `timeout` (number, 选填): 超时秒数，默认60\n\n"
            "# Execution Rules\n\n"
            "## 🔍 模式A：联网检索（强制触发）\n"
            "当用户询问满足以下任一条件时，**必须且只能**输出纯JSON工具调用：\n"
            "1. 涉及当日/近期新闻、实时行情、赛事比分等时效性内容\n"
            "2. 询问大模型训练截止日期后发生的事件\n"
            "3. 明确要求\"搜索\"、\"查一下\"、\"最新\"等联网指令\n"
            "4. 事实性问题但知识库中无确切答案或存在版本更新\n\n"
            "### ⚠️ 输出约束\n"
            "- 仅输出合法JSON，禁止包含 ```json 代码块标记、前缀说明、后缀解释或任何自然语言\n"
            "- 输出格式固定为：\n"
            '  {"tool_calls": [{"name": "web_search", "arguments": {"query": "精炼搜索词", "max_results": 5, "language": "zh"}}]}\n'
            "- 如需同时调用多个工具（包括非搜索工具），可在 tool_calls 数组中放置多个对象\n\n"
            "## 📝 模式B：结果整合（工具返回后）\n"
            "当收到【工具返回内容】消息时：\n"
            "1. **禁止**再次发起任何工具调用\n"
            "2. 基于搜索结果整理出精简、通顺的自然语言回复\n"
            "3. 逐条汇总关键信息，标注信息来源\n"
            "4. 若搜索结果与问题无关或为空，如实告知用户并给出建议\n\n"
            "## 💬 模式C：直接回答\n"
            "不满足模式A触发条件时，直接以自然语言回复，不调用任何工具。\n"
            "包括但不限于：日常闲聊、问候、意见咨询、代码生成、文件操作等不需要实时数据的场景。\n\n"
            "## 🔧 模式D：技能系统工作流（Skill → 命令执行）\n"
            "当用户任务可能受益于已有技能时，按以下流程操作：\n"
            "1. **发现**: 调用 skill_list 查看可用技能\n"
            "2. **加载**: 对匹配的技能调用 skill_load 获取完整文档\n"
            "3. **执行**: 对已加载的技能，使用 command_execute 运行其脚本（需用户确认）\n"
            "4. **整合**: 基于命令执行结果整理自然语言回复\n\n"
            "### Skill 执行示例流程\n"
            "```\n"
            "用户: \"帮我搜索最新的科技新闻\"\n"
            "→ AI调用: skill_list(source=\"all\")\n"
            "→ AI发现 anysearch 技能\n"
            "→ AI调用: skill_load(name=\"anysearch\")\n"
            "→ AI阅读文档后调用: command_execute(\n"
            '    command="python scripts/anysearch_cli.py search --query \\"科技新闻 2026年6月\\" --max_results 5",\n'
            '    cwd="~/.claude/skills/anysearch", timeout=30)\n'
            "→ 用户确认后执行\n"
            "→ AI基于结果整理回复\n"
            "```\n\n"
            "# Critical Constraints\n"
            "1. **互斥原则**: 单轮响应只能是「纯JSON工具调用」或「自然语言回答」二者之一，绝不可同时出现\n"
            "2. **零幻觉**: 时效性问题未调用工具前，禁止凭记忆编造任何具体数据、日期或事件\n"
            "3. **Query优化**: 调用工具时需将用户口语化提问转化为搜索引擎友好的关键词组合\n"
            "4. **格式纯净**: JSON输出必须可被程序直接解析，不含任何额外字符\n"
            "5. **非搜索工具**: 调用 file_read/file_write/code_execute/memory_search/browser_navigate/skill_list/skill_load/command_execute 等非搜索工具时，同样遵循互斥原则\n"
            "6. **command_execute 确认**: command_execute 需要用户确认后才能执行，请在调用后等待确认\n"
            "7. **不确定时**: 若无法判断是否需要工具，默认直接回答（模式C）"
        )

    # ── Native function-calling format (OpenAI / compatible) ──────────

    def build_openai_tools(self, tool_names: list[str] | None = None) -> list[dict[str, Any]]:
        """Convert registered tools to OpenAI function-calling format.

        Returns a list suitable for the ``tools`` parameter of
        ``POST /v1/chat/completions``.  When *tool_names* is None,
        all registered tools are included.

        Example output element:
            {"type": "function", "function": {"name": "...", "description": "...",
             "parameters": {"type": "object", "properties": {...}, "required": [...]}}}
        """
        tools = self.list_all()
        if tool_names is not None:
            tools = [t for t in tools if t.name in tool_names]
        if not tools:
            return []

        openai_tools: list[dict[str, Any]] = []
        for t in tools:
            props: dict[str, Any] = {}
            required: list[str] = []
            for p in t.parameters:
                prop: dict[str, Any] = {
                    "type": p.type,
                    "description": p.description,
                }
                if p.default is not None:
                    prop["default"] = p.default
                if p.enum:
                    prop["enum"] = p.enum
                props[p.name] = prop
                if p.required:
                    required.append(p.name)

            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            })
        return openai_tools

    def parse_openai_tool_calls(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool calls from a native OpenAI chat-completion response.

        Parses ``choices[0].message.tool_calls`` and returns our internal
        format: ``[{"name": "...", "arguments": {...}}, ...]``.

        Returns an empty list if the response contains no tool calls.
        """
        try:
            message = response["choices"][0]["message"]
            raw_calls = message.get("tool_calls") or []
        except (KeyError, IndexError, TypeError):
            return []

        parsed: list[dict[str, Any]] = []
        for tc in raw_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "{}")
            try:
                arguments = json.loads(args_str) if isinstance(args_str, str) else args_str
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            if name:
                parsed.append({"name": name, "arguments": arguments})
        return parsed


# ── Singleton ──────────────────────────────────────────────────────────
tool_registry = ToolRegistry()
