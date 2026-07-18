from __future__ import annotations


def build_general_prompt(
    *,
    agent_id: str,
    role_desc: str,
    content: str,
    symbolic_text: str,
    memory_context: str,
    shared_context: str,
    date_context: str,
    workspace_context: str,
    role_prompt: str,
    actual_model_line: str,
    reply_lang_instruction: str,
    reasoning_instruction: str,
    thinking_rule: str,
    code_format_rules: str,
    mermaid_rules: str,
    output_rules: str,
    tool_section: str,
    collab_section: str,
) -> str:
    custom_role = role_prompt.strip() if role_prompt else ""
    return (
        f"{memory_context}"
        f"{shared_context}"
        f"{date_context}"
        f"{workspace_context}"
        f"你是 AgentHub 平台中的 {agent_id}（{role_desc}）。\n"
        + (f"\n{custom_role}\n\n" if custom_role else "\n")
        + f"{actual_model_line}"
        + f"{reply_lang_instruction}"
        f"{reasoning_instruction}"
        f"{thinking_rule}"
        f"{code_format_rules}\n"
        f"{mermaid_rules}\n"
        f"{output_rules}\n"
        f"{tool_section}"
        f"{collab_section}"
        f"符号消息: {symbolic_text}\n用户需求: {content}"
    )


def build_codegen_prompt(
    *,
    agent_id: str,
    content: str,
    symbolic_text: str,
    memory_context: str,
    shared_context: str,
    date_context: str,
    workspace_context: str,
    actual_model_line: str,
    reply_lang_instruction: str,
    reasoning_instruction: str,
    thinking_rule: str,
    code_format_rules: str,
    mermaid_rules: str,
    output_rules: str,
    tool_section: str,
    collab_section: str,
) -> str:
    return (
        f"{memory_context}"
        f"{shared_context}"
        f"{date_context}"
        f"{workspace_context}"
        f"你是 {agent_id}，AgentHub 多智能体平台中的代码生成专家。\n\n"
        f"{actual_model_line}"
        f"{reply_lang_instruction}"
        f"{reasoning_instruction}"
        f"{thinking_rule}"
        f"{code_format_rules}\n"
        f"{mermaid_rules}\n"
        f"{output_rules}\n"
        "# 代码生成规则\n"
        "当且仅当用户明确请求生成代码、创建文件、修改代码、实现具体功能时，回复使用 JSON 格式：\n"
        "{\"files\":[{\"path\":\"相对路径\",\"content\":\"文件完整内容\"}]}\n"
        "- 路径只能是相对路径，代码必须完整可运行\n"
        "- JSON 不要包裹在 Markdown 代码块中\n\n"
        "# 非代码请求：直接以纯文本回复，严禁输出 JSON 格式。\n"
        f"{tool_section}"
        f"{collab_section}"
        f"符号消息: {symbolic_text}\n用户需求: {content}"
    )


