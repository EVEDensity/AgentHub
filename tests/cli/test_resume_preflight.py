"""CLI metadata checks never substitute for the private Runner recovery image."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.cli import runtime
from app.cli.resume import ResumeExecutionPlan, prepare_resume_handoff


def _anchor() -> dict:
    return {
        "id": "chk-1", "missionId": "mis-1", "workUnitId": "wu-1",
        "attempt": 1, "sequence": 2, "iteration": 1, "terminal": False,
        "phase": "harness.model.completed", "resumeProtocolVersion": 2,
        "nextAction": {"resumeImageDigest": "sha256:" + "a" * 64},
        "workspaceRevision": "sha256:" + "b" * 64,
        "contextManifestDigest": "sha256:" + "c" * 64,
    }


def _client() -> Mock:
    client = Mock()
    client.get_mission.return_value = {"id": "mis-1", "status": "RUNNING"}
    client.checkpoints.return_value = [_anchor()]
    client.work_units.return_value = [{
        "id": "wu-1", "missionId": "mis-1", "status": "RUNNING", "attempt": 1,
        "lease": {"id": "lease-1", "runnerId": "local-admin", "attempt": 1,
                  "expiresAt": "2999-01-01T00:00:00+00:00"},
    }]
    client.heartbeat_work_unit.return_value = {"lease": deepcopy(client.work_units.return_value[0]["lease"])}
    client.execution_context.return_value = {"executionContext": {"checkpoint": _anchor()}}
    client.decisions.return_value = []
    return client


def _prepare(client: Mock) -> ResumeExecutionPlan:
    with patch.object(runtime, "workspace_revision", return_value="sha256:" + "d" * 64):
        return runtime.prepare_resume_execution(client, "mis-1", Path("."))


def test_digest_only_v2_anchor_keeps_same_attempt_without_inventing_a_resume_dto():
    client = _client()
    plan = _prepare(client)
    assert plan.can_resume
    assert plan.attempt == 1
    assert plan.lease_id == "lease-1"
    assert plan.resume_input is None
    assert isinstance(plan, runtime.ResumeExecutionPlan)
    client.heartbeat_work_unit.assert_called_once_with(
        "mis-1", "wu-1", lease_id="lease-1", lease_seconds=300,
    )
    client.recover_work_unit.assert_not_called()
    client.lease_work_unit.assert_not_called()


def test_v2_start_anchor_at_iteration_zero_is_a_valid_metadata_preflight():
    client = _client()
    client.checkpoints.return_value[0].update(iteration=0, phase="harness.execution.started")
    client.execution_context.return_value["executionContext"]["checkpoint"] = client.checkpoints.return_value[0]
    plan = _prepare(client)
    assert plan.can_resume
    assert plan.resume_input is None


@pytest.mark.parametrize("version", [None, 1, True])
def test_legacy_metadata_is_diagnostic_and_cannot_be_an_execution_resume(version):
    client = _client()
    client.checkpoints.return_value[0]["resumeProtocolVersion"] = version
    with patch.object(runtime, "workspace_revision", return_value="sha256:" + "b" * 64):
        diagnostic = runtime.resume_work_unit(client, "mis-1", Path("."))
        assert diagnostic["legacy"]
        with pytest.raises(RuntimeError, match="legacy"):
            runtime.prepare_resume_execution(client, "mis-1", Path("."))
    client.heartbeat_work_unit.assert_not_called()
    client.recover_work_unit.assert_not_called()
    client.lease_work_unit.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("expiresAt", "2000-01-01T00:00:00+00:00"),
    ("expiresAt", "2999-01-01T00:00:00"),
    ("expiresAt", "invalid"),
    ("runnerId", "different-runner"),
    ("runnerId", None),
    ("attempt", 2),
])
def test_stale_or_foreign_lease_is_refused_without_recovery_mutation(field, value):
    client = _client()
    client.work_units.return_value[0]["lease"][field] = value
    plan = _prepare(client)
    assert not plan.can_resume
    assert plan.resume_input is None
    client.heartbeat_work_unit.assert_not_called()
    client.recover_work_unit.assert_not_called()
    client.lease_work_unit.assert_not_called()


@pytest.mark.parametrize("status", ["PENDING", "FAILED", "RETRYING", "SUCCEEDED", "VERIFYING"])
def test_a_new_attempt_is_never_claimed_to_reuse_an_old_checkpoint(status):
    client = _client()
    client.work_units.return_value[0]["status"] = status
    plan = _prepare(client)
    assert not plan.can_resume
    client.recover_work_unit.assert_not_called()
    client.lease_work_unit.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("id", "different-lease"), ("attempt", 2), ("runnerId", "different-runner"),
])
def test_heartbeat_cannot_switch_lease_owner_or_attempt(field, value):
    client = _client()
    client.heartbeat_work_unit.return_value["lease"][field] = value
    plan = _prepare(client)
    assert not plan.can_resume
    assert plan.resume_input is None


@pytest.mark.parametrize("checkpoint", [None, {}, {**_anchor(), "sequence": 3},
                                             {**_anchor(), "nextAction": {"resumeImageDigest": "sha256:" + "e" * 64}}])
def test_missing_or_changed_execution_projection_fails_before_heartbeat(checkpoint):
    client = _client()
    client.execution_context.return_value = {"executionContext": {"checkpoint": checkpoint}}
    plan = _prepare(client)
    assert not plan.can_resume
    assert "checkpoint changed" in plan.refusal_reason
    client.heartbeat_work_unit.assert_not_called()


def test_claimed_receipt_success_is_not_a_harness_input_or_recovered_tool_result():
    client = _client()
    gate = {"checkpoint": _anchor(), "workUnits": client.work_units.return_value,
            "receiptDecision": "already_succeeded"}
    with patch.object(runtime, "resume_work_unit", return_value=gate):
        plan = runtime.prepare_resume_execution(client, "mis-1", Path("."))
    assert plan.can_resume
    assert plan.resume_input is None


def test_pending_decision_without_exact_attempt_is_not_restored():
    client = _client()
    client.decisions.return_value = [{"workUnitId": "wu-1", "status": "PENDING"}]
    plan = _prepare(client)
    assert plan.can_resume
    assert plan.pending_decision is None


def test_authenticated_mission_identity_must_match_the_requested_mission():
    client = _client()
    client.get_mission.return_value["id"] = "mis-other"
    with pytest.raises(RuntimeError, match="invalid mission payload"):
        _prepare(client)
    client.heartbeat_work_unit.assert_not_called()


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELLED"])
def test_already_terminal_mission_is_observed_without_starting_an_execution(status):
    client = _client()
    client.get_mission.return_value["status"] = status
    strict = Mock(side_effect=AssertionError("terminal Mission must not execute"))
    plan = prepare_resume_handoff(client, "mis-1", Path("."), execution_preparer=strict)
    assert plan.can_resume and plan.observation_only
    assert plan.resume_input is None
    strict.assert_not_called()
    client.heartbeat_work_unit.assert_not_called()


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
def test_worker_completion_during_preflight_rereads_and_observes_real_terminal_status(status):
    client = _client()
    strict = Mock(side_effect=RuntimeError("checkpoint became terminal"))
    reread = Mock(return_value={"missionId": "mis-1", "missionStatus": status})
    plan = prepare_resume_handoff(client, "mis-1", Path("."),
                                  execution_preparer=strict, gate_reader=reread)
    assert plan.can_resume and plan.observation_only
    assert plan.resume_input is None
    reread.assert_called_once()
    client.recover_work_unit.assert_not_called()
    client.lease_work_unit.assert_not_called()


def test_completed_execution_waits_for_independent_verification_without_resuming_tools():
    client = _client()
    checkpoint = {**_anchor(), "terminal": True, "phase": "harness.execution.completed"}
    unit = {**client.work_units.return_value[0], "status": "VERIFYING", "lease": None}
    refused = ResumeExecutionPlan("mis-1", "wu-1", 1, None, checkpoint, None,
                                  "not_checked", False, "terminal checkpoint")
    reread = Mock(return_value={"missionId": "mis-1", "missionStatus": "RUNNING", "workUnits": [unit], "checkpoint": checkpoint})
    plan = prepare_resume_handoff(client, "mis-1", Path("."),
                                  execution_preparer=Mock(return_value=refused), gate_reader=reread)
    assert plan.can_resume and plan.observation_only
    assert plan.resume_input is None
    assert plan.lease_id is None
    reread.assert_called_once()
    client.heartbeat_work_unit.assert_not_called()


@pytest.mark.parametrize("field,value", [("attempt", 2), ("missionId", "mis-other"),
                                          ("terminal", False), ("phase", "harness.execution.failed")])
def test_wait_observer_requires_matching_completed_execution_anchor(field, value):
    client = _client()
    checkpoint = {**_anchor(), "terminal": True, "phase": "harness.execution.completed", field: value}
    unit = {**client.work_units.return_value[0], "status": "VERIFYING", "lease": None}
    strict = Mock(side_effect=RuntimeError("strict refusal"))
    reread = Mock(return_value={"missionId": "mis-1", "missionStatus": "RUNNING", "workUnits": [unit], "checkpoint": checkpoint})
    with pytest.raises(RuntimeError, match="strict refusal"):
        prepare_resume_handoff(client, "mis-1", Path("."),
                               execution_preparer=strict, gate_reader=reread)
    client.heartbeat_work_unit.assert_not_called()
