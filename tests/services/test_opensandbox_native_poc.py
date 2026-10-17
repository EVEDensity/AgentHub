"""CI composition controls; mocked ports do not establish real Linux evidence."""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import pytest

from app.services.opensandbox_protocol import CommandResult, CommandStop
from scripts import opensandbox_native_poc as native
from scripts import opensandbox_poc_ci as ci


class MemoryBackend:
    def __init__(self, config, report):
        self.config, self.report = config, report
        self.cancel_started = asyncio.Event()
        self.cancel_command = self.cancel_sandbox = None
        self.removed = set()

    async def create(self, _job):
        sandbox = str(uuid.uuid4())
        self.report["sandbox_ids"].append(sandbox)
        return sandbox

    async def execute(self, sandbox, argv, *, timeout_seconds):
        script = argv[-1]
        if native.CANCEL_SCRIPT in script:
            self.cancel_sandbox, self.cancel_command = sandbox, "actual-fixture-command"
            self.cancel_started.set()
            await asyncio.Future()
        code = 7 if native.FAILURE_SCRIPT in script else 0
        output = "native-failure-proof" if code else "native-stdout-proofnative-stderr-proof"
        return CommandResult("fixture-command", code, output)

    async def destroy(self, sandbox):
        self.removed.add(sandbox)
        return CommandStop("confirmed_sandbox_destroy")

    async def close(self):
        self.removed.update(self.report["sandbox_ids"])


@pytest.fixture
def native_ports(monkeypatch):
    instances = []

    def backend(*args):
        instance = MemoryBackend(*args)
        instances.append(instance)
        return instance

    async def absent(_config, sandbox):
        return sandbox in instances[0].removed

    async def root_roundtrip(_backend, report):
        report["unit_test_roundtrip_stub"] = True

    monkeypatch.setattr(native, "ObservedBackend", backend)
    monkeypatch.setattr(native, "_confirm_absent", absent)
    monkeypatch.setattr(native, "_budgeted_roundtrip", root_roundtrip)
    return instances


@pytest.mark.asyncio
async def test_script_drives_real_native_loop_and_checks_failed_exit_and_cancel_cleanup(native_ports):
    args = argparse.Namespace(endpoint="http://127.0.0.1:18080", enable=True, job_id=str(uuid.uuid4()))
    report = await native.run_poc(args, "test-only-secret")
    assert report["status"] == "passed"
    assert report["schema"] == native.SCHEMA
    assert report["paid_provider_called"] is False
    assert report["model_fixture"] == "deterministic-canonical-model.v1"
    assert len(set(report["sandbox_ids"])) == 3
    assert native_ports[0].removed == set(report["sandbox_ids"])
    assert [item["exit_code"] for item in report["observations"]] == [0, 7]
    assert all(item["passed"] for item in report["checks"])
    assert "native_cancellation_propagated" in {item["name"] for item in report["checks"]}
    assert "test-only-secret" not in json.dumps(report)


@pytest.mark.asyncio
async def test_cleanup_failure_invalidates_success_and_is_sanitized(native_ports, monkeypatch):
    async def failed_close(_self):
        raise RuntimeError("private-secret-never-report")

    monkeypatch.setattr(MemoryBackend, "close", failed_close)
    args = argparse.Namespace(endpoint="http://127.0.0.1:18080", enable=True, job_id=str(uuid.uuid4()))
    report = await native.run_poc(args, "private-secret-never-report")
    assert report["status"] == "failed"
    assert report["cleanup_error_type"] == "RuntimeError"
    assert "private-secret-never-report" not in json.dumps(report)


@pytest.mark.parametrize("flag", ["disabled", "job", "image", "existing"])
def test_native_cli_refuses_invalid_inputs_without_execution(tmp_path, monkeypatch, flag):
    destination = tmp_path / "native.json"
    arguments = ["--enable", "--endpoint", "http://localhost:18080", "--job-id", str(uuid.uuid4()),
                 "--report", str(destination)]
    if flag == "disabled":
        arguments.remove("--enable")
    elif flag == "job":
        arguments[arguments.index("--job-id") + 1] = "not-canonical"
    elif flag == "image":
        monkeypatch.setenv("AGENTHUB_OPENSANDBOX_WORKLOAD_IMAGE", "mutable:tag")
    else:
        destination.write_text("retained evidence")
    monkeypatch.setattr(native, "run_poc", lambda *_: pytest.fail("must not execute"))
    assert native.main(arguments) == 1
    if flag == "existing":
        assert destination.read_text() == "retained evidence"


def test_wrapper_native_flag_is_explicit_and_still_requires_enable(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ci, "execute", lambda path, **kwargs: calls.append((path, kwargs)) or 0)
    monkeypatch.setattr(ci.signal, "signal", lambda *_: None)
    destination = tmp_path / "report.json"
    assert ci.main(["--enable", "--report", str(destination)]) == 0
    assert calls[-1][1] == {"native_composition": False}
    assert ci.main(["--enable", "--native-composition", "--report", str(destination)]) == 0
    assert calls[-1][1] == {"native_composition": True}
    with pytest.raises(SystemExit):
        ci.main(["--native-composition"])


