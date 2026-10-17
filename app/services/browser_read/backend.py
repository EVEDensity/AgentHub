"""Bounded Linux subprocess adapter for one fresh Node/Chromium read."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.services.trusted_tests.process import ProcessResult

from .process import LinuxBrowserProcessBackend
from .process_cleanup import OWNER_ENV, confirmed_cleanup
from .profile import (
    CHROMIUM_VERSION,
    MAX_PROCESS_OUTPUT_BYTES,
    MAX_WORKDIR_BYTES,
    PLAYWRIGHT_VERSION,
    BrowserReadError,
    BrowserReadProfile,
    canonical_json,
    digest,
)


class BrowserReadResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, populate_by_name=True
    )
    schema_: Literal["agenthub.browser-read.v1"] = Field(alias="schema")
    requestId: str
    success: StrictBool
    errorCode: Annotated[str, Field(pattern=r"^[a-z_]{1,64}$")] | None
    requestedUrl: Annotated[str, Field(max_length=2048)]
    finalUrl: Annotated[str, Field(max_length=2048)] | None
    httpStatus: Annotated[StrictInt, Field(ge=100, le=599)] | None
    title: Annotated[str, Field(max_length=4096)]
    text: Annotated[str, Field(max_length=65_536)]
    textSha256: Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
    textBytes: Annotated[StrictInt, Field(ge=0)]
    truncated: StrictBool
    blockedResourceCount: Annotated[StrictInt, Field(ge=0)]
    redirectRejected: StrictBool
    playwrightVersion: Literal[PLAYWRIGHT_VERSION]
    browserVersion: Annotated[str, Field(max_length=255)]
    workerPid: Annotated[StrictInt, Field(ge=1)] | None
    browserPid: Annotated[StrictInt, Field(ge=1)] | None
    cleanupConfirmed: StrictBool

    def to_public_dict(self) -> dict:
        return self.model_dump(by_alias=True)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise BrowserReadError("browser worker repeated a result field")
        value[key] = item
    return value


def _reject_constant(_value):
    raise BrowserReadError("browser worker emitted a nonfinite value")


def parse_worker_result(
    process: ProcessResult,
    *,
    request_id: str,
    url: str,
    profile: BrowserReadProfile,
    cleanup_confirmed: bool,
) -> BrowserReadResult:
    if process.timed_out or process.cancelled or process.limit_exceeded:
        raise BrowserReadError("browser process did not complete within its limits")
    invalid = False
    try:
        value = json.loads(
            process.output.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=_reject_constant,
        )
        result = BrowserReadResult.model_validate(value)
        _validate_result(result, request_id, url, profile, process.exit_code)
    except (ValueError, TypeError):
        invalid = True
    if invalid:
        raise BrowserReadError("browser worker result is invalid")
    if not cleanup_confirmed:
        raise BrowserReadError("browser owned process cleanup was not confirmed")
    return result


def _validate_result(result, request_id, url, profile, exit_code):
    raw = result.text.encode("utf-8")
    if result.requestId != request_id or result.requestedUrl != url:
        raise BrowserReadError("browser result identity differs from its request")
    if len(result.text) > profile.max_text_chars or result.textSha256 != digest(raw):
        raise BrowserReadError("browser text differs from its bounded digest")
    if result.textBytes < len(raw) or result.truncated != (result.textBytes > len(raw)):
        raise BrowserReadError("browser text byte facts are inconsistent")
    if result.finalUrl is not None and profile.validate_url(result.finalUrl) != url:
        raise BrowserReadError("browser final URL differs from its no-redirect request")
    if re.fullmatch(r"[\x20-\x7e]*", result.browserVersion) is None:
        raise BrowserReadError("browser version is invalid")
    _validate_success(result, exit_code)


def _validate_success(result, exit_code):
    if result.success:
        valid = (
            exit_code == 0,
            result.errorCode is None,
            result.cleanupConfirmed,
            result.finalUrl is not None,
            result.httpStatus is not None and 200 <= result.httpStatus < 300,
            not result.redirectRejected,
            result.browserVersion == CHROMIUM_VERSION,
        )
        if not all(valid):
            raise BrowserReadError(
                "browser successful result contradicts its observations"
            )
    elif exit_code != 1 or result.errorCode is None:
        raise BrowserReadError("browser failed result contradicts its process exit")


def child_environment(
    root: Path, owner: str, profile: BrowserReadProfile
) -> dict[str, str]:
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", OWNER_ENV: owner}
    for variable, relative in (
        ("HOME", "home"),
        ("TMPDIR", "tmp"),
        ("TMP", "tmp"),
        ("TEMP", "tmp"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_RUNTIME_DIR", "runtime"),
    ):
        path = root / relative
        path.mkdir(exist_ok=True, mode=0o700)
        environment[variable] = str(path)
    if profile.browser_cache_path is not None:
        environment["PLAYWRIGHT_BROWSERS_PATH"] = str(profile.browser_cache_path)
    return environment


class LocalBrowserReadBackend:
    """Operator-only; no remote dependency installation or fallback execution."""

    def __init__(self, profile: BrowserReadProfile, *, process_backend=None):
        if sys.platform != "linux":
            raise BrowserReadError(
                "browser subprocess backend currently requires Linux"
            )
        if not isinstance(profile, BrowserReadProfile):
            raise BrowserReadError("browser backend requires a frozen operator profile")
        self.profile = profile
        self._process = process_backend or LinuxBrowserProcessBackend()

    async def read(self, url: str, *, timeout_seconds: float) -> BrowserReadResult:
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise BrowserReadError("browser read deadline must be finite and positive")
        url = self.profile.validate_url(url)
        effective_timeout = min(timeout_seconds, self.profile.timeout_ms / 1000)
        async with asyncio.timeout(effective_timeout):
            await asyncio.to_thread(self.profile.verify_runtime)
            return await self._read(url, effective_timeout)

    async def _read(self, url: str, timeout: float) -> BrowserReadResult:
        request_id = owner = str(uuid.uuid4())
        with tempfile.TemporaryDirectory(prefix="agenthub-browser-read-") as directory:
            root = Path(directory).resolve()
            payload = {
                "schemaVersion": 1,
                "requestId": request_id,
                "url": url,
                "origins": list(self.profile.origins),
                "maxTextChars": self.profile.max_text_chars,
                "timeoutMs": min(self.profile.timeout_ms, max(1, int(timeout * 1000))),
            }
            input_file = root / "input.json"
            input_file.write_bytes(canonical_json(payload))
            task = asyncio.create_task(
                self._process.run(
                    (
                        str(self.profile.node_path),
                        str(self.profile.worker_dir / "worker.mjs"),
                        str(input_file),
                    ),
                    cwd=root,
                    environment=child_environment(root, owner, self.profile),
                    timeout_seconds=timeout,
                    max_output_bytes=MAX_PROCESS_OUTPUT_BYTES,
                    monitored_root=root,
                    max_workdir_bytes=MAX_WORKDIR_BYTES,
                )
            )
            task.add_done_callback(_consume)
            try:
                process = await asyncio.shield(task)
            finally:
                if not task.done():
                    task.cancel()
                    await _drain(task, self.profile.cleanup_timeout_seconds)
                confirmed = await confirmed_cleanup(
                    owner, self.profile.cleanup_timeout_seconds
                )
            if not task.done():
                raise BrowserReadError("browser process cleanup is still unresolved")
            return parse_worker_result(
                process,
                request_id=request_id,
                url=url,
                profile=self.profile,
                cleanup_confirmed=confirmed,
            )


def _consume(task):
    if not task.cancelled():
        task.exception()


async def _drain(task, timeout):
    try:
        await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        return
