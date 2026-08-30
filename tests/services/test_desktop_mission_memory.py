"""P1-2 cross-task memory deposition: sink round trip and key/body shape."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from app.services.desktop_mission_memory import (
    MISSION_MEMORY_BODY_SUMMARY_CHARS,
    DesktopMissionMemorySink,
    mission_memory_name,
)


def _memory_patches() -> tuple[ExitStack, Path]:
    tmp = tempfile.TemporaryDirectory()
    memory_dir = Path(tmp.name)
    stack = ExitStack()
    stack.callback(tmp.cleanup)
    stack.enter_context(patch("app.config.MEMORY_DIR", memory_dir))
    stack.enter_context(
        patch("app.services.tools.builtin_tools.MEMORY_DIR", memory_dir)
    )
    return stack, memory_dir


class MissionMemoryKeyTests(unittest.TestCase):
    def test_key_is_stable_per_mission(self) -> None:
        self.assertEqual(mission_memory_name("mis-abc123"), "mission-mis-abc123")
        self.assertEqual(
            mission_memory_name("mis-abc123"), mission_memory_name("mis-abc123")
        )


class DesktopMissionMemorySinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_mission_summary_persists_searchable_memory(self) -> None:
        stack, memory_dir = _memory_patches()
        with stack:
            sink = DesktopMissionMemorySink()
            saved = await sink.save_mission_summary(
                "mis-mem-1",
                objective="在工作区创建 hello.txt",
                summary="已创建 hello.txt，内容为问候语。",
            )
            self.assertTrue(saved)

            from app.services.tools.builtin_tools import memory_search_handler

            result = await memory_search_handler("mission-mis-mem-1")
            self.assertTrue(result.get("success"))
            names = [item["name"] for item in result["result"]["results"]]
            self.assertIn("mission-mis-mem-1", names)

            stored = (memory_dir / "mission-mis-mem-1.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("在工作区创建 hello.txt", stored)
            self.assertIn("已创建 hello.txt", stored)

    async def test_summary_body_is_truncated_to_500_chars(self) -> None:
        stack, memory_dir = _memory_patches()
        with stack:
            sink = DesktopMissionMemorySink()
            await sink.save_mission_summary(
                "mis-mem-2",
                objective="长任务",
                summary="s" * (MISSION_MEMORY_BODY_SUMMARY_CHARS + 1000),
            )
            stored = (
                memory_dir / "mission-mis-mem-2.md"
            ).read_text(encoding="utf-8")
            self.assertIn("objective: 长任务", stored)
            summary_part = stored.split("最终总结: ", 1)[1].strip()
            self.assertLessEqual(len(summary_part), MISSION_MEMORY_BODY_SUMMARY_CHARS)

    async def test_blank_summary_still_saves_objective(self) -> None:
        stack, memory_dir = _memory_patches()
        with stack:
            sink = DesktopMissionMemorySink()
            saved = await sink.save_mission_summary(
                "mis-mem-3",
                objective="只有目标没有总结",
                summary="",
            )
            self.assertTrue(saved)
            stored = (
                memory_dir / "mission-mis-mem-3.md"
            ).read_text(encoding="utf-8")
            self.assertIn("只有目标没有总结", stored)


if __name__ == "__main__":
    unittest.main()
