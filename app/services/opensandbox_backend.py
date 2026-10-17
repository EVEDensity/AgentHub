"""Explicit, default-off OpenSandbox HTTP experiment; no production selection.

The caller owns authorization and may use this port only after resolving its
Contract. No Mission state or Evidence is written here. Failure never falls
back to a local subprocess, and no upstream SDK enters application dependencies.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.services.opensandbox_protocol import (
    MAX_BODY,
    WORKLOAD_IMAGE,
    CommandResult,
    OpenSandboxConfig,
    OpenSandboxError,
    TransferredFile,
    bounded_seconds,
    digest,
    endpoint,
    execution_from_sse,
    identifier,
    json_object,
    terminal_status,
    workspace_path,
)


class OpenSandboxBackend:
    """One process-owned client; tracked sandboxes must be explicitly destroyed."""

    def __init__(self, config: OpenSandboxConfig, *, transport=None) -> None:
        if config.enabled is not True:
            raise OpenSandboxError("OpenSandbox experiment is disabled")
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=endpoint(config.endpoint) + "/v1/",
            headers={"OPEN-SANDBOX-API-KEY": config.api_key},
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._owned: set[str] = set()
        self._active: dict[str, set[str]] = {}

    def _path(self, sandbox_id: str, suffix: str) -> str:
        sandbox_id = identifier(sandbox_id)
        if sandbox_id not in self._owned:
            raise OpenSandboxError("sandbox is not owned by this backend")
        return f"sandboxes/{sandbox_id}/proxy/44772/{suffix}"

    @asynccontextmanager
    async def _response(self, method: str, path: str, **kwargs) -> AsyncIterator[httpx.Response]:
        try:
            async with self._client.stream(method, path, **kwargs) as response:
                yield response
        except httpx.HTTPError:
            raise OpenSandboxError("backend transport failed") from None

    async def _body(self, response: httpx.Response) -> bytes:
        result = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            result.extend(chunk)
            if len(result) > MAX_BODY:
                raise OpenSandboxError("backend response exceeded limit")
        return bytes(result)

    async def _request(self, method: str, path: str, *, accepted=(200,), content_type=None, **kwargs) -> bytes:
        try:
            async with asyncio.timeout(self.config.request_timeout_seconds):
                async with self._response(method, path, **kwargs) as response:
                    if response.status_code not in accepted:
                        raise OpenSandboxError(f"backend HTTP {response.status_code}")
                    if content_type and response.headers.get("content-type", "").split(";")[0] != content_type:
                        raise OpenSandboxError("unexpected backend content type")
                    return await self._body(response)
        except TimeoutError:
            raise OpenSandboxError("backend request timed out") from None

    async def create(self, job_id: str) -> str:
        body = {
            "image": {"uri": WORKLOAD_IMAGE},
            "platform": {"os": "linux", "arch": "amd64"},
            "entrypoint": ["tail", "-f", "/dev/null"],
            "timeout": self.config.lifetime_seconds,
            "resourceLimits": {"cpu": "500m", "memory": "512Mi"},
            "metadata": {"agenthub.job": identifier(job_id)},
            "networkPolicy": {"defaultAction": "deny", "egress": []},
        }
        data = json_object(await self._request("POST", "sandboxes", accepted=(202,), json=body))
        sandbox_id = identifier(data.get("id"))
        self._owned.add(sandbox_id)
        try:
            await self._ready(sandbox_id)
            result = await self.execute(sandbox_id, ["mkdir", "-p", "/workspace"], timeout_seconds=10)
            if result.exit_code != 0:
                raise OpenSandboxError("workspace initialization failed")
        except BaseException:
            await asyncio.shield(self.destroy(sandbox_id))
            raise
        return sandbox_id

    async def _ready(self, sandbox_id: str) -> None:
        try:
            async with asyncio.timeout(self.config.ready_timeout_seconds):
                while True:
                    data = json_object(await self._request("GET", f"sandboxes/{sandbox_id}"))
                    status = data.get("status")
                    state = status.get("state") if isinstance(status, dict) else None
                    if data.get("id") != sandbox_id or state in ("Failed", "Terminated"):
                        raise OpenSandboxError("sandbox failed readiness")
                    if state == "Running":
                        async with self._response("GET", self._path(sandbox_id, "ping")) as response:
                            if response.status_code == 200:
                                return
                    await asyncio.sleep(0.2)
        except TimeoutError:
            raise OpenSandboxError("sandbox readiness timed out") from None

    async def command_status(self, sandbox_id: str, execution_id: str) -> int | None:
        data = await self._request("GET", self._path(sandbox_id, f"command/status/{identifier(execution_id)}"))
        result = terminal_status(json_object(data), execution_id)
        if result is not None:
            self._active.get(sandbox_id, set()).discard(execution_id)
        return result

    async def network_policy(self, sandbox_id: str) -> dict:
        self._path(sandbox_id, "ping")
        data = await self._request("GET", f"sandboxes/{sandbox_id}/networkpolicy")
        return json_object(data)

    async def _wait(self, sandbox_id: str, execution_id: str) -> int:
        while True:
            code = await self.command_status(sandbox_id, execution_id)
            if code is not None:
                return code
            await asyncio.sleep(0.1)

    async def interrupt(self, sandbox_id: str, execution_id: str) -> None:
        """Confirm stopped state, or destroy the entire sandbox if uncertain."""
        try:
            async with asyncio.timeout(10):
                await self._request("DELETE", self._path(sandbox_id, "command"),
                                    params={"id": identifier(execution_id)})
                await self._wait(sandbox_id, execution_id)
        except (OpenSandboxError, TimeoutError):
            await self.destroy(sandbox_id)

    async def start_command(self, sandbox_id: str, argv: list[str], *, timeout_seconds: float = 30) -> str:
        bounded_seconds(timeout_seconds)
        if (not isinstance(argv, list) or not argv or len(argv) > 128
                or any(not isinstance(item, str) or "\x00" in item for item in argv)):
            raise OpenSandboxError("command requires explicit argv")
        if sum(len(item) for item in argv) > 32768:
            raise OpenSandboxError("command exceeded limit")
        data = await self._request("POST", self._path(sandbox_id, "command"), content_type="text/event-stream", json={
            "argv": argv, "background": True, "cwd": "/", "timeout": int(timeout_seconds * 1000),
        })
        execution_id = execution_from_sse(data)
        self._active.setdefault(sandbox_id, set()).add(execution_id)
        return execution_id

    async def execute(self, sandbox_id: str, argv: list[str], *, timeout_seconds: float = 30) -> CommandResult:
        bounded_seconds(timeout_seconds)
        execution_id = None
        try:
            async with asyncio.timeout(timeout_seconds):
                execution_id = await self.start_command(sandbox_id, argv, timeout_seconds=timeout_seconds)
                code = await self._wait(sandbox_id, execution_id)
                output = await self._request("GET", self._path(sandbox_id, f"command/{execution_id}/logs"))
                return CommandResult(execution_id, code, output.decode("utf-8", errors="replace")
                                     .replace(self.config.api_key, "[REDACTED]"))
        except BaseException as exc:
            if execution_id is not None:
                await asyncio.shield(self.interrupt(sandbox_id, execution_id))
            else:
                await asyncio.shield(self.destroy(sandbox_id))
            if isinstance(exc, TimeoutError):
                raise OpenSandboxError("command timed out") from None
            raise

    async def upload(self, sandbox_id: str, relative: str, data: bytes, *, sha256: str) -> TransferredFile:
        path = workspace_path(relative)
        if len(data) > MAX_BODY or digest(data) != sha256:
            raise OpenSandboxError("input file failed digest or size validation")
        await self._check_file_path(sandbox_id, path)
        metadata = json.dumps({"path": path, "mode": 600})
        await self._request("POST", self._path(sandbox_id, "files/upload"), files=[
            ("metadata", (None, metadata, "application/json")),
            ("file", ("input", data, "application/octet-stream")),
        ])
        downloaded = await self.download(sandbox_id, relative)
        if digest(downloaded) != sha256:
            raise OpenSandboxError("uploaded file digest mismatch")
        return TransferredFile(relative, len(data), sha256)

    async def download(self, sandbox_id: str, relative: str, *, sha256: str | None = None) -> bytes:
        path = workspace_path(relative)
        await self._check_file_path(sandbox_id, path)
        data = await self._request("GET", self._path(sandbox_id, "files/download"),
                                   params={"path": path})
        if sha256 is not None and digest(data) != sha256:
            raise OpenSandboxError("downloaded file digest mismatch")
        return data

    async def _check_file_path(self, sandbox_id: str, path: str) -> None:
        if self._active.get(sandbox_id):
            raise OpenSandboxError("cannot transfer files while a command is running")
        # Transfers run between bounded commands, never against a live executor.
        # Refuse symlinks at every path component; this is not an openat2 boundary
        # against a malicious concurrently running workload (see deployment guide).
        script = ('target="$1"; while [ "$target" != / ]; do '
                  '[ ! -L "$target" ] || exit 73; target=$(dirname -- "$target"); done')
        result = await self.execute(sandbox_id, ["bash", "-c", script, "path-check", path], timeout_seconds=5)
        if result.exit_code != 0:
            raise OpenSandboxError("workspace symlinks are not transferable")

    async def destroy(self, sandbox_id: str) -> None:
        sandbox_id = identifier(sandbox_id)
        if sandbox_id not in self._owned:
            return
        await self._request("DELETE", f"sandboxes/{sandbox_id}", accepted=(204, 404))
        async with self._response("GET", f"sandboxes/{sandbox_id}") as response:
            if response.status_code != 404:
                raise OpenSandboxError("sandbox destruction was not confirmed")
        self._owned.discard(sandbox_id)
        self._active.pop(sandbox_id, None)

    async def close(self) -> None:
        failures = []
        for sandbox_id in tuple(self._owned):
            try:
                await self.destroy(sandbox_id)
            except OpenSandboxError:
                failures.append(sandbox_id)
        await self._client.aclose()
        if failures:
            raise OpenSandboxError("backend cleanup failed")
