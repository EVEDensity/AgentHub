"""Single durable context source for direct chat and Mission resume."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

class ContextStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.path = self.directory / "conversation.jsonl"

    def append(self, role: str, content: str, *, source: str = "direct", mission_id: str = "") -> None:
        if role not in {"user", "assistant"} or not str(content).strip():
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        item: dict[str, Any] = {"schemaVersion": 1, "role": role, "content": str(content).strip(), "source": source}
        if mission_id: item["missionId"] = mission_id
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def messages(self, limit: int = 40) -> list[dict[str, str]]:
        try: lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError: return []
        out = []
        for line in lines:
            try: item = json.loads(line)
            except (TypeError, ValueError): continue
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip():
                out.append({"role": str(item["role"]), "content": str(item["content"])})
        return out[-limit:]

    def render(self, limit: int = 8) -> str:
        return "\n".join(f"{m['role']}: {m['content']}" for m in self.messages(limit))

__all__ = ["ContextStore"]
