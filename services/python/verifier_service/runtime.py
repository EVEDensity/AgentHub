from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from app.services.artifact_integrity_service import (
    ArtifactByteDescriptor,
    ArtifactBytesUnavailableError,
    ArtifactByteVerification,
    ArtifactByteVerifier,
    ContentAddressedArtifactByteVerifier,
)
from app.services.verifier_service import (
    ControlledVerifier,
    MissionControlVerifierClient,
)
from app.services.verifier_worker import VerifierWorker, VerifierWorkerSnapshot

from .config import VerifierServiceSettings, read_secret_file

_LOCAL_CONTENT_PREFIX = "local:sha256/"


@dataclass(frozen=True, slots=True)
class LocalArtifactVerificationSettings:
    local_root: Path
    verify_max_bytes: int


class VerifierWorkerPort(Protocol):
    @property
    def snapshot(self) -> VerifierWorkerSnapshot: ...

    async def run(self) -> None: ...

    def request_stop(self) -> None: ...


class AsyncClosePort(Protocol):
    async def aclose(self) -> None: ...


class LocalArtifactByteVerifier:
    """Restrict the first verifier deployment to its mounted local CAS."""

    def __init__(self, delegate: ArtifactByteVerifier) -> None:
        self._delegate = delegate

    async def verify_all(
        self,
        artifacts: Sequence[ArtifactByteDescriptor],
    ) -> list[ArtifactByteVerification]:
        if any(
            not artifact.content_address.startswith(_LOCAL_CONTENT_PREFIX)
            for artifact in artifacts
        ):
            raise ArtifactBytesUnavailableError(
                "verifier service supports only mounted local Artifact bytes"
            )
        return await self._delegate.verify_all(artifacts)


@dataclass(slots=True)
class VerifierServiceRuntime:
    """Own process resources around a non-persistent verifier worker."""

    worker: VerifierWorkerPort
    shutdown_timeout_seconds: float
    closeables: Sequence[AsyncClosePort] = ()
    _worker_task: asyncio.Task[None] | None = field(default=None, init=False)

    @property
    def snapshot(self) -> VerifierWorkerSnapshot:
        return self.worker.snapshot

    @property
    def healthy(self) -> bool:
        task = self._worker_task
        return task is not None and not task.done()

    @property
    def ready(self) -> bool:
        snapshot = self.snapshot
        return self.healthy and snapshot.running and snapshot.ready

    async def start(self) -> None:
        if self._worker_task is not None:
            raise RuntimeError("Verifier service runtime is already started")
        self._worker_task = asyncio.create_task(
            self.worker.run(),
            name="agenthub-verifier-worker",
        )
        await asyncio.sleep(0)
        if self._worker_task.done():
            await self._worker_task
            raise RuntimeError("Verifier worker stopped during startup")

    async def stop(self) -> None:
        task = self._worker_task
        try:
            if task is not None:
                self.worker.request_stop()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=self.shutdown_timeout_seconds,
                    )
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            for closeable in reversed(tuple(self.closeables)):
                await closeable.aclose()


def build_verifier_runtime(
    settings: VerifierServiceSettings,
) -> VerifierServiceRuntime:
    """Compose one strict workspace verifier without runtime fallbacks."""

    control_token = read_secret_file(settings.mission_control_token_file)
    control_http = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        follow_redirects=False,
    )
    control = MissionControlVerifierClient(
        settings.mission_control_url,
        access_token=control_token,
        http_client=control_http,
    )
    byte_verifier = LocalArtifactByteVerifier(
        ContentAddressedArtifactByteVerifier(
            LocalArtifactVerificationSettings(
                local_root=settings.artifact_local_root,
                verify_max_bytes=settings.max_artifact_bytes,
            )
        )
    )
    verifier = ControlledVerifier(
        control,
        byte_verifier=byte_verifier,
        verifier_id=settings.verifier_id,
        verifier_version=settings.verifier_version,
    )
    worker = VerifierWorker(
        verifier,
        workspace_id=settings.workspace_id,
        idle_delay_seconds=settings.idle_delay_seconds,
        max_delay_seconds=settings.max_delay_seconds,
    )
    return VerifierServiceRuntime(
        worker=worker,
        shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        closeables=(control_http,),
    )


__all__ = [
    "LocalArtifactByteVerifier",
    "LocalArtifactVerificationSettings",
    "VerifierServiceRuntime",
    "build_verifier_runtime",
]
