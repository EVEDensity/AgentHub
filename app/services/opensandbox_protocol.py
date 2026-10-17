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

    def __init__(self, message: str, *, code: str = "backend_failure",
                 http_status: int | None = None, upstream_code: str | None = None,
                 operation: str = "unknown") -> None:
        super().__init__(message)
        self.code = code if isinstance(code, str) and code in {
            "backend_failure", "http_error", "transport_error", "transport_timeout", "request_timeout",
        } else "backend_failure"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.upstream_code = (upstream_code if isinstance(upstream_code, str)
                              and upstream_code in UPSTREAM_ERROR_CODES else None)
        self.operation = operation if isinstance(operation, str) and operation in WIRE_OPERATIONS else "unknown"
        self.cleanup_error: dict | None = None

    def diagnostic(self) -> dict:
        """Only fixed local codes, an HTTP integer and exact upstream enums."""
        result = {"code": self.code, "operation": self.operation}
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.upstream_code is not None:
            result["upstream_code"] = self.upstream_code
        if self.cleanup_error is not None:
            result["cleanup"] = self.cleanup_error
        return result


# Exact 1.1.1 services/constants.py values. A pattern match would permit a
# reflected token or attacker-controlled string into reports. Never log messages.
UPSTREAM_ERROR_CODES = frozenset({
    "DOCKER::INITIALIZATION_ERROR", "DOCKER::SANDBOX_QUERY_FAILED", "DOCKER::SANDBOX_NOT_FOUND",
    "DOCKER::SANDBOX_IMAGE_PULL_FAILED", "DOCKER::SANDBOX_START_FAILED", "DOCKER::SANDBOX_DELETE_FAILED",
    "DOCKER::SANDBOX_NOT_RUNNING", "DOCKER::SANDBOX_EXECD_START_FAILED",
    "DOCKER::SANDBOX_EXECD_DISTRIBUTION_FAILED", "DOCKER::SANDBOX_BOOTSTRAP_INSTALL_FAILED",
    "DOCKER::INVALID_ENTRYPOINT", "DOCKER::INVALID_PORT", "DOCKER::NETWORK_MODE_ENDPOINT_UNAVAILABLE",
    "SANDBOX::UNKNOWN_ERROR", "SANDBOX::API_NOT_SUPPORTED", "SANDBOX::INVALID_METADATA_LABEL",
    "SANDBOX::INVALID_PARAMETER", "SANDBOX::INTERNAL_ERROR",
})
WIRE_OPERATIONS = frozenset({
    "unknown", "sandbox_create", "sandbox_list", "sandbox_get", "sandbox_destroy", "sandbox_ready",
    "workspace_initialize", "sandbox_ping", "command_start", "command_cancel", "command_status",
    "command_logs", "file_upload", "file_download", "network_policy",
})


def wire_operation(method: str, path: str) -> str:
    """Translate a route to a fixed name without reporting identities or URLs."""
    if path == "sandboxes":
        return "sandbox_create" if method == "POST" else "sandbox_list"
    if len(path.split("/")) == 2:
        return "sandbox_destroy" if method == "DELETE" else "sandbox_get"
    suffixes = {"/ping": "sandbox_ping", "/logs": "command_logs", "/files/upload": "file_upload",
                "/files/download": "file_download", "/networkpolicy": "network_policy"}
    for suffix, name in suffixes.items():
        if path.endswith(suffix):
            return name
    if "/command/status/" in path:
        return "command_status"
    if path.endswith("/command"):
        return "command_cancel" if method == "DELETE" else "command_start"
    return "unknown"


def upstream_error_code(data: bytes) -> str | None:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        return None
    code = value.get("code") if isinstance(value, dict) else None
    return code if isinstance(code, str) and code in UPSTREAM_ERROR_CODES else None


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
    create_timeout_seconds: float = 90
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
        bounded_seconds(self.create_timeout_seconds, 180)
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
