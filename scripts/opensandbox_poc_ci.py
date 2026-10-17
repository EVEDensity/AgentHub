"""Provision a disposable, explicitly enabled Linux Docker OpenSandbox PoC."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import platform
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "deploy" / "opensandbox"
ENDPOINT = "http://127.0.0.1:18080"
JOB_LABEL = "agenthub.job"
SANDBOX_LABEL = "opensandbox.io/id"
SIDECAR_LABEL = "opensandbox.io/egress-sidecar-for"
VOLUME_LABEL = "opensandbox.io/volume-managed-by"
SERVER_LABEL = "agenthub.poc.server"
IMAGE_PATTERN = re.compile(r"docker\.io/[a-z0-9/_.:-]+@sha256:[a-f0-9]{64}\Z")
CONTAINER_PATTERN = re.compile(r"[a-f0-9]{64}\Z")
HANDLED_ERRORS = (OSError, RuntimeError, ValueError, subprocess.SubprocessError)


@dataclass
class Runtime:
    job_id: str
    api_key: str = field(repr=False)
    directory: Path
    server_id: str = ""
    sandbox_ids: set[str] = field(default_factory=set)
    unknown_resource_counts: dict[str, int] | None = None


def run(command: list[str], *, environment: dict[str, str] | None = None,
        timeout: int = 60) -> str:
    """Never echo command output on failure: diagnostics can contain credentials."""
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout,
                            check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {result.returncode})")
    return result.stdout.strip()


def load_lock() -> dict:
    lock = json.loads((ASSETS / "images.lock.json").read_text(encoding="utf-8"))
    if lock.get("platform") != "linux/amd64":
        raise ValueError("PoC requires the locked linux/amd64 platform")
    images = lock.get("images", {})
    if set(images) != {"server", "execd", "egress", "workload"}:
        raise ValueError("image lock must contain all four PoC images")
    if not all(isinstance(value, str) and IMAGE_PATTERN.fullmatch(value)
               for value in images.values()):
        raise ValueError("every PoC image must have an immutable Docker Hub digest")
    return lock


def preflight() -> dict:
    if platform.system() != "Linux":
        raise RuntimeError("OpenSandbox CI provisioning requires a Linux host")
    docker_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if docker_host != "unix:///var/run/docker.sock":
        raise RuntimeError("PoC requires the local /var/run/docker.sock daemon")
    context = run(["docker", "context", "inspect", "--format",
                   "{{.Endpoints.docker.Host}}"])
    if context != "unix:///var/run/docker.sock":
        raise RuntimeError("remote and alternate Docker contexts are unsupported")
    info = json.loads(run(["docker", "info", "--format", "{{json .}}"]))
    if info.get("OSType") != "linux" or info.get("Architecture") not in {"amd64", "x86_64"}:
        raise RuntimeError("PoC requires a reachable Linux amd64 Docker daemon")
    if (container_ids(SANDBOX_LABEL) or container_ids(SIDECAR_LABEL)
            or managed_runtime_volumes()):
        raise RuntimeError("PoC requires a disposable daemon without existing OpenSandbox resources")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18080))
    return {"os": info["OSType"], "architecture": info["Architecture"],
            "docker_version": info.get("ServerVersion", "unknown")}


def prepare_config(runtime: Runtime, images: dict[str, str]) -> Path:
    config = (ASSETS / "server.config.toml.in").read_text(encoding="utf-8")
    config = config.replace("@EXECD_IMAGE@", images["execd"])
    config = config.replace("@EGRESS_IMAGE@", images["egress"])
    target = runtime.directory / "server.toml"
    target.write_text(config, encoding="utf-8")
    # No credentials are in config. The upstream server runs as UID 0 with
    # all capabilities dropped, so it cannot bypass the host runner's DAC.
    # The parent TemporaryDirectory stays private (0700); only these exact
    # mount targets are readable/writable by the container's different UID.
    target.chmod(0o644)
    state = runtime.directory / "state"
    state.mkdir()
    state.chmod(0o777)
    return target


def start_server(runtime: Runtime, images: dict[str, str]) -> None:
    config = prepare_config(runtime, images)
    environment = os.environ.copy()
    environment["OPENSANDBOX_SERVER_API_KEY"] = runtime.api_key
    command = [
        "docker", "run", "--detach", "--platform", "linux/amd64", "--network", "host",
        "--name", f"agenthub-opensandbox-{runtime.job_id}",
        "--label", f"{SERVER_LABEL}={runtime.job_id}",
        "--cidfile", str(runtime.directory / "server.cid"),
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--env", "OPENSANDBOX_SERVER_API_KEY",
        "--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
        "--mount", f"type=bind,src={config},dst=/etc/opensandbox/config.toml,readonly",
        "--mount", f"type=bind,src={runtime.directory / 'state'},dst=/poc-state",
        images["server"], "--config", "/etc/opensandbox/config.toml",
    ]
    runtime.server_id = run(command, environment=environment).splitlines()[-1]
    if not CONTAINER_PATTERN.fullmatch(runtime.server_id):
        raise RuntimeError("Docker did not return a full server container ID")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_status(path: str, key: str = "") -> int:
    headers = {"OPEN-SANDBOX-API-KEY": key} if key else {}
    request = Request(ENDPOINT + path, headers=headers)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=2) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def wait_ready(runtime: Runtime, *, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if http_status("/health") == 200:
                break
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError("OpenSandbox health deadline expired")
    if http_status("/v1/sandboxes", runtime.api_key) != 200:
        raise RuntimeError("the generated key cannot access the lifecycle API")
    for key in ("", "invalid-poc-key"):
        if http_status("/v1/sandboxes", key) not in {401, 403}:
            raise RuntimeError("lifecycle API did not reject unauthorized access")


def run_poc(runtime: Runtime, report: Path, workload: str) -> None:
    environment = os.environ.copy()
    environment["AGENTHUB_OPENSANDBOX_API_KEY"] = runtime.api_key
    environment["AGENTHUB_OPENSANDBOX_WORKLOAD_IMAGE"] = workload
    gateway = run(["docker", "network", "inspect", "bridge", "--format",
                   "{{(index .IPAM.Config 0).Gateway}}"])
    address = ipaddress.IPv4Address(gateway)
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError("Docker bridge did not return a usable canary IPv4 address")
    environment["AGENTHUB_OPENSANDBOX_CANARY_HOST"] = str(address)
    command = [sys.executable, str(ROOT / "scripts" / "opensandbox_poc.py"),
               "--enable", "--endpoint", ENDPOINT, "--job-id", runtime.job_id,
               "--report", str(report)]
    output = run(command, environment=environment, timeout=480)
    if output:
        print(output.replace(runtime.api_key, "[REDACTED]"))
    result = json.loads(report.read_text(encoding="utf-8"))
    if (not isinstance(result, dict) or result.get("schema") != "agenthub.opensandbox-poc.v1"
            or result.get("job_id") != runtime.job_id or result.get("status") != "passed"):
        raise RuntimeError("PoC did not produce a successful report for this job")


def container_ids(label: str) -> list[str]:
    values = run(["docker", "ps", "--all", "--quiet", "--no-trunc", "--filter",
                  f"label={label}"]).splitlines()
    if not all(CONTAINER_PATTERN.fullmatch(value) for value in values):
        raise RuntimeError("Docker returned an invalid container ID")
    return values


def managed_runtime_volumes() -> list[str]:
    """List only; never infer ownership or deletion rights from this inventory."""
    values = run(["docker", "volume", "ls", "--quiet", "--filter",
                  f"label={VOLUME_LABEL}=server"]).splitlines()
    return [value for value in values if value.startswith("opensandbox-runtime-")]


def remaining_resource_counts() -> dict[str, int]:
    """Conservatively count every unresolved object after exact-scope cleanup."""
    return {"main_containers": len(container_ids(SANDBOX_LABEL)),
            "sidecar_containers": len(container_ids(SIDECAR_LABEL)),
            "runtime_volumes": len(managed_runtime_volumes())}


def read_report_ids(runtime: Runtime, report: Path) -> None:
    if not report.exists():
        return
    data = json.loads(report.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("job_id") != runtime.job_id:
        raise ValueError("PoC report job identity does not match cleanup scope")
    values = data.get("sandbox_ids", [])
    if (not isinstance(values, list) or len(values) > 32
            or not all(isinstance(value, str) for value in values)):
        raise ValueError("invalid PoC sandbox ID list")
    runtime.sandbox_ids.update(str(uuid.UUID(value)) for value in values)


def discover_owned(runtime: Runtime) -> list[str]:
    containers = container_ids(f"{JOB_LABEL}={runtime.job_id}")
    for container in containers:
        value = run(["docker", "inspect", "--format",
                     '{{index .Config.Labels "opensandbox.io/id"}}', container])
        runtime.sandbox_ids.add(str(uuid.UUID(value)))
    return containers


def cleanup_sandbox(runtime: Runtime, sandbox_id: str) -> None:
    for label in (f"{SANDBOX_LABEL}={sandbox_id}", f"{SIDECAR_LABEL}={sandbox_id}"):
        for container in container_ids(label):
            run(["docker", "rm", "--force", container])
        if container_ids(label):
            raise RuntimeError("PoC sandbox container remains after cleanup")
    volume = f"opensandbox-runtime-{sandbox_id}"
    volumes = run(["docker", "volume", "ls", "--quiet", "--filter", f"name=^{volume}$",
                   "--filter", f"label={VOLUME_LABEL}=server"]).splitlines()
    if volumes not in ([], [volume]):
        raise RuntimeError("unexpected Docker runtime volume identity")
    if volumes:
        run(["docker", "volume", "rm", volume])
        remaining = run(["docker", "volume", "ls", "--quiet", "--filter", f"name=^{volume}$",
                         "--filter", f"label={VOLUME_LABEL}=server"])
        if remaining:
            raise RuntimeError("PoC runtime volume remains after cleanup")


def cleanup(runtime: Runtime, report: Path) -> list[str]:
    errors: list[str] = []
    for operation in (lambda: read_report_ids(runtime, report),
                      lambda: discover_owned(runtime)):
        try:
            operation()
        except HANDLED_ERRORS as exc:
            errors.append(type(exc).__name__)
    for sandbox_id in sorted(runtime.sandbox_ids):
        try:
            cleanup_sandbox(runtime, sandbox_id)
        except HANDLED_ERRORS as exc:
            errors.append(type(exc).__name__)
    try:
        cleanup_server(runtime)
    except HANDLED_ERRORS as exc:
        errors.append(type(exc).__name__)
    runtime.unknown_resource_counts = None
    try:
        # Stop the controller first. A lost create response can leave a sidecar
        # without either a client-recorded sandbox ID or the job metadata label.
        # Unknown resources are observed, never adopted or deleted by this scan.
        runtime.unknown_resource_counts = remaining_resource_counts()
        if any(runtime.unknown_resource_counts.values()):
            errors.append("UnresolvedOpenSandboxResources")
    except HANDLED_ERRORS as exc:
        errors.append(f"ResidualScan{type(exc).__name__}")
    return errors


def cleanup_server(runtime: Runtime) -> None:
    cidfile = runtime.directory / "server.cid"
    server_id = runtime.server_id or (cidfile.read_text().strip() if cidfile.exists() else "")
    if not server_id:
        return
    if not CONTAINER_PATTERN.fullmatch(server_id):
        raise RuntimeError("invalid recorded server container ID")
    label = f"{SERVER_LABEL}={runtime.job_id}"
    owned = container_ids(label)
    if not owned:
        return
    if server_id not in owned:
        raise RuntimeError("server container ownership cannot be verified")
    run(["docker", "rm", "--force", server_id])
    if container_ids(label):
        raise RuntimeError("PoC server remains after cleanup")


def interrupt(signum, frame) -> None:
    raise InterruptedError("PoC provisioning interrupted")


def execute(report: Path) -> int:
    lock = load_lock()
    infrastructure = {"status": "failed", "images": lock["images"],
                      "platform": lock["platform"], "cleanup_errors": [],
                      "unknown_resource_counts": None}
    job_id = str(uuid.uuid4())
    infrastructure["job_id"] = job_id
    report.parent.mkdir(parents=True, exist_ok=True)
    destination = report.with_suffix(".infrastructure.json")
    if report.exists() or destination.exists():
        raise ValueError("refusing to overwrite an existing PoC report")
    with tempfile.TemporaryDirectory(prefix="agenthub-opensandbox-") as directory:
        runtime = Runtime(job_id, secrets.token_urlsafe(32), Path(directory))
        docker_ready = False
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::add-mask::{runtime.api_key}", flush=True)
        try:
            infrastructure["stage"] = "preflight"
            infrastructure["docker"] = preflight()
            docker_ready = True
            for name, image in lock["images"].items():
                infrastructure["stage"] = f"pull_{name}"
                run(["docker", "pull", "--platform", "linux/amd64", image], timeout=240)
            infrastructure["stage"] = "start_server"
            start_server(runtime, lock["images"])
            infrastructure["stage"] = "readiness_and_auth"
            wait_ready(runtime)
            infrastructure["stage"] = "poc_checks"
            run_poc(runtime, report, lock["images"]["workload"])
            infrastructure["status"] = "passed"
            infrastructure["stage"] = "complete"
        except (*HANDLED_ERRORS, KeyboardInterrupt) as exc:
            infrastructure["error_type"] = type(exc).__name__
        finally:
            if docker_ready:
                infrastructure["cleanup_errors"] = cleanup(runtime, report)
                infrastructure["unknown_resource_counts"] = runtime.unknown_resource_counts
    if infrastructure["cleanup_errors"]:
        infrastructure["status"] = "failed"
    destination.write_text(json.dumps(infrastructure, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": infrastructure["status"], "job_id": job_id,
                      "stage": infrastructure["stage"],
                      "infrastructure_report": str(destination)}))
    return 0 if infrastructure["status"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true", help="explicitly create disposable containers")
    parser.add_argument("--report", type=Path, default=ROOT / "data" / "opensandbox-poc" / "report.json")
    args = parser.parse_args(argv)
    if not args.enable:
        parser.error("--enable is required; provisioning is disabled by default")
    signal.signal(signal.SIGTERM, interrupt)
    try:
        return execute(args.report.resolve())
    except (*HANDLED_ERRORS, KeyboardInterrupt) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
