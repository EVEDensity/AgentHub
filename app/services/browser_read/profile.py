"""Frozen operator runtime and origin policy for anonymous navigation."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

PLAYWRIGHT_VERSION = "1.61.1"
CHROMIUM_VERSION = "149.0.7827.55"
UPSTREAM_REVISION = "39e3553a4f283a41134d75d7e404484bd9e6865a"
PROFILE_KIND = "playwright-anonymous-read.v1"
RUNTIME_FILES = ("worker.mjs", "policy.mjs", "package-lock.json")
MAX_PROCESS_OUTPUT_BYTES = 1_048_576
MAX_WORKDIR_BYTES = 134_217_728


class BrowserReadError(RuntimeError):
    """Static safe failure; underlying process output is never an exception."""


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def canonical_json(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _address(value: str) -> bool:
    return value in {"127.0.0.1", "::1"}


def canonical_url(value: str, *, allow_http_loopback: bool = False) -> str:
    parsed = _parsed_url(value)
    _validate_url_policy(parsed, allow_http_loopback)
    return urlunsplit(
        (parsed.scheme, _authority(parsed), parsed.path or "/", parsed.query, "")
    )


def _parsed_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise BrowserReadError("browser URL is invalid")
    if re.fullmatch(r"[A-Za-z0-9._~:/?@!$&()*+,;=%\[\]-]+", value) is None:
        raise BrowserReadError("browser URL must be bounded visible ASCII")
    invalid = False
    try:
        parsed = urlsplit(value)
        _port = parsed.port
    except ValueError:
        invalid = True
    if invalid:
        raise BrowserReadError("browser URL is invalid")
    return parsed


def _validate_url_policy(parsed, allow_http_loopback):
    hostname = parsed.hostname
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise BrowserReadError("browser URL refuses credentials and fragments")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and allow_http_loopback and _address(hostname)
    ):
        raise BrowserReadError("browser URL scheme is not allowed")
    _validate_host(hostname)
    if any(
        segment.lower() in {".", ".."} for segment in _decoded_segments(parsed.path)
    ):
        raise BrowserReadError("browser URL dot segments are unsupported")
    if re.search(r"%(?![a-fA-F0-9]{2})", parsed.path + parsed.query):
        raise BrowserReadError("browser URL percent encoding is invalid")


def _decoded_segments(path):
    invalid = False
    try:
        segments = tuple(
            unquote(segment, errors="strict") for segment in path.split("/")
        )
    except UnicodeError:
        invalid = True
    if invalid:
        raise BrowserReadError("browser URL path encoding must be valid UTF-8")
    return segments


def _authority(parsed):
    host, port = parsed.hostname.lower(), parsed.port
    host = f"[{host}]" if ":" in host else host
    default = 443 if parsed.scheme == "https" else 80
    return host if port is None or port == default else f"{host}:{port}"


def _validate_host(hostname):
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        if str(address) != hostname.lower():
            raise BrowserReadError("browser IP address is not canonical")
    elif (
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", hostname)
        is None
        or hostname.rsplit(".", 1)[-1].isdecimal()
    ):
        raise BrowserReadError("browser URL host is invalid")


def canonical_origin(value: str, *, allow_http_loopback: bool = False) -> str:
    canonical = canonical_url(value, allow_http_loopback=allow_http_loopback)
    parsed = urlsplit(canonical)
    if parsed.path != "/" or parsed.query:
        raise BrowserReadError("browser origins must not contain paths or queries")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if value != origin:
        raise BrowserReadError(
            "browser origin must be canonical without a trailing slash"
        )
    return origin


def _regular(path: Path, maximum: int) -> bytes:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise BrowserReadError("browser runtime requires regular absolute files")
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if not content or len(content) > maximum:
        raise BrowserReadError("browser runtime files exceed their byte bound")
    return content


def runtime_digest(directory: Path) -> str:
    return digest(
        canonical_json(
            {
                name: digest(_regular(directory / name, 2_097_152))
                for name in RUNTIME_FILES
            }
        )
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class BrowserReadProfile:
    node_path: Path
    worker_dir: Path
    origins: tuple[str, ...]
    browser_cache_path: Path | None = None
    allow_http_loopback: bool = False
    max_text_chars: int = 16_384
    timeout_ms: int = 15_000
    max_reads: int = 4
    max_model_output_bytes: int = 65_536
    cleanup_timeout_seconds: float = 5.0
    runtime_sha256: str = field(init=False)
    node_sha256: str = field(init=False)
    digest: str = field(init=False)

    def __post_init__(self):
        self._validate_limits()
        if not isinstance(self.node_path, Path) or not isinstance(
            self.worker_dir, Path
        ):
            raise BrowserReadError("browser runtime paths must be operator Path values")
        if (
            not self.worker_dir.is_absolute()
            or self.worker_dir.is_symlink()
            or not self.worker_dir.is_dir()
        ):
            raise BrowserReadError("browser worker directory is invalid")
        if self.browser_cache_path is not None and (
            not isinstance(self.browser_cache_path, Path)
            or not self.browser_cache_path.is_absolute()
        ):
            raise BrowserReadError("browser cache path must be explicit and absolute")
        if not isinstance(self.origins, tuple) or not 1 <= len(self.origins) <= 8:
            raise BrowserReadError("browser origins require an explicit bounded tuple")
        origins = tuple(
            sorted(
                {
                    canonical_origin(item, allow_http_loopback=self.allow_http_loopback)
                    for item in self.origins
                }
            )
        )
        if len(origins) != len(self.origins):
            raise BrowserReadError("browser origins must be unique")
        object.__setattr__(self, "origins", origins)
        object.__setattr__(self, "runtime_sha256", runtime_digest(self.worker_dir))
        object.__setattr__(
            self, "node_sha256", digest(_regular(self.node_path, 268_435_456))
        )
        object.__setattr__(self, "digest", digest(canonical_json(self._identity())))

    def _validate_limits(self):
        limits = (
            (self.max_text_chars, 256, 65_536),
            (self.timeout_ms, 1, 30_000),
            (self.max_reads, 1, 32),
            (self.max_model_output_bytes, 1, 1_048_576),
        )
        if any(
            type(value) is not int or not lower <= value <= upper
            for value, lower, upper in limits
        ):
            raise BrowserReadError("browser profile limits are invalid")
        if type(self.allow_http_loopback) is not bool:
            raise BrowserReadError("browser loopback mode requires an explicit boolean")
        cleanup = self.cleanup_timeout_seconds
        if (
            type(cleanup) not in (int, float)
            or not math.isfinite(cleanup)
            or not 0 < cleanup <= 15
        ):
            raise BrowserReadError("browser cleanup deadline is invalid")

    def _identity(self):
        return {
            "profile": PROFILE_KIND,
            "playwrightVersion": PLAYWRIGHT_VERSION,
            "browserVersion": CHROMIUM_VERSION,
            "upstreamRevision": UPSTREAM_REVISION,
            "runtimeSha256": self.runtime_sha256,
            "nodeSha256": self.node_sha256,
            "origins": self.origins,
            "allowHttpLoopback": self.allow_http_loopback,
            "maxTextChars": self.max_text_chars,
            "timeoutMs": self.timeout_ms,
            "maxReads": self.max_reads,
            "maxModelOutputBytes": self.max_model_output_bytes,
            "cleanupTimeoutSeconds": self.cleanup_timeout_seconds,
            "maxProcessOutputBytes": MAX_PROCESS_OUTPUT_BYTES,
            "maxWorkdirBytes": MAX_WORKDIR_BYTES,
        }

    def validate_url(self, url: str) -> str:
        value = canonical_url(url, allow_http_loopback=self.allow_http_loopback)
        parsed = urlsplit(value)
        if f"{parsed.scheme}://{parsed.netloc}" not in self.origins:
            raise BrowserReadError("browser URL is outside the operator origin profile")
        return value

    def verify_runtime(self) -> None:
        if (
            runtime_digest(self.worker_dir) != self.runtime_sha256
            or digest(_regular(self.node_path, 268_435_456)) != self.node_sha256
        ):
            raise BrowserReadError("browser runtime changed after operator selection")

    def scope(self) -> dict:
        return {
            "profile": PROFILE_KIND,
            "profileDigest": self.digest,
            "origins": list(self.origins),
        }
