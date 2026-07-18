from __future__ import annotations

from app.services.conversation_history import build_conversation_history_transcript


def test_build_conversation_history_transcript_keeps_chronological_order() -> None:
    transcript = build_conversation_history_transcript(
        [
            {"sender": "assistant", "content": "second"},
            {"sender": "user", "content": "first"},
        ],
        max_chars=10_000,
        max_messages=10,
    )

    assert transcript.startswith("【会话历史】")
    assert transcript.index("first") < transcript.index("second")


def test_build_conversation_history_transcript_truncates_long_messages() -> None:
    transcript = build_conversation_history_transcript(
        [{"sender": "user", "content": "x" * 700}],
        max_chars=10_000,
        max_messages=10,
        max_message_chars=100,
    )

    assert len(transcript) < 200
    assert transcript.endswith("...")


def test_build_conversation_history_transcript_handles_empty_input() -> None:
    assert build_conversation_history_transcript([]) == ""
