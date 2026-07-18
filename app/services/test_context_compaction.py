from __future__ import annotations

from app.services.context_compaction import (
    build_preprocess_context,
    build_result_preview,
    build_task_preview_item,
)


def test_build_preprocess_context_is_compact() -> None:
    text = build_preprocess_context(
        {
            "intent_type": "technical_development",
            "clarified_question": "请帮我设计一个可扩展的企业级多智能体平台，并支持任务编排。",
            "requirements": ["多智能体协作", "企业自托管"],
            "non_functional_requirements": ["低耦合", "低 token 消耗"],
            "solutions": [
                {
                    "id": "A",
                    "name": "Go + Python",
                    "tech_stack": ["Go", "Python", "NATS"],
                    "score": 91,
                    "risk_level": "low",
                }
            ],
            "sub_tasks": [
                {"id": 1, "domain": "architect", "title": "拆分架构", "depends_on": []},
            ],
            "constraints": ["自托管"],
            "routing": {"execution_order": ["Architect", "CodeGen"]},
        }
    )

    assert "intent=technical_development" in text
    assert "route=Architect->CodeGen" in text
    assert "requirements=" in text
    assert "【预处理摘要】" in text


def test_build_task_preview_item_is_short() -> None:
    item = build_task_preview_item(
        {
            "id": "n1",
            "agent": "Architect",
            "description": "梳理企业级多智能体平台的整体架构、模块边界和协作方式",
            "dependencies": ["n0"],
            "estimated_effort": "high",
        }
    )

    assert item["description"].startswith("梳理企业级多智能体平台")
    assert item["estimatedSeconds"] == 90


def test_build_result_preview_trims_long_text() -> None:
    preview = build_result_preview("x" * 500, max_chars=80)

    assert len(preview) <= 80
    assert preview.endswith("...")
