"""Single durable context source for direct chat and Mission resume."""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ContextRecord:
    source: str
    source_id: str
    mission_id: str
    event_id: str
    role: str
    content: str
    created_at: str


class ContextStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.path = self.directory / "conversation.jsonl"

    def append(
        self,
        role: str,
        content: str,
        *,
        source: str = "direct",
        source_id: str = "",
        mission_id: str = "",
        event_id: str = "",
    ) -> None:
        if role not in {"user", "assistant"} or not str(content).strip():
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        item: dict[str, Any] = {
            "schemaVersion": 1,
            "role": role,
            "content": str(content).strip(),
            "source": source,
            "sourceId": source_id or "conversation",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        if mission_id:
            item["missionId"] = mission_id
        if event_id:
            item["eventId"] = event_id
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def messages(self, limit: int = 40) -> list[dict[str, str]]:
        return [
            {"role": record.role, "content": record.content}
            for record in self.records(limit=limit)
        ]

    def records(self, limit: int = 40) -> list[ContextRecord]:
        """Read valid context records while tolerating a truncated JSONL tail."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[ContextRecord] = []
        for line in lines:
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if (
                isinstance(item, dict)
                and item.get("role") in {"user", "assistant"}
                and str(item.get("content") or "").strip()
            ):
                out.append(
                    ContextRecord(
                        source=str(item.get("source") or "direct"),
                        source_id=str(item.get("sourceId") or "conversation"),
                        mission_id=str(item.get("missionId") or ""),
                        event_id=str(item.get("eventId") or ""),
                        role=str(item["role"]),
                        content=str(item["content"]),
                        created_at=str(
                            item.get("createdAt") or item.get("timestamp") or ""
                        ),
                    )
                )
        return out[-limit:]

    def set_resume(self, mission_id: str) -> None:
        self._append_session_event("resume.set", mission_id)

    def clear_resume(self) -> None:
        self._append_session_event("resume.clear", "")

    def resume_id(self) -> str | None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        current: str | None = None
        for line in lines:
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(item, dict) and item.get("recordType") == "session":
                if item.get("event") == "resume.set":
                    current = str(item.get("missionId") or "") or None
                elif item.get("event") == "resume.clear":
                    current = None
        return current

    def _append_session_event(self, event: str, mission_id: str) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            item = {
                "schemaVersion": 1,
                "recordType": "session",
                "event": event,
            }
            if mission_id:
                item["missionId"] = mission_id
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError:
            return

    def render(self, limit: int = 8) -> str:
        return "\n".join(
            f"{message['role']}: {message['content']}"
            for message in self.messages(limit)
        )


__all__ = ["ContextRecord", "ContextStore"]
