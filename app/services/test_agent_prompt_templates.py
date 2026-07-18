from __future__ import annotations

from app.services.agent_prompt_templates import build_general_prompt


def test_build_general_prompt_preserves_anchor_and_sections() -> None:
    prompt = build_general_prompt(
        agent_id="Helper",
        role_desc="generalist",
        content="summarize this",
        symbolic_text='{"intent":"general"}',
        memory_context="[memory]\n",
        shared_context="[shared]\n",
        date_context="[date]\n",
        workspace_context="[workspace]\n",
        role_prompt="Be concise.",
        actual_model_line="[model]\n",
        reply_lang_instruction="[language]\n",
        reasoning_instruction="[reasoning]\n",
        thinking_rule="[thinking]\n",
        code_format_rules="[code]\n",
        mermaid_rules="[mermaid]\n",
        output_rules="[output]\n",
        tool_section="[tools]\n",
        collab_section="[collab]\n",
    )

    assert prompt.startswith("[memory]\n[shared]\n[date]\n[workspace]\n")
    assert "Be concise." in prompt
    assert "符号消息: {\"intent\":\"general\"}" in prompt
    assert prompt.endswith("用户需求: summarize this")
