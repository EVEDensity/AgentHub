"""Cross-task memory deposition for the desktop local runner (P1-2).

After the unattended verifier submits PASS Evidence, the controller
persists one automatic memory entry per Mission (``mission-<id>``) so
future tasks can recall it through the whitelisted ``memory_search``
tool. The body keeps the Mission objective plus the first 500 characters
of the final model summary (the ``report`` Artifact the runner
published).
"""

from __future__ import annotations

from typing import Any, Protocol

MISSION_MEMORY_BODY_SUMMARY_CHARS = 500


def mission_memory_name(mission_id: str) -> str:
    """Stable memory key slug for one Mission."""
    return f"mission-{mission_id}"


class MissionMemorySinkPort(Protocol):
    """Persistence boundary so the controller stays storage-free."""

    async def save_mission_summary(
        self,
        mission_id: str,
        *,
        objective: str,
        summary: str,
    ) -> bool: ...


class DesktopMissionMemorySink:
    """Writes mission summaries through the built-in memory_save handler."""

    async def save_mission_summary(
        self,
        mission_id: str,
        *,
        objective: str,
        summary: str,
    ) -> bool:
        from app.services.tools.builtin_tools import memory_save_handler

        body = (
            f"objective: {objective}\n\n最终总结: "
            f"{summary[:MISSION_MEMORY_BODY_SUMMARY_CHARS]}"
        )
        result: dict[str, Any] = await memory_save_handler(
            name=mission_memory_name(mission_id),
            content=body,
            type="project",
            description=f"任务 mission:{mission_id} 的自动沉淀记忆（PASS 后生成）",
        )
        return bool(result.get("success"))


__all__ = [
    "MISSION_MEMORY_BODY_SUMMARY_CHARS",
    "DesktopMissionMemorySink",
    "MissionMemorySinkPort",
    "mission_memory_name",
]