def passing_checks():
    names = (
        "native_success_exit_and_output", "native_success_final_output", "native_success_cleanup_confirmed",
        "native_failure_exit_preserved", "native_failure_not_success", "native_failure_cleanup_confirmed",
        "native_cancellation_command_started", "native_cancellation_propagated", "native_cancellation_cleanup_confirmed",
        "fresh_sandboxes_per_execution", "runner_budget_artifact_integrity_terminal",
        "runner_sandbox_cleanup_confirmed", "backend_close_confirmed",
    )
    return [{"name": name, "passed": True} for name in names]


@pytest.mark.parametrize("change", [None, "schema", "job", "status", "missing", "failed", "duplicate"])
def test_native_subprocess_requires_separate_matching_report_and_key_stays_in_env(tmp_path, monkeypatch, capsys, change):
    runtime = ci.Runtime(str(uuid.uuid4()), "test-private-key", tmp_path)
    destination = tmp_path / "report.native.json"
    calls = []
    result = {"schema": native.SCHEMA, "job_id": runtime.job_id, "status": "passed", "checks": passing_checks()}
    if change in {"schema", "job", "status"}:
        result[{"schema": "schema", "job": "job_id", "status": "status"}[change]] = "wrong"
    elif change == "missing":
        result["checks"].pop()
    elif change == "failed":
        result["checks"][0]["passed"] = False
    elif change == "duplicate":
        result["checks"][0] = result["checks"][1]

    def run(command, **kwargs):
        calls.append((command, kwargs))
        destination.write_text(json.dumps(result))
        return runtime.api_key

    monkeypatch.setattr(ci, "run", run)
    if change is None:
        ci.run_native_poc(runtime, destination, native.WORKLOAD_IMAGE)
    else:
        with pytest.raises(RuntimeError, match="successful report"):
            ci.run_native_poc(runtime, destination, native.WORKLOAD_IMAGE)
    command, options = calls[0]
    assert runtime.api_key not in " ".join(command)
    assert options["environment"]["AGENTHUB_OPENSANDBOX_API_KEY"] == runtime.api_key
    assert options["environment"]["AGENTHUB_OPENSANDBOX_WORKLOAD_IMAGE"] == native.WORKLOAD_IMAGE
    assert destination in runtime.additional_reports
    assert runtime.api_key not in capsys.readouterr().out


def test_wrapper_runs_old_poc_first_and_native_failure_still_cleans_exact_scope(tmp_path, monkeypatch):
    stages = []
    destination = tmp_path / "report.json"
    monkeypatch.setattr(ci, "preflight", lambda: {})
    monkeypatch.setattr(ci, "run", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(ci, "start_server", lambda *_: None)
    monkeypatch.setattr(ci, "wait_ready", lambda *_: None)
    monkeypatch.setattr(ci, "run_poc", lambda *_: stages.append("old-poc"))

    def failed_native(runtime, report, _image):
        stages.append("native")
        runtime.additional_reports.add(report)
        raise RuntimeError("unavailable")

    def cleanup(runtime, report):
        stages.append("cleanup")
        assert report == destination
        assert runtime.additional_reports == {destination.with_suffix(".native.json")}
        return []

    monkeypatch.setattr(ci, "run_native_poc", failed_native)
    monkeypatch.setattr(ci, "cleanup", cleanup)
    assert ci.execute(destination, native_composition=True) == 1
    assert stages == ["old-poc", "native", "cleanup"]
    report = json.loads(destination.with_suffix(".infrastructure.json").read_text())
    assert report["stage"] == "native_composition" and report["status"] == "failed"


def test_cleanup_recovers_native_orphan_ids_even_when_original_report_is_malformed(tmp_path, monkeypatch):
    runtime = ci.Runtime(str(uuid.uuid4()), "test-key", tmp_path)
    original = tmp_path / "report.json"
    original.write_text("malformed")
    native_report = original.with_suffix(".native.json")
    sandbox = str(uuid.uuid4())
    native_report.write_text(json.dumps({"job_id": runtime.job_id, "sandbox_ids": [sandbox]}))
    runtime.additional_reports.add(native_report)
    removed = []
    monkeypatch.setattr(ci, "discover_owned", lambda *_: [])
    monkeypatch.setattr(ci, "cleanup_sandbox", lambda _runtime, identifier: removed.append(identifier))
    monkeypatch.setattr(ci, "cleanup_server", lambda *_: None)
    monkeypatch.setattr(ci, "remaining_resource_counts", lambda: {
        "main_containers": 0, "sidecar_containers": 0, "runtime_volumes": 0})
    assert ci.cleanup(runtime, original) == ["JSONDecodeError"]
    assert removed == [sandbox]


def test_existing_native_evidence_is_not_overwritten(tmp_path, monkeypatch):
    destination = tmp_path / "report.json"
    native_report = destination.with_suffix(".native.json")
    native_report.write_text("existing evidence")
    monkeypatch.setattr(ci, "preflight", lambda: pytest.fail("must not provision"))
    with pytest.raises(ValueError, match="overwrite"):
        ci.execute(destination, native_composition=True)
    assert native_report.read_text() == "existing evidence"
