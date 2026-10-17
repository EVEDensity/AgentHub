"""Provisioning gates, secret handling and exact-resource cleanup for the PoC."""

import json
import os
import stat
import subprocess
import tomllib
import uuid

import pytest

from scripts import opensandbox_poc_ci as ci


@pytest.fixture
def runtime(tmp_path):
    return ci.Runtime(str(uuid.uuid4()), "test-secret-only-in-environment", tmp_path)


def test_provisioning_is_disabled_without_explicit_enable(monkeypatch):
    monkeypatch.setattr(ci, "execute", lambda *_: pytest.fail("must not provision"))
    with pytest.raises(SystemExit) as result:
        ci.main([])
    assert result.value.code == 2


def test_lock_has_all_immutable_linux_images():
    lock = ci.load_lock()
    assert lock["release"] == "1.1.1"
    assert lock["platform"] == "linux/amd64"
    assert lock["source_tag_commit"] != lock["upstream_bom_git_commit"]
    assert len(lock["images"]) == 4
    assert all("@sha256:" in image for image in lock["images"].values())


@pytest.mark.parametrize("change", ["tag", "missing", "platform"])
def test_lock_rejects_mutable_or_incomplete_configuration(tmp_path, monkeypatch, change):
    lock = ci.load_lock()
    if change == "tag":
        lock["images"]["server"] = "docker.io/opensandbox/server:latest"
    elif change == "missing":
        del lock["images"]["egress"]
    else:
        lock["platform"] = "linux/arm64"
    (tmp_path / "images.lock.json").write_text(json.dumps(lock))
    monkeypatch.setattr(ci, "ASSETS", tmp_path)
    with pytest.raises(ValueError):
        ci.load_lock()


def test_preflight_rejects_windows_without_calling_docker(monkeypatch):
    monkeypatch.setattr(ci.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ci, "run", lambda *_: pytest.fail("must not invoke Docker"))
    with pytest.raises(RuntimeError, match="Linux host"):
        ci.preflight()


def test_preflight_rejects_remote_daemon(monkeypatch):
    monkeypatch.setattr(ci.platform, "system", lambda: "Linux")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.example:2376")
    monkeypatch.setattr(ci, "run", lambda *_: pytest.fail("must not invoke Docker"))
    with pytest.raises(RuntimeError, match="local"):
        ci.preflight()


