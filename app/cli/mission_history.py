"""Shared Mission history rendering for the interactive CLI."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _print_missions(missions: list[dict[str, Any]], emit: Callable[..., None]) -> None:
    if not missions:
        emit("  （暂无历史任务）")
        return
    emit(f"  {'MISSION ID':36} {'STATUS':12} OBJECTIVE")
    for mission in missions:
        mission_id = str(mission.get("id") or "")[:34]
        status = str(mission.get("status") or "")[:12]
        lines = str(mission.get("objective") or "").splitlines()
        summary = (lines[0] if lines else "")[:52]
        emit(f"  {mission_id:36} {status:12} {summary}")
