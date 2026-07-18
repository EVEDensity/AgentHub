from __future__ import annotations

from app.services.agent_prompt_templates import build_orchestrator_prompt


def test_build_orchestrator_prompt_simple_mode_is_minimal() -> None:
    prompt = build_orchestrator_prompt(
        content="hello",
        symbolic_text='{"intent":"general"}',
        memory_context="",
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
        tools_enabled=False,
    )

    assert "AI 助手" in prompt
    assert "不要调用任何工具" in prompt
    assert prompt.endswith("用户需求: hello")


def test_build_orchestrator_prompt_with_preprocess_includes_workflow() -> None:
    prompt = build_orchestrator_prompt(
        content="do work",
        symbolic_text='{"intent":"orchestration"}',
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
        preprocess_context="subtask A -> B",
        tools_enabled=True,
    )

    assert "系统预处理分析" in prompt
    assert "invoke_agent" in prompt
    assert "[tools]" in prompt
