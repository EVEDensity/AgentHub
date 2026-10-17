"""Explicit real OpenSandbox HTTP smoke; writes no Mission or Evidence state."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_protocol import (
    WORKLOAD_IMAGE,
    OpenSandboxConfig,
    OpenSandboxError,
    digest,
)


def _check(report: dict, name: str, condition: bool) -> None:
    report["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        raise OpenSandboxError(f"PoC check failed: {name}")


async def _auth_checks(origin: str, report: dict) -> None:
    async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
        for name, headers in (("missing_auth_rejected", {}),
                              ("wrong_auth_rejected", {"OPEN-SANDBOX-API-KEY": "invalid-poc-key"})):
            response = await client.get(origin + "/v1/sandboxes", headers=headers)
            _check(report, name, response.status_code in (401, 403))


async def _transfer_checks(backend: OpenSandboxBackend, first: str, second: str, report: dict) -> None:
    payload = b"agenthub real sandbox\n"
    with tempfile.TemporaryDirectory(prefix="agenthub-opensandbox-input-") as temporary:
        source = Path(temporary) / "input.txt"
        source.write_bytes(payload)
        receipt = await backend.upload(first, "input.txt", source.read_bytes(), sha256=digest(payload))
        result = await backend.execute(first, ["bash", "-c", "tr a-z A-Z < /workspace/input.txt > /workspace/output.txt"])
        _check(report, "file_transform_exit", result.exit_code == 0)
        expected = payload.upper()
        exported = await backend.download(first, "output.txt", sha256=digest(expected))
        target = Path(temporary) / "exported.txt"
        target.write_bytes(exported)
        _check(report, "workspace_transfer_digest", digest(target.read_bytes()) == digest(expected)
               and source.read_bytes() == payload)
        report["files"] = [{"path": receipt.path, "sha256": receipt.sha256,
                            "size_bytes": receipt.size_bytes},
                           {"path": "output.txt", "sha256": digest(exported), "size_bytes": len(exported)}]
    result = await backend.execute(second, ["bash", "-c", "test ! -e /workspace/input.txt"])
    _check(report, "second_sandbox_has_no_first_input", result.exit_code == 0)
    await backend.upload(second, "input.txt", b"different", sha256=digest(b"different"))
    original = await backend.download(first, "input.txt", sha256=digest(payload))
    _check(report, "same_path_different_sandbox_isolated", original == payload)


async def _cancellation_check(backend: OpenSandboxBackend, sandbox_id: str, report: dict) -> None:
    execution = await backend.start_command(sandbox_id, ["bash", "-c",
        "(sleep 2; printf late > /workspace/cancel-marker) & wait"], timeout_seconds=15)
    _check(report, "cancellation_started_running", await backend.command_status(sandbox_id, execution) is None)
    await backend.interrupt(sandbox_id, execution)
    _check(report, "cancellation_terminal_confirmed", await backend.command_status(sandbox_id, execution) is not None)
    await asyncio.sleep(3)
    result = await backend.execute(sandbox_id, ["bash", "-c", "test ! -e /workspace/cancel-marker"])
    _check(report, "cancellation_stopped_child_marker", result.exit_code == 0)


async def _timeout_check(backend: OpenSandboxBackend, sandbox_id: str, report: dict) -> None:
    timed_out = False
    try:
        # Pinned execd keeps its start SSE response open for a 200ms grace.
        # Allow identity delivery before the bounded execution deadline; keep
        # the child marker well beyond that deadline and verify it afterwards.
        result = await backend.execute(sandbox_id, ["bash", "-c",
            "(sleep 5; printf late > /workspace/timeout-marker) & wait"], timeout_seconds=2)
        report["timeout_observation"] = {"cleanup_action": "terminal_response", "exit_code": result.exit_code}
        timed_out = result.exit_code != 0
    except OpenSandboxError as exc:
        if str(exc) != "command timed out":
            raise
        report["timeout_observation"] = exc.diagnostic()
        timed_out = True
    _check(report, "timeout_not_success", timed_out)
    await asyncio.sleep(6)
    result = await backend.execute(sandbox_id, ["bash", "-c", "test ! -e /workspace/timeout-marker"])
    _check(report, "timeout_stopped_child_marker", result.exit_code == 0)


async def _network_check(backend: OpenSandboxBackend, sandbox_id: str, report: dict) -> None:
    host = backend.config.canary_control_host
    if host is None:
        raise OpenSandboxError("network control requires explicit enablement and a configured canary host")
    connections = []

    async def accept(reader, writer):
        connections.append(True)
        writer.close()
        await writer.wait_closed()

    policy = await backend.network_policy(sandbox_id)
    _check(report, "deny_policy_acknowledged", policy.get("policy") == {
        "defaultAction": "deny", "egress": []})
    control = await backend.create_network_control(report["job_id"])
    report["sandbox_ids"].append(control)
    control_policy = await backend.network_policy(control)
    _check(report, "network_control_policy_acknowledged", control_policy.get("policy") == {
        "defaultAction": "deny", "egress": [{"action": "allow", "target": host}]})
    server = await asyncio.start_server(accept, "0.0.0.0", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        _reader, writer = await asyncio.open_connection(host, port)
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.1)
        _check(report, "network_canary_positive_control", len(connections) == 1)
        command = ["timeout", "2", "bash", "-c", f"exec 3<>/dev/tcp/{host}/{port}"]
        allowed = await backend.execute(control, command, timeout_seconds=5)
        await asyncio.sleep(0.1)
        _check(report, "allowed_sandbox_reaches_host_canary", allowed.exit_code == 0 and len(connections) == 2)
        result = await backend.execute(sandbox_id, command, timeout_seconds=5)
        await asyncio.sleep(0.1)
        _check(report, "default_deny_blocks_host_canary", result.exit_code != 0 and len(connections) == 2)


async def run_poc(args: argparse.Namespace, api_key: str) -> dict:
    canary_host = (os.environ.get("AGENTHUB_OPENSANDBOX_CANARY_HOST") or None
                   if getattr(args, "enable_network_control", False) else None)
    config = OpenSandboxConfig(args.endpoint, api_key, enabled=args.enable, canary_control_host=canary_host)
    report = {"schema": "agenthub.opensandbox-poc.v1", "job_id": args.job_id,
              "status": "failed", "stage": "authentication", "sandbox_ids": [], "checks": []}
    backend = OpenSandboxBackend(config)
    try:
        async with asyncio.timeout(240):
            await _auth_checks(config.endpoint.rstrip("/"), report)
            report["stage"] = "create_first_sandbox"
            first = await backend.create(args.job_id)
            report["sandbox_ids"].append(first)
            report["stage"] = "create_second_sandbox"
            second = await backend.create(args.job_id)
            report["sandbox_ids"].append(second)
            report["stage"] = "command_output"
            result = await backend.execute(first, ["bash", "-c", "printf stdout-proof; printf stderr-proof >&2"])
            _check(report, "real_output_and_zero_exit", result.exit_code == 0
                   and "stdout-proof" in result.output and "stderr-proof" in result.output)
            failed = await backend.execute(first, ["bash", "-c", "exit 7"])
            _check(report, "nonzero_exit_preserved", failed.exit_code == 7)
            report["stage"] = "file_transfer_and_isolation"
            await _transfer_checks(backend, first, second, report)
            report["stage"] = "cancellation"
            await _cancellation_check(backend, first, report)
            report["stage"] = "timeout"
            await _timeout_check(backend, first, report)
            report["stage"] = "network_default_deny"
            await _network_check(backend, first, report)
            report["status"] = "passed"
            report["stage"] = "complete"
    except (OpenSandboxError, httpx.HTTPError, TimeoutError, ValueError, OSError) as exc:
        # Error types only: upstream messages/requests may contain sensitive data.
        report["error_type"] = type(exc).__name__
        if isinstance(exc, OpenSandboxError):
            report["error"] = exc.diagnostic()
    finally:
        try:
            await backend.close()
            _check(report, "sandbox_destruction_confirmed", True)
        except (OpenSandboxError, httpx.HTTPError, OSError) as exc:
            report["status"] = "failed"
            report["cleanup_error_type"] = type(exc).__name__
            if isinstance(exc, OpenSandboxError):
                report["cleanup_error"] = exc.diagnostic()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--enable-network-control", action="store_true",
                        help="create a temporary sandbox allowed to reach only the canary host IP")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.enable:
            raise OpenSandboxError("explicit --enable is required")
        if str(UUID(args.job_id)) != args.job_id:
            raise OpenSandboxError("job ID must be a canonical UUID")
        if args.report.exists():
            raise OpenSandboxError("report destination must be new")
        if os.environ.get("AGENTHUB_OPENSANDBOX_WORKLOAD_IMAGE", WORKLOAD_IMAGE) != WORKLOAD_IMAGE:
            raise OpenSandboxError("workload image does not match pinned BOM")
        api_key = os.environ.get("AGENTHUB_OPENSANDBOX_API_KEY", "")
        report = asyncio.run(run_poc(args, api_key))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, indent=2)
        print(json.dumps({"status": report["status"], "checks": len(report["checks"])}))
        return 0 if report["status"] == "passed" else 1
    except (OpenSandboxError, ValueError, OSError):
        print("OpenSandbox PoC refused or unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
