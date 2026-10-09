from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.domain import Lease, MissionSource
from tests.domain.factories import build_contract, build_mission, build_work_unit
from tests.integration.recovery_worker import control_repository


async def seed(root):
    (root / "workspace").mkdir()
    async with control_repository(root / "control.sqlite3") as repository:
        await repository.add_contract_lineage("contract-1", "workspace-1")
        await repository.add_contract(build_contract())
        await repository.add_mission(build_mission(status="RUNNING", source=MissionSource(type="manual")))
        await repository.add_work_unit(build_work_unit(kind="desktop.task", status="RUNNING", attempt=1,
            assigned_agent_id="agent-1", assigned_adapter="function-calling", lease=Lease(id="lease-1",
                runner_id="runner-1", expires_at=datetime.now(timezone.utc) + timedelta(seconds=300))))


def worker(root, mode):
    return subprocess.Popen([sys.executable, "-m", "tests.integration.recovery_worker", str(root), mode],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())


@asynccontextmanager
async def paused_worker(root, mode):
    (root / "paused").unlink(missing_ok=True)
    child = worker(root, mode)
    try:
        deadline = asyncio.get_running_loop().time() + 25
        while not (root / "paused").exists():
            if child.poll() is not None:
                stdout, stderr = child.communicate()
                pytest.fail(f"child exited before durable boundary: {stdout!r} {stderr!r}")
            if asyncio.get_running_loop().time() >= deadline:
                child.kill()
                stdout, stderr = await asyncio.to_thread(child.communicate, timeout=10)
                pytest.fail(f"worker did not reach checkpoint: {stdout!r} {stderr!r}")
            await asyncio.sleep(.02)
        yield child
    finally:
        if child.poll() is None:
            child.kill()
            await asyncio.to_thread(child.communicate, timeout=10)


async def kill_at_boundary(root, mode):
    async with paused_worker(root, mode) as child:
        assert child.poll() is None
    assert child.returncode != 0


async def restart(root, mode="resume"):
    child = worker(root, mode)
    try:
        stdout, stderr = await asyncio.to_thread(child.communicate, timeout=25)
        return child.returncode, stdout, stderr
    finally:
        if child.poll() is None:
            child.kill()
            await asyncio.to_thread(child.communicate, timeout=10)


async def verify_completed_control_state(root):
    async with control_repository(root / "control.sqlite3") as repository:
        latest = await repository.get_latest_execution_checkpoint("wu-1", 1)
        assert latest.terminal and latest.prompt_tokens == 150 and latest.completion_tokens == 30
        assert latest.tool_calls == 2 and latest.model_cost == pytest.approx(.15)
        unit = await repository.get_work_unit("wu-1")
        assert unit.status.value == "VERIFYING" and unit.attempt == 1
        checkpoints = await repository.list_execution_checkpoints("mis-1")
        public = str([checkpoint.model_dump() for checkpoint in checkpoints])
        assert "written one" not in public and "private args" not in public

@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["checkpoint", "receipt"])
async def test_real_kill_restart_preserves_tools_budgets_and_authoritative_checkpoints(tmp_path, boundary):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, boundary)
    assert (tmp_path / "workspace" / "output").read_text() == "one\n"
    code, stdout, stderr = await restart(tmp_path)
    assert code == 0, (stdout, stderr)
    assert (tmp_path / "workspace" / "output").read_text() == "one\ntwo\n"
    assert (tmp_path / "model-calls").read_text().splitlines() == ["0", "2"]
    await verify_completed_control_state(tmp_path)


@pytest.mark.asyncio
async def test_real_kill_with_ambiguous_side_effect_never_replays(tmp_path):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, "ambiguous")
    code, _, stderr = await restart(tmp_path)
    assert code != 0 and b"safely restored" in stderr
    assert (tmp_path / "workspace" / "output").read_text() == "one\n"
    assert (tmp_path / "model-calls").read_text().splitlines() == ["0"]


@pytest.mark.asyncio
async def test_real_restart_refuses_workspace_drift(tmp_path):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, "checkpoint")
    (tmp_path / "workspace" / "output").write_text("external change\n")
    code, _, _ = await restart(tmp_path)
    assert code != 0
    assert (tmp_path / "workspace" / "output").read_text() == "external change\n"
    assert (tmp_path / "model-calls").read_text().splitlines() == ["0"]


