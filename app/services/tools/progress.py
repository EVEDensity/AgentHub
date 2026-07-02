from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class ToolProgressData:
    """Progress update from a running tool execution.

    Modeled on FUNCTION_CALLING_IMPLEMENTATION.md §9.2:
    ``ToolProgressData`` union type.

    Different ``type`` values carry different payload fields:
      - ``"progress"``: message + optional percentage
      - ``"read_progress"``: file + bytes_read + total_bytes (for file_read)
      - ``"bash_progress"``: running bool + output (for code_execute/bash)
    """
    type: str  # "progress" | "read_progress" | "bash_progress"
    message: str = ""
    percentage: float | None = None  # 0.0–1.0

    # read_progress-specific
    file: str = ""
    bytes_read: int = 0
    total_bytes: int = 0

    # bash_progress-specific
    running: bool = False
    output: str = ""


class ProgressTracker:
    """Tracks progress for long-running tool executions.

    Tool handlers can optionally use this tracker to report incremental
    progress instead of just returning a final result. The tracker
    invokes registered callbacks which typically broadcast WebSocket
    events to the frontend.

    Usage in a tool handler:
        tracker = context.get("progress_tracker")
        if tracker:
            tracker.report("web_search", ToolProgressData(
                type="progress", message="Fetching results...", percentage=0.5,
            ))
    """

    def __init__(self) -> None:
        self._callbacks: list[
            Callable[[str, ToolProgressData], Awaitable[None]]
        ] = []

    def on_progress(
        self,
        callback: Callable[[str, ToolProgressData], Awaitable[None]],
    ) -> None:
        """Register a progress callback.

        The callback receives (tool_name, ToolProgressData).
        """
        self._callbacks.append(callback)

    async def report(self, tool_name: str, data: ToolProgressData) -> None:
        """Report progress for a tool. Invokes all registered callbacks."""
        for cb in self._callbacks:
            try:
                await cb(tool_name, data)
            except Exception:
                pass  # best-effort; progress callbacks must not break execution

    def remove_callback(
        self,
        callback: Callable[[str, ToolProgressData], Awaitable[None]],
    ) -> None:
        """Remove a previously registered callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)


# ── Singleton ──────────────────────────────────────────────────────────
progress_tracker = ProgressTracker()
