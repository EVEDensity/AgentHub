"""Real HTTP Runner cancellation releases private attempt exclusion.

The shared fixture boots actual SQLite/API persistence. Tests inject a bounded
transport/publication stall and cancel workers after their real lease admission.
"""
import asyncio

import pytest

from app.core.config import ArtifactStoreSettings
from app.services.artifact_store_service import ContentAddressedArtifactPublisher
from tests.integration.test_runner_parallel_sqlite_http import (
    GatedPublisher,
    ParallelModelFactory,
    assert_attempt_lock_released,
    boot_http,  # noqa: F401 - shared pytest fixture
    parallel_controller,
)


async def cancel_workers(controller):
    for task in controller._worker_tasks:
        task.cancel()
    await asyncio.gather(*controller._worker_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_publication_cancellation_releases_lock_and_preserves_resumable_lease(boot_http, tmp_path):
    repository, control = boot_http
    publisher = GatedPublisher(tmp_path)
    controller = parallel_controller(tmp_path, control, ParallelModelFactory(), publisher)
    await controller.start()
    try:
        await asyncio.wait_for(publisher.first_entered.wait(), timeout=5)
        await cancel_workers(controller)
        assert_attempt_lock_released(controller.workspace_root)
        unit = await repository.get_work_unit("parallel-0")
        assert unit.status.value == "RUNNING" and unit.lease.runner_id == "runner-1"
        assert unit.attempt == 1
        assert await repository.list_work_unit_artifacts("parallel-mission", unit.id, 1) == []
    finally:
        publisher.release.set()
        await controller.stop()


@pytest.mark.asyncio
async def test_start_transport_cancellation_releases_lock_without_starting_work(boot_http, tmp_path):
    repository, control = boot_http
    entered = asyncio.Event()
    original = control._request

    async def stalled_start(method, path, **kwargs):
        if path.endswith("/parallel-0/start"):
            entered.set()
            await asyncio.Event().wait()
        return await original(method, path, **kwargs)

    control._request = stalled_start
    publisher = ContentAddressedArtifactPublisher(ArtifactStoreSettings(backend="local", local_root=tmp_path / "cas"))
    controller = parallel_controller(tmp_path, control, ParallelModelFactory(), publisher)
    await controller.start()
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await cancel_workers(controller)
        assert_attempt_lock_released(controller.workspace_root)
        unit = await repository.get_work_unit("parallel-0")
        assert unit.status.value == "LEASED" and unit.attempt == 1
        assert unit.lease.runner_id == "runner-1"
        assert await repository.get_latest_execution_checkpoint(unit.id, 1) is None
    finally:
        await controller.stop()
