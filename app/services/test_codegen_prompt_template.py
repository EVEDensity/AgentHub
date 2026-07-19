from __future__ import annotations

from app.services.agent_prompt_templates import build_codegen_prompt


def test_build_codegen_prompt_keeps_json_contract() -> None:
    prompt = build_codegen_prompt(
        agent_id="CodeGen",
        content="implement feature",
        symbolic_text='{"intent":"code_generation"}',
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
    assert "代码生成规则" in prompt
    assert "{\"files\":[{\"path\":\"相对路径\"" in prompt
    assert prompt.endswith("用户需求: implement feature")
