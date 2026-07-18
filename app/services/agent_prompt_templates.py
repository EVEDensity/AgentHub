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
