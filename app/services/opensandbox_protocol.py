"""Bounded wire helpers for the experimental OpenSandbox 1.1.1 HTTP port.

Only the pinned lifecycle and Execd contracts are implemented. These are not
Mission DTOs and confer no execution authority.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit


class OpenSandboxError(RuntimeError):
    """Sanitized backend failure; never contains remote bodies or credentials."""


WORKLOAD_IMAGE = (
    "docker.io/library/ubuntu:22.04@sha256:"
    "0a9887931b0e38af2e3487ed39e29436b0535c4f3f52e57745baaf79d1898956"
)
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MAX_BODY = 1024 * 1024


def identifier(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise OpenSandboxError("invalid backend identity")
    return value


def bounded_seconds(value: float, maximum: float = 300) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
        raise OpenSandboxError("invalid backend deadline")
    return value


def endpoint(value: str) -> str:
    """Require an explicit origin; plaintext transport is loopback-only."""
    try:
        parts = urlsplit(value)
        host = parts.hostname
        _ = parts.port
    except ValueError:
        raise OpenSandboxError("invalid backend endpoint") from None
    if (not host or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.path not in ("", "/")):
        raise OpenSandboxError("backend endpoint must be an origin")
    loopback = host.lower() == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise OpenSandboxError("backend requires HTTPS or loopback HTTP")
    return value.rstrip("/")


@dataclass(frozen=True)
class OpenSandboxConfig:
    endpoint: str
    api_key: str = field(repr=False)
    enabled: bool = False
    lifetime_seconds: int = 180
    request_timeout_seconds: float = 15
    ready_timeout_seconds: float = 90

    def __post_init__(self) -> None:
        endpoint(self.endpoint)
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise OpenSandboxError("backend authentication is required")
        if any(ord(char) < 33 or ord(char) > 126 for char in self.api_key):
            raise OpenSandboxError("invalid backend authentication")
        if type(self.lifetime_seconds) is not int:
            raise OpenSandboxError("sandbox lifetime must be integral seconds")
        bounded_seconds(self.lifetime_seconds)
        bounded_seconds(self.request_timeout_seconds, 60)
        bounded_seconds(self.ready_timeout_seconds)


@dataclass(frozen=True)
class CommandResult:
    execution_id: str
    exit_code: int
    output: str


@dataclass(frozen=True)
class TransferredFile:
    path: str
    size_bytes: int
    sha256: str


def workspace_path(relative: str) -> str:
    if (not isinstance(relative, str) or not relative or len(relative) > 1024
            or "\\" in relative or ":" in relative or any(ord(char) < 32 for char in relative)):
        raise OpenSandboxError("invalid workspace path")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(part in ("", ".", "..") for part in relative.split("/")):
        raise OpenSandboxError("invalid workspace path")
    return str(PurePosixPath("/workspace") / parts)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_object(data: bytes) -> dict:
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        raise OpenSandboxError("invalid backend JSON") from None
    if not isinstance(result, dict):
        raise OpenSandboxError("invalid backend JSON object")
    return result


def execution_from_sse(data: bytes) -> str:
    """Background start requires exactly one well-formed init event."""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise OpenSandboxError("invalid backend event stream") from None
    found: list[str] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        payload = "\n".join(line[5:].lstrip(" ") for line in block.splitlines()
                            if line.startswith("data:"))
        if not payload:
            continue
        event = json_object(payload.encode())
        if event.get("type") == "error":
            raise OpenSandboxError("backend rejected command start")
        if event.get("type") == "init":
            found.append(identifier(event.get("text")))
    if len(found) != 1:
        raise OpenSandboxError("backend did not confirm command identity")
    return found[0]


def terminal_status(data: dict, execution_id: str) -> int | None:
    if data.get("id") != execution_id or type(data.get("running")) is not bool:
        raise OpenSandboxError("invalid command status identity")
    if data["running"]:
        return None
    code = data.get("exit_code")
    error = data.get("error")
    finished = data.get("finished_at")
    if type(code) is not int or not isinstance(finished, str):
        raise OpenSandboxError("backend did not confirm command terminal state")
    try:
        if datetime.fromisoformat(finished).tzinfo is None:
            raise ValueError
    except ValueError:
        raise OpenSandboxError("invalid command finish timestamp") from None
    if (error is not None and not isinstance(error, str)) or (code == 0 and error):
        raise OpenSandboxError("inconsistent command terminal error")
    return code
