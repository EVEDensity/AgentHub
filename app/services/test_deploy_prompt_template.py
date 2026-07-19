from __future__ import annotations

from app.services.agent_prompt_templates import build_deploy_prompt


def test_build_deploy_prompt_keeps_deployment_contract() -> None:
    prompt = build_deploy_prompt(
        agent_id="Deploy",
        content="deploy feature",
        symbolic_text='{"intent":"deployment"}',
        memory_context="[memory]\n",
        shared_context="[shared]\n",
        date_context="[date]\n",
        workspace_context="[workspace]\n",
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
    assert "deploy-card" in prompt
    assert "code_execute" in prompt
    assert prompt.endswith("deploy feature")
