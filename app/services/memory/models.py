from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class MemoryType(str, Enum):
    """Legacy content category retained for API and file compatibility."""

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


MEMORY_TYPE_DESCRIPTIONS = {
    MemoryType.USER: "用户角色、目标、技能与偏好",
    MemoryType.FEEDBACK: "用户对工作方式的纠正或肯定",
    MemoryType.PROJECT: "无法从代码推导的项目上下文",
    MemoryType.REFERENCE: "指向外部系统的指针",
}


class CognitiveMemoryType(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class MemoryScope(str, Enum):
    REQUEST = "request"
    SESSION = "session"
    USER = "user"
    TEAM = "team"
    AGENT = "agent"
    TENANT = "tenant"
    GLOBAL = "global"


@dataclass
class MemoryMeta:
    """YAML frontmatter of a memory file."""

    name: str
    description: str
    type: MemoryType
    memory_type: CognitiveMemoryType = CognitiveMemoryType.SEMANTIC
    scope: MemoryScope = MemoryScope.USER
    source: str = "manual"
    version: int = 1
    created_at: str = ""
    updated_at: str = ""

    def to_frontmatter(self) -> str:
        lines = ["---"]
        lines.append(f'name: {self.name}')
        lines.append(f'description: {self.description}')
        lines.append(f'type: {self.type.value}')
        lines.append(f'memory_type: {self.memory_type.value}')
        lines.append(f'scope: {self.scope.value}')
        lines.append(f'source: {self.source}')
        lines.append(f'version: {max(1, self.version)}')
        if self.created_at:
            lines.append(f'created_at: {self.created_at}')
        if self.updated_at:
            lines.append(f'updated_at: {self.updated_at}')
        lines.append("---")
        return "\n".join(lines)

    @classmethod
    def from_frontmatter(cls, text: str, filename: str = "") -> Optional["MemoryMeta"]:
        """Parse YAML frontmatter from markdown text.

        Supports the format:
        ---
        name: ...
        description: ...
        type: user|feedback|project|reference
        ---
        """
        m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if not m:
            return None
        raw = m.group(1)
        fields: dict[str, str] = {}
        for line in raw.split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                fields[key.strip()] = val.strip()
        name = fields.get("name", filename.replace(".md", "") if filename else "")
        desc = fields.get("description", "")
        raw_type = fields.get("type", "reference")
        try:
            mem_type = MemoryType(raw_type)
        except ValueError:
            mem_type = MemoryType.REFERENCE
        try:
            cognitive_type = CognitiveMemoryType(fields.get("memory_type", "semantic"))
        except ValueError:
            cognitive_type = CognitiveMemoryType.SEMANTIC
        try:
            scope = MemoryScope(fields.get("scope", "user"))
        except ValueError:
            scope = MemoryScope.USER
        try:
            version = max(1, int(fields.get("version", "1")))
        except ValueError:
            version = 1
        return cls(
            name=name,
            description=desc,
            type=mem_type,
            memory_type=cognitive_type,
            scope=scope,
            source=fields.get("source", "legacy-file"),
            version=version,
            created_at=fields.get("created_at", ""),
            updated_at=fields.get("updated_at", ""),
        )


@dataclass
class MemoryHeader:
    """Lightweight header returned by scanMemoryFiles()."""

    filename: str
    path: str
    mtime: float
    description: str
    type: MemoryType
    memory_type: CognitiveMemoryType = CognitiveMemoryType.SEMANTIC
    scope: MemoryScope = MemoryScope.USER
    source: str = "legacy-file"
    version: int = 1
    name: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class MemoryDocument:
    """Full memory document: frontmatter + body."""

    meta: MemoryMeta
    body: str
    file_path: str = ""

    def to_markdown(self) -> str:
        return f"{self.meta.to_frontmatter()}\n\n{self.body.strip()}\n"

    @classmethod
    def parse(cls, content: str, file_path: str = "") -> "MemoryDocument":
        """Parse a complete .md file into frontmatter + body."""
        meta = MemoryMeta.from_frontmatter(content) or MemoryMeta(
            name="untitled", description="", type=MemoryType.REFERENCE
        )
        # Strip frontmatter to get body
        body = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", content, count=1, flags=re.DOTALL).strip()
        return cls(meta=meta, body=body, file_path=file_path)


def sanitize_filename(name: str) -> str:
    """Convert a name to a safe filename."""
    safe = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff\-]", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    if not safe:
        safe = "memory"
    return safe + ".md"