@pytest.mark.asyncio
async def test_terminal_checkpoint_restarts_publication_without_new_model_or_tool_work(tmp_path):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, "terminal")
    code, stdout, stderr = await restart(tmp_path)
    assert code == 0, (stdout, stderr)
    assert (tmp_path / "workspace" / "output").read_text() == "one\ntwo\n"
    assert (tmp_path / "model-calls").read_text().splitlines() == ["0", "2"]
    async with control_repository(tmp_path / "control.sqlite3") as repository:
        assert (await repository.get_work_unit("wu-1")).status.value == "VERIFYING"


@pytest.mark.asyncio
async def test_second_live_process_is_fenced_and_crashed_process_releases_os_lock(tmp_path):
    await seed(tmp_path)
    async with paused_worker(tmp_path, "checkpoint"):
        code, _, stderr = await restart(tmp_path)
        assert code == 0, stderr
        assert json.loads((tmp_path / "result.json").read_text())["claimStatus"] == "capacity_saturated"
        async with control_repository(tmp_path / "control.sqlite3") as repository:
            assert (await repository.get_work_unit("wu-1")).status.value == "RUNNING"
    code, stdout, stderr = await restart(tmp_path)
    assert code == 0, (stdout, stderr)
    assert (tmp_path / "workspace" / "output").read_text() == "one\ntwo\n"


@pytest.mark.asyncio
async def test_inflight_model_checkpoint_requires_reconciliation(tmp_path):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, "model")
    code, _, stderr = await restart(tmp_path)
    assert code != 0 and b"safely restored" in stderr
    assert not (tmp_path / "model-calls").exists()
    assert not (tmp_path / "workspace" / "output").exists()


@pytest.mark.asyncio
async def test_repeated_crashes_before_model_call_retain_exact_iteration_cursor(tmp_path):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, "iteration")
    await kill_at_boundary(tmp_path, "resume_start")
    code, stdout, stderr = await restart(tmp_path)
    assert code == 0, (stdout, stderr)
    assert (tmp_path / "model-calls").read_text().splitlines() == ["0", "2"]
    assert (tmp_path / "workspace" / "output").read_text() == "one\ntwo\n"
    async with control_repository(tmp_path / "control.sqlite3") as repository:
        assert (await repository.get_latest_execution_checkpoint("wu-1", 1)).iteration == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout_changed", "context_changed"])
async def test_real_restart_refuses_changed_execution_policy(tmp_path, mode):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, "checkpoint")
    code, _, stderr = await restart(tmp_path, mode)
    assert code != 0 and b"safely restored" in stderr
    assert (tmp_path / "workspace" / "output").read_text() == "one\n"
    assert (tmp_path / "model-calls").read_text().splitlines() == ["0"]


@pytest.mark.asyncio
async def test_real_restart_refuses_legacy_null_metadata(tmp_path):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, "checkpoint")
    with sqlite3.connect(tmp_path / "control.sqlite3") as connection:
        connection.execute("UPDATE execution_checkpoints SET resume_protocol_version=NULL, next_action=NULL, "
                           "workspace_revision=NULL, context_manifest_digest=NULL, idempotency_key=NULL")
    code, _, stderr = await restart(tmp_path)
    assert code != 0 and b"safely restored" in stderr
    assert (tmp_path / "workspace" / "output").read_text() == "one\n"


@pytest.mark.asyncio
async def test_real_restart_refuses_corrupt_private_body_without_tool_replay(tmp_path):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, "checkpoint")
    private = next((tmp_path / "private").rglob("resume-images.sqlite3"))
    with sqlite3.connect(private) as connection:
        connection.execute("UPDATE resume_images SET body='{}'")
    code, _, stderr = await restart(tmp_path)
    assert code != 0 and b"safely restored" in stderr
    assert (tmp_path / "workspace" / "output").read_text() == "one\n"


@pytest.mark.asyncio
async def test_original_timeout_deadline_cannot_be_replenished_by_process_restart(tmp_path):
    await seed(tmp_path)
    await kill_at_boundary(tmp_path, "short_deadline")
    await asyncio.sleep(2.05)
    code, stdout, stderr = await restart(tmp_path, "resume_short")
    assert code == 0, (stdout, stderr)
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["success"] is False and result["status"] == "FAILED"
    assert (tmp_path / "workspace" / "output").read_text() == "one\n"
    assert (tmp_path / "model-calls").read_text().splitlines() == ["0"]