def test_preflight_rejects_nonempty_opensandbox_daemon(monkeypatch):
    monkeypatch.setattr(ci.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    outputs = iter(["unix:///var/run/docker.sock",
                    json.dumps({"OSType": "linux", "Architecture": "amd64"}), "a" * 64])
    monkeypatch.setattr(ci, "run", lambda *_: next(outputs))
    with pytest.raises(RuntimeError, match="disposable daemon"):
        ci.preflight()


def test_preflight_rejects_existing_managed_runtime_volume_without_deleting(monkeypatch):
    monkeypatch.setattr(ci.platform, "system", lambda: "Linux")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    calls = []
    outputs = iter(["unix:///var/run/docker.sock",
                    json.dumps({"OSType": "linux", "Architecture": "amd64"}),
                    "", "", "opensandbox-runtime-existing"])

    def run(command):
        calls.append(command)
        return next(outputs)

    monkeypatch.setattr(ci, "run", run)
    with pytest.raises(RuntimeError, match="disposable daemon"):
        ci.preflight()
    assert not any("rm" in command for command in calls)


def test_generated_config_binds_loopback_and_does_not_persist_key(runtime):
    path = ci.prepare_config(runtime, ci.load_lock()["images"])
    raw = path.read_text()
    config = tomllib.loads(raw)
    assert runtime.api_key not in raw
    assert "api_key" not in config["server"]
    assert config["server"]["host"] == "127.0.0.1"
    assert config["docker"]["network_mode"] == "bridge"
    assert config["docker"]["publish_host"] == "127.0.0.1"
    assert config["docker"]["host_ip"] == "127.0.0.1"
    assert config["docker"]["port_range_max"] - config["docker"]["port_range_min"] >= 100
    assert config["storage"]["allowed_host_paths"]
    assert config["egress"]["mode"] == "dns+nft"
    assert not config["renew_intent"]["enabled"]
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        assert stat.S_IMODE((runtime.directory / "state").stat().st_mode) == 0o777


def test_server_receives_key_by_environment_and_keeps_full_container_id(runtime, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return "a" * 64

    monkeypatch.setattr(ci, "run", run)
    ci.start_server(runtime, ci.load_lock()["images"])
    command, kwargs = calls[-1]
    assert runtime.server_id == "a" * 64
    assert runtime.api_key not in " ".join(command)
    assert kwargs["environment"]["OPENSANDBOX_SERVER_API_KEY"] == runtime.api_key
    assert command[command.index("--network") + 1] == "host"
    assert f"{ci.SERVER_LABEL}={runtime.job_id}" in command
    assert "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock" in command


def test_readiness_checks_correct_missing_and_wrong_keys(runtime, monkeypatch):
    calls = []

    def status(path, key=""):
        calls.append((path, key))
        return 200 if path == "/health" or key == runtime.api_key else 401

    monkeypatch.setattr(ci, "http_status", status)
    ci.wait_ready(runtime)
    assert calls == [("/health", ""), ("/v1/sandboxes", runtime.api_key),
                     ("/v1/sandboxes", ""), ("/v1/sandboxes", "invalid-poc-key")]


def test_readiness_rejects_unauthenticated_api(runtime, monkeypatch):
    monkeypatch.setattr(ci, "http_status", lambda *_: 200)
    with pytest.raises(RuntimeError, match="unauthorized"):
        ci.wait_ready(runtime)


def test_process_errors_never_echo_credentials_or_output(monkeypatch):
    monkeypatch.setattr(ci.subprocess, "run", lambda *_args, **_kwargs:
                        subprocess.CompletedProcess([], 7, "secret-token", "credential-error"))
    with pytest.raises(RuntimeError) as result:
        ci.run(["docker", "inspect"])
    assert "exit 7" in str(result.value)
    assert "secret-token" not in str(result.value)
    assert "credential-error" not in str(result.value)


def test_child_gets_key_only_via_environment_and_must_write_report(runtime, monkeypatch, capsys):
    report = runtime.directory / "report.json"
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["docker", "network", "inspect"]:
            return "172.17.0.1"
        report.write_text(json.dumps({"schema": "agenthub.opensandbox-poc.v1",
                                      "job_id": runtime.job_id, "status": "passed"}))
        return runtime.api_key

    monkeypatch.setattr(ci, "run", run)
    ci.run_poc(runtime, report, "fixed-image")
    command, kwargs = calls[-1]
    assert runtime.api_key not in " ".join(command)
    assert kwargs["environment"]["AGENTHUB_OPENSANDBOX_API_KEY"] == runtime.api_key
    assert kwargs["environment"]["AGENTHUB_OPENSANDBOX_WORKLOAD_IMAGE"] == "fixed-image"
    assert kwargs["environment"]["AGENTHUB_OPENSANDBOX_CANARY_HOST"] == "172.17.0.1"
    assert command[command.index("--job-id") + 1] == runtime.job_id
    assert runtime.api_key not in capsys.readouterr().out


@pytest.mark.parametrize("status", ["failed", None])
def test_zero_exit_without_success_evidence_is_rejected(runtime, monkeypatch, status):
    report = runtime.directory / "report.json"
    report.write_text(json.dumps({"job_id": runtime.job_id, "status": status}))
    monkeypatch.setattr(ci, "run", lambda *_args, **_kwargs: "172.17.0.1")
    with pytest.raises(RuntimeError, match="successful report"):
        ci.run_poc(runtime, report, "fixed-image")


def test_report_ids_are_bounded_and_job_scoped(runtime):
    report = runtime.directory / "report.json"
    sandbox_id = str(uuid.uuid4())
    report.write_text(json.dumps({"job_id": runtime.job_id, "sandbox_ids": [sandbox_id]}))
    ci.read_report_ids(runtime, report)
    assert runtime.sandbox_ids == {sandbox_id}
    report.write_text(json.dumps({"job_id": "another-job", "sandbox_ids": [sandbox_id]}))
    with pytest.raises(ValueError, match="identity"):
        ci.read_report_ids(runtime, report)


def test_cleanup_discovers_only_job_containers_and_exact_dependents(runtime, monkeypatch):
    sandbox_id = str(uuid.uuid4())
    main_id, sidecar_id = "a" * 64, "b" * 64
    containers = {f"{ci.JOB_LABEL}={runtime.job_id}": [main_id],
                  f"{ci.SANDBOX_LABEL}={sandbox_id}": [main_id],
                  f"{ci.SIDECAR_LABEL}={sandbox_id}": [sidecar_id]}
    calls = []
    volumes = [f"opensandbox-runtime-{sandbox_id}"]

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["docker", "inspect"]:
            return sandbox_id
        if command[:2] == ["docker", "ps"]:
            label = command[-1].removeprefix("label=")
            return "\n".join(containers.get(label, []))
        if command[:3] == ["docker", "rm", "--force"]:
            for ids in containers.values():
                if command[-1] in ids:
                    ids.remove(command[-1])
        if command[:3] == ["docker", "volume", "ls"]:
            return "\n".join(volumes)
        if command[:3] == ["docker", "volume", "rm"]:
            volumes.clear()
        return ""

    monkeypatch.setattr(ci, "run", run)
    assert ci.cleanup(runtime, runtime.directory / "absent.json") == []
    removals = [command[-1] for command in calls if command[:2] == ["docker", "rm"]]
    assert removals == [main_id, sidecar_id]
    assert ["docker", "volume", "rm", f"opensandbox-runtime-{sandbox_id}"] in calls
    filters = [command[-1] for command in calls if command[:2] == ["docker", "ps"]]
    assert all(runtime.job_id in value or sandbox_id in value
               or value in {f"label={ci.SANDBOX_LABEL}", f"label={ci.SIDECAR_LABEL}"}
               for value in filters)
    assert runtime.unknown_resource_counts == {
        "main_containers": 0, "sidecar_containers": 0, "runtime_volumes": 0}


def test_report_recovers_orphan_scope_after_main_container_is_gone(runtime, monkeypatch):
    sandbox_id = str(uuid.uuid4())
    report = runtime.directory / "report.json"
    report.write_text(json.dumps({"job_id": runtime.job_id, "sandbox_ids": [sandbox_id]}))
    cleaned = []
    monkeypatch.setattr(ci, "discover_owned", lambda *_: [])
    monkeypatch.setattr(ci, "cleanup_sandbox", lambda _runtime, value: cleaned.append(value))
    monkeypatch.setattr(ci, "cleanup_server", lambda *_: None)
    monkeypatch.setattr(ci, "remaining_resource_counts", lambda: {
        "main_containers": 0, "sidecar_containers": 0, "runtime_volumes": 0})
    assert ci.cleanup(runtime, report) == []
    assert cleaned == [sandbox_id]


def test_cidfile_recovers_server_when_run_interrupted(runtime, monkeypatch):
    server_id = "c" * 64
    (runtime.directory / "server.cid").write_text(server_id)
    inventory = [[server_id], []]
    calls = []
    monkeypatch.setattr(ci, "container_ids", lambda *_: inventory.pop(0))
    monkeypatch.setattr(ci, "run", lambda command: calls.append(command) or "")
    ci.cleanup_server(runtime)
    assert calls == [["docker", "rm", "--force", server_id]]


def test_cleanup_failure_invalidates_success_report(tmp_path, monkeypatch, capsys):
    report = tmp_path / "report.json"
    monkeypatch.setattr(ci, "preflight", lambda: {"os": "linux"})
    monkeypatch.setattr(ci, "run", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(ci, "start_server", lambda *_: None)
    monkeypatch.setattr(ci, "wait_ready", lambda *_: None)
    monkeypatch.setattr(ci, "run_poc", lambda *_: None)
    monkeypatch.setattr(ci, "cleanup", lambda *_: ["RuntimeError"])
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert ci.execute(report) == 1
    result = json.loads(report.with_suffix(".infrastructure.json").read_text())
    assert result["status"] == "failed"
    assert result["cleanup_errors"] == ["RuntimeError"]
    assert "api_key" not in result
    assert "test-secret" not in capsys.readouterr().out


def test_failed_preflight_does_not_try_to_clean_an_unavailable_daemon(tmp_path, monkeypatch):
    report = tmp_path / "report.json"
    monkeypatch.setattr(ci, "preflight", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(ci, "cleanup", lambda *_: pytest.fail("nothing was provisioned"))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert ci.execute(report) == 1
    result = json.loads(report.with_suffix(".infrastructure.json").read_text())
    assert result["error_type"] == "RuntimeError"
    assert result["cleanup_errors"] == []
    assert result["stage"] == "preflight"
    assert result["unknown_resource_counts"] is None


def test_existing_report_is_never_overwritten(tmp_path, monkeypatch):
    report = tmp_path / "report.json"
    report.write_text("existing evidence")
    monkeypatch.setattr(ci, "preflight", lambda: pytest.fail("must not provision"))
    with pytest.raises(ValueError, match="overwrite"):
        ci.execute(report)
    assert report.read_text() == "existing evidence"


def test_existing_infrastructure_failure_is_never_overwritten(tmp_path, monkeypatch):
    report = tmp_path / "report.json"
    infrastructure = report.with_suffix(".infrastructure.json")
    infrastructure.write_text("prior failure evidence")
    monkeypatch.setattr(ci, "preflight", lambda: pytest.fail("must not provision"))
    with pytest.raises(ValueError, match="overwrite"):
        ci.execute(report)
    assert infrastructure.read_text() == "prior failure evidence"


@pytest.mark.parametrize("gateway", ["127.0.0.1", "::1", "0.0.0.0", "224.0.0.1"])
def test_canary_gateway_rejects_invalid_or_host_loopback_addresses(runtime, monkeypatch, gateway):
    monkeypatch.setattr(ci, "run", lambda *_args, **_kwargs: gateway)
    with pytest.raises(ValueError):
        ci.run_poc(runtime, runtime.directory / "report.json", "fixed-image")


def test_unknown_resources_are_counted_after_controller_stop_and_never_deleted(runtime, monkeypatch):
    calls = []
    events = []
    unknown_main, unknown_sidecar = "d" * 64, "e" * 64

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["docker", "ps"]:
            if command[-1] == f"label={ci.SANDBOX_LABEL}":
                events.append("scan")
                return unknown_main
            if command[-1] == f"label={ci.SIDECAR_LABEL}":
                return unknown_sidecar
        if command[:3] == ["docker", "volume", "ls"]:
            return "opensandbox-runtime-unknown\nordinary-managed-data"
        return ""

    monkeypatch.setattr(ci, "run", run)
    monkeypatch.setattr(ci, "cleanup_server", lambda *_: events.append("controller_stopped"))
    errors = ci.cleanup(runtime, runtime.directory / "no-report.json")
    assert errors == ["UnresolvedOpenSandboxResources"]
    assert runtime.unknown_resource_counts == {
        "main_containers": 1, "sidecar_containers": 1, "runtime_volumes": 1}
    assert events == ["controller_stopped", "scan"]
    assert not any("rm" in command for command in calls)
    assert unknown_main not in json.dumps(errors)
    assert "unknown" not in json.dumps(errors)


def test_residual_scan_failure_keeps_counts_unavailable(runtime, monkeypatch):
    monkeypatch.setattr(ci, "discover_owned", lambda *_: [])
    monkeypatch.setattr(ci, "cleanup_server", lambda *_: None)
    monkeypatch.setattr(ci, "remaining_resource_counts",
                        lambda: (_ for _ in ()).throw(RuntimeError("daemon unavailable")))
    runtime.unknown_resource_counts = {"main_containers": 0}
    errors = ci.cleanup(runtime, runtime.directory / "no-report.json")
    assert runtime.unknown_resource_counts is None
    assert errors == ["ResidualScanRuntimeError"]


def test_unresolved_counts_are_persisted_and_fail_infrastructure(tmp_path, monkeypatch):
    report = tmp_path / "report.json"
    counts = {"main_containers": 0, "sidecar_containers": 1, "runtime_volumes": 1}

    def cleanup(runtime, _report):
        runtime.unknown_resource_counts = counts
        return ["UnresolvedOpenSandboxResources"]

    monkeypatch.setattr(ci, "preflight", lambda: {"os": "linux"})
    monkeypatch.setattr(ci, "run", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(ci, "start_server", lambda *_: None)
    monkeypatch.setattr(ci, "wait_ready", lambda *_: None)
    monkeypatch.setattr(ci, "run_poc", lambda *_: None)
    monkeypatch.setattr(ci, "cleanup", cleanup)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert ci.execute(report) == 1
    result = json.loads(report.with_suffix(".infrastructure.json").read_text())
    assert result["status"] == "failed"
    assert result["unknown_resource_counts"] == counts
