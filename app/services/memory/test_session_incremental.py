# Tests for the L1 incremental fold (ADR-0107 change-only rollup).
from __future__ import annotations

import unittest

from app.services.memory.session_memory import (
    SESSION_SUMMARY_INCREMENTAL_PROMPT,
    SessionMemoryManager,
)


def _msg(seq: int) -> dict:
    return {"id": f"m{seq}", "sender": "user", "content": f"turn {seq}"}


class MessagesAfterCursorTests(unittest.TestCase):
    def test_no_cursor_returns_everything(self) -> None:
        self.assertEqual(
            SessionMemoryManager._messages_after([_msg(1), _msg(2)], ""),
            [_msg(1), _msg(2)],
        )

    def test_cursor_returns_strict_suffix(self) -> None:
        messages = [_msg(1), _msg(2), _msg(3), _msg(4)]
        self.assertEqual(
            [m["id"] for m in SessionMemoryManager._messages_after(messages, "m2")],
            ["m3", "m4"],
        )

    def test_missing_cursor_fails_open(self) -> None:
        # Cursor id not in the fetched set: must not silently drop data.
        messages = [_msg(1), _msg(2)]
        self.assertEqual(
            [m["id"] for m in SessionMemoryManager._messages_after(messages, "ghost")],
            ["m1", "m2"],
        )

    def test_cursor_at_end_returns_empty(self) -> None:
        messages = [_msg(1), _msg(2)]
        self.assertEqual(SessionMemoryManager._messages_after(messages, "m2"), [])


class IncrementalInputTests(unittest.TestCase):
    TEST_SUMMARY = "既有：用户要实现 REST API，已确定 JWT 认证方案。"

    def test_empty_new_messages_returns_none(self) -> None:
        result = SessionMemoryManager._build_incremental_input(self.TEST_SUMMARY, [])
        self.assertIsNone(result)

    def test_composes_prompt_with_existing_summary_and_new_turns(self) -> None:
        result = SessionMemoryManager._build_incremental_input(
            self.TEST_SUMMARY, [_msg(5), _msg(6)]
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("既有摘要", result)
        self.assertIn("JWT 认证", result)
        self.assertIn("turn 5", result)
        self.assertIn("turn 6", result)
        # Regression guard: the prompt template drives the fold behaviour.
        self.assertIn("新增对话", SESSION_SUMMARY_INCREMENTAL_PROMPT)
        self.assertIn("既有摘要", SESSION_SUMMARY_INCREMENTAL_PROMPT)

    def test_result_contains_only_new_turns_not_old_ones(self) -> None:
        result = SessionMemoryManager._build_incremental_input(
            self.TEST_SUMMARY, [_msg(8)]
        )
        assert result is not None
        self.assertIn("turn 8", result)
        self.assertNotIn("turn 3", result)  # history lives in the digest, not the prompt
        self.assertIn(self.TEST_SUMMARY[:40], result)


if __name__ == "__main__":
    unittest.main()