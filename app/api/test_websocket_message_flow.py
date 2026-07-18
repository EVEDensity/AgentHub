from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.api import websocket_message_flow as flow


def test_detect_multi_mention_greeting_strips_mentions() -> None:
    is_greeting, cleaned = flow.detect_multi_mention_greeting(
        "@A @B 大家好",
        ["A", "B"],
        route_selected=False,
    )

    assert is_greeting is True
    assert cleaned == "大家好"


def test_detect_multi_mention_greeting_rejects_technical_content() -> None:
    is_greeting, cleaned = flow.detect_multi_mention_greeting(
        "@A @B 请帮我改代码",
        ["A", "B"],
        route_selected=False,
    )

    assert is_greeting is False
    assert cleaned == "@A @B 请帮我改代码"


def test_build_dag_task_items_supports_objects_and_dicts() -> None:
    items = flow.build_dag_task_items(
        [
            SimpleNamespace(
                id="n1",
                agent="Architect",
                domain="architect",
                description="梳理方案",
                dependencies=["n0"],
                estimated_effort="high",
            ),
            {
                "id": "n2",
                "agent": "CodeGen",
                "domain": "codegen",
                "description": "实现代码",
                "dependencies": [],
                "estimated_effort": "low",
            },
        ]
    )

    assert items[0]["description"].startswith("Architect")
    assert items[0]["estimatedSeconds"] == 90
    assert items[1]["estimatedSeconds"] == 20


def test_build_followup_todos_includes_domain_actions() -> None:
    todos = flow.build_followup_todos(
        [
            {"agent_id": "CodeGen", "domain": "codegen"},
            {"agent_id": "Review", "domain": "review"},
            {"agent_id": "Deploy", "domain": "deploy"},
        ]
    )

    ids = [item["id"] for item in todos]
    assert ids == ["todo_test", "todo_fix", "todo_verify_deploy", "todo_feedback"]


def test_run_message_flow_uses_greeting_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_broadcast_greeting(**kwargs) -> None:
        seen["greeting"] = kwargs

    async def fail_collab(**kwargs) -> None:  # pragma: no cover - guard rail
        raise AssertionError("collaborative flow should not run")

    async def fail_single(**kwargs) -> None:  # pragma: no cover - guard rail
        raise AssertionError("single flow should not run")

    monkeypatch.setattr(flow, "_broadcast_greeting", fake_broadcast_greeting)
    monkeypatch.setattr(flow, "_run_collaborative_flow", fail_collab)
    monkeypatch.setattr(flow, "_run_single_message_flow", fail_single)

    asyncio.run(
        flow.run_message_flow(
            session_id="s1",
            content="@A @B 大家好",
            sender="Alice",
            user_id="u1",
            token=SimpleNamespace(cancelled=False),
            attachments=[],
            quote_references=[],
            auto_reply=True,
            target_agents=[{"agent_id": "A"}, {"agent_id": "B"}],
            route_dag=None,
            mentioned=["A", "B"],
            invoke_agent=lambda *args, **kwargs: None,
        )
    )

    assert seen["greeting"]["content"] == "大家好"
