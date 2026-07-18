from __future__ import annotations

from app.services.agent_prompt_templates import build_architect_prompt


def test_build_architect_prompt_keeps_design_contract() -> None:
    prompt = build_architect_prompt(
        agent_id="Architect",
        content="design the workflow",
        symbolic_text='{"intent":"architecture"}',
        memory_context="[memory]\n",
        shared_context="[shared]\n",
        date_context="[date]\n",
        workspace_context="[workspace]\n",
        actual_model_line="[model]\n",
        reply_lang_instruction="[language]\n",
        reasoning_instruction="[reasoning]\n",
        thinking_rule="[thinking]\n",
        mermaid_rules="[mermaid]\n",
        tool_section="[tools]\n",
        collab_section="[collab]\n",
    )

    assert "架构设计师" in prompt
    assert "@主人" in prompt
    assert "不直接写代码" in prompt
    assert prompt.endswith("用户需求: design the workflow")
