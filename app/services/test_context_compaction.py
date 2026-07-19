from __future__ import annotations

from app.services.context_compaction import (
    build_agent_roster_summary,
    build_preprocess_context,
    build_result_preview,
    build_task_preview_item,
)


def test_build_preprocess_context_is_compact() -> None:
    text = build_preprocess_context(
        {
            "intent_type": "technical_development",
            "clarified_question": "please design a scalable enterprise multi-agent platform with task orchestration",
            "requirements": ["multi-agent collaboration", "enterprise self-hosting"],
            "non_functional_requirements": ["low coupling", "reduce token usage"],
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
                {"id": 1, "domain": "architect", "title": "split architecture", "depends_on": []},
            ],
            "constraints": ["self-hosted"],
            "routing": {"execution_order": ["Architect", "CodeGen"]},
        }
    )

    assert "intent=technical_development" in text
    assert "route=Architect->CodeGen" in text
    assert "requirements=" in text


def test_build_task_preview_item_is_short() -> None:
    item = build_task_preview_item(
        {
            "id": "n1",
            "agent": "Architect",
            "description": "A long task description that should be compacted for preview payloads and prompts",
            "dependencies": ["n0"],
            "estimated_effort": "high",
        }
    )

    assert item["description"].startswith("A long task description")
    assert item["estimatedSeconds"] == 90


def test_build_task_preview_item_caps_dependencies_and_text() -> None:
    item = build_task_preview_item(
        {
            "id": "node_with_a_very_long_identifier_that_should_be_shortened",
            "agent": "ArchitectAgentWithVerboseName",
            "description": "x" * 160,
            "dependencies": ["dep-1", "dep-2", "dep-3", "dep-4"],
            "estimated_effort": "medium",
        }
    )

    assert len(item["id"]) <= 18
    assert len(item["agent"]) <= 18
    assert len(item["description"]) <= 56
    assert item["dependencies"] == ["dep-1", "dep-2", "dep-3"]


def test_build_agent_roster_summary_defaults_are_short() -> None:
    agents = [
        {
            "agent_id": f"Agent{i}",
            "domain": "architecture",
            "risk_level": "L2",
            "status": "active",
            "duty_note": "x" * 120,
            "capability_tags": ["plan", "code", "review", "extra"],
        }
        for i in range(8)
    ]

    summary = build_agent_roster_summary(agents)

    assert summary.count("\n- ") == 7
    assert "+2 agents" in summary
    assert "|t=plan,code,review" in summary


def test_build_result_preview_trims_long_text() -> None:
    preview = build_result_preview("x" * 500, max_chars=80)

    assert len(preview) <= 80
    assert preview.endswith("...")