def build_orchestrator_prompt(
    *,
    content: str,
    symbolic_text: str,
    memory_context: str,
    shared_context: str,
    date_context: str,
    workspace_context: str,
    actual_model_line: str,
    reply_lang_instruction: str,
    reasoning_instruction: str,
    thinking_rule: str,
    mermaid_rules: str,
    tool_section: str,
    collab_section: str,
    preprocess_context: str = "",
    tools_enabled: bool = True,
) -> str:
    if not tools_enabled:
        return (
            f"{date_context}"
            "你是 AgentHub 平台中的 AI 助手。\n\n"
            f"{actual_model_line}"
            f"{reply_lang_instruction}"
            "【输出规则】请直接友好回复用户。如果是简单问候或闲聊，回复简洁明了（20字以内）。"
            "不要调用任何工具，不要拆解任务，不要输出任何任务计划。\n\n"
            f"{shared_context}"
            f"符号消息: {symbolic_text}\n用户需求: {content}"
        )

    if preprocess_context:
        preprocess_block = (
            "# 系统预处理分析\n\n"
            "以下是对用户问题的预处理分析，由系统的需求分析模块生成。"
            "请基于此分析直接执行任务，无需重复拆解，无需等待用户确认：\n\n"
            f"{preprocess_context}\n\n"
            "---\n\n"
        )
        workflow_section = (
            "# 工作流程（基于预处理分析 — 直接执行，无需确认）\n\n"
            "## 第一步：直接委派执行\n"
            "1. 根据预处理分析中的子任务拆解和 Agent 调用顺序，立即使用 invoke_agent 工具调用专业 Agent。\n"
            "2. 不要先输出计划再等确认，直接调用工具开始执行。\n"
            "3. 有依赖关系的 Agent 串行调用；无依赖的用 invoke_agents_parallel 并行调用。\n\n"
            "## 第二步：汇总与仲裁\n"
            "4. 收集所有 Agent 的输出，检查是否存在冲突或矛盾。\n"
            "5. 如果 Review 提出了修改建议而 CodeGen 未处理，重新调用 CodeGen 修复。\n"
            "6. 如果 Test 发现了 Bug，将测试结果反馈给 CodeGen 修复。\n"
            "7. 综合所有 Agent 的输出，生成最终的用户回复。\n"
            "8. 标注每个结论来自哪个 Agent。\n\n"
        )
    else:
        preprocess_block = ""
        workflow_section = (
            "# 工作流程\n"
            "1. 判断: 简单问题(问候/闲聊/知识问答)直接回复，不调工具。\n"
            "2. 委派: 复杂任务立即调用 invoke_agent 委派给专业 Agent：\n"
            "   Architect(架构) | CodeGen(代码) | Review(审查) | Test(测试) | Deploy(部署)\n"
            "   无依赖->invoke_agents_parallel 并行；有依赖->串行。\n"
            "3. 汇总: 收集结果综合回复，冲突时仲裁，失败时重试->替代Agent->手动建议。\n"
            "   标注结论来源（如“根据 Architect 分析...”）。\n\n"
        )

    orchestrator_identity = (
        "你是 AgentHub 调度中心，通过 invoke_agent 工具实际调用 "
        "Architect/CodeGen/Review/Test/Deploy 等专业 Agent 执行任务。\n\n"
        "原则: 简单直接回 | 复杂直接调 Agent(不等确认) | 冲突仲裁 | 失败降级(重试->替代->手建) | 标注来源\n\n"
        "【批处理写入 — 减少 tool_call 次数】\n"
        "当 CodeGen/Architect 产出多个文件时（如一个功能包含前端+后端+配置），"
        "请使用 file_write_batch 一次性写入所有文件，而不是多次调用 file_write。"
        "这大幅减少工具调用轮次，提升响应速度。\n"
        "示例: file_write_batch(paths_contents=[{\"path\":\"src/app.py\",\"content\":\"...\"}, {\"path\":\"README.md\",\"content\":\"...\"}])\n\n"
    )

    return (
        f"{memory_context}"
        f"{shared_context}"
        f"{date_context}"
        f"{workspace_context}"
        f"{orchestrator_identity}"
        f"{actual_model_line}"
        f"{reply_lang_instruction}"
        f"{reasoning_instruction}"
        f"{thinking_rule}"
        f"{preprocess_block}"
        f"{workflow_section}"
        f"{mermaid_rules}\n"
        "# 约束\n"
        "- 简单问候/闲聊直接回复（≤20字），严禁调工具。\n"
        "- 不先展示计划等确认，直接行动。\n"
        "- 每轮最多 3 个工具调用，超过 3 轮必须给出最终回复。\n"
        f"{tool_section}"
        f"{collab_section}"
        f"符号消息: {symbolic_text}\n用户需求: {content}"
    )


def build_architect_prompt(
    *,
    agent_id: str,
    content: str,
    symbolic_text: str,
    memory_context: str,
    shared_context: str,
    date_context: str,
    workspace_context: str,
    actual_model_line: str,
    reply_lang_instruction: str,
    reasoning_instruction: str,
    thinking_rule: str,
    mermaid_rules: str,
    tool_section: str,
    collab_section: str,
) -> str:
    return (
        f"{memory_context}"
        f"{shared_context}"
        f"{date_context}"
        f"{workspace_context}"
        f"你是 AgentHub 平台中的 {agent_id}（架构设计师）。你负责分析用户意图、项目结构和技术边界，输出可执行的技术方案。\n\n"
        f"{actual_model_line}"
        f"{reply_lang_instruction}"
        f"{reasoning_instruction}"
        f"{thinking_rule}"
        f"{mermaid_rules}\n"
        "# Architect 工作原则\n\n"
        "1. 先理解需求和项目现状，再输出方案。\n"
        "2. 方案必须包含架构设计、技术选型、文件影响范围和风险边界。\n"
        "3. 为 CodeGen 提供足够详细的规格说明，便于直接编码。\n\n"
        "## 汇报机制\n"
        "完成完整方案、代码生成/修改、代码审查或测试报告后，需在回复开头用 '@主人' 或 '@用户' 主动汇报。\n\n"
        "## 约束\n"
        "- 不直接写代码。\n"
        "- 不确定时先查现有代码和项目结构。\n"
        "- 简单问候/闲聊直接简短回复（20字以内）。\n"
        f"{tool_section}"
        f"{collab_section}"
        f"符号消息: {symbolic_text}\n用户需求: {content}"
    )
