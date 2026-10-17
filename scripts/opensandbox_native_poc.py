"""Explicit Native/tool/real-command experiment; no paid model or test verdict."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.harness_service import FunctionCallingHarness, HarnessExecutionContext, HarnessRequest
from app.services.model_contract import ModelResponse, ToolCall
from app.services.opensandbox_backend import OpenSandboxBackend
from app.services.opensandbox_harness import SandboxHarnessLimits, SandboxNativeHarness
from app.services.opensandbox_protocol import WORKLOAD_IMAGE, OpenSandboxConfig, OpenSandboxError

SCHEMA = "agenthub.opensandbox-native-poc.v1"
SUCCESS_SCRIPT = "printf native-stdout-proof; printf native-stderr-proof >&2"
FAILURE_SCRIPT = "printf native-failure-proof; exit 7"
CANCEL_SCRIPT = "printf native-cancel-started; sleep 30; printf native-cancel-too-late"


def _check(report: dict, name: str, condition: bool) -> None:
    report["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        raise OpenSandboxError(f"Native PoC check failed: {name}")


class DeterministicModel:
    """Host fixture drives the real Native loop; it is not a supplier response."""
    def __init__(self, script: str):
        self.script = script
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(tool_calls=(ToolCall("fixture-command", "run_shell", {
                "script": self.script, "timeout_seconds": 40 if self.script == CANCEL_SCRIPT else 10,
            }),))
        # Deliberately claim completion even after exit 7: the wrapper must use
        # the observed command result rather than this model text as authority.
        observed = next((message.content for message in reversed(request.messages) if message.role == "tool"),
                        "missing tool result")
        return ModelResponse(content=f"Deterministic fixture final response; observed={observed}")


class ObservedBackend(OpenSandboxBackend):
    """Observe actual returned lifecycle/command IDs without replacing HTTP."""
    def __init__(self, config, report):
        super().__init__(config)
        self.report = report
        self.cancel_started = asyncio.Event()
        self.cancel_sandbox = None
        self.cancel_command = None

    async def create(self, job_id):
        sandbox = await super().create(job_id)
        self.report["sandbox_ids"].append(sandbox)
        return sandbox

    async def start_command(self, sandbox_id, argv, *, timeout_seconds=30):
        command = await super().start_command(sandbox_id, argv, timeout_seconds=timeout_seconds)
        if any(item == CANCEL_SCRIPT or item.endswith("\n" + CANCEL_SCRIPT) for item in argv):
            self.cancel_sandbox, self.cancel_command = sandbox_id, command
            self.cancel_started.set()
        return command


def _harness(backend, job_id, scenario, script):
    execution = HarnessExecutionContext(f"poc-mission-{job_id}", f"poc-{scenario}", 1)
    model = DeterministicModel(script)
    harness = SandboxNativeHarness(execution=execution, backend=backend, job_id=job_id,
        native_factory=lambda tools: FunctionCallingHarness(model, list(tools), max_iterations=2, model_retries=0),
        limits=SandboxHarnessLimits(max_command_seconds=45))
    return harness, HarnessRequest(code="Run the operator fixture command and describe its actual output.",
                                   language="text", timeout=120, execution=execution)


async def _confirm_absent(config, sandbox_id):
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = await client.get(config.endpoint.rstrip("/") + f"/v1/sandboxes/{sandbox_id}",
                                    headers={"OPEN-SANDBOX-API-KEY": config.api_key})
        return response.status_code == 404


def _execution_report(result):
    artifacts = result.artifacts
    if len(artifacts) != 2 or artifacts[0].kind != "test-result" or artifacts[-1].kind != "report":
        raise OpenSandboxError("Native execution did not return its exact output/report bundle")
    report = json.loads(artifacts[-1].content)
    if not isinstance(report, dict) or report.get("schema") != "agenthub.opensandbox-execution-report.v1":
        raise OpenSandboxError("Native execution report has another schema")
    return report


async def _completed_case(backend, report, scenario, script):
    harness, request = _harness(backend, report["job_id"], scenario, script)
    result = await harness.execute(request)
    observed = _execution_report(result)
    commands = observed["commands"]
    if len(commands) != 1:
        raise OpenSandboxError("Native fixture expected exactly one observed command")
    command = commands[0]
    report["observations"].append({"scenario": scenario, "sandbox_id": observed["sandboxId"],
        "command_id": command["commandId"], "exit_code": command["exitCode"],
        "combined_output": command["combinedOutput"], "output_stream": command["outputStream"]})
    if scenario == "success":
        _check(report, "native_success_exit_and_output", result.sandbox.success and command["exitCode"] == 0
               and "native-stdout-proof" in command["combinedOutput"]
               and "native-stderr-proof" in command["combinedOutput"] and command["outputStream"] == "combined")
        _check(report, "native_success_final_output", "native-stdout-proof" in result.sandbox.stdout)
    else:
        _check(report, "native_failure_exit_preserved", command["exitCode"] == 7
               and "native-failure-proof" in command["combinedOutput"])
        _check(report, "native_failure_not_success", result.sandbox.success is False)
    _check(report, f"native_{scenario}_cleanup_confirmed", observed["cleanupConfirmed"] is True
           and await _confirm_absent(backend.config, observed["sandboxId"]))


async def _cancel_case(backend, report):
    harness, request = _harness(backend, report["job_id"], "cancel", CANCEL_SCRIPT)
    task = asyncio.create_task(harness.execute(request))
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    propagated = False
    try:
        await asyncio.wait_for(backend.cancel_started.wait(), timeout=120)
        _check(report, "native_cancellation_command_started", bool(backend.cancel_command))
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=30)
        propagated = bool(done) and task.cancelled()
        if done and not propagated:
            task.result()
        _check(report, "native_cancellation_propagated", propagated)
        _check(report, "native_cancellation_cleanup_confirmed",
               await _confirm_absent(backend.config, backend.cancel_sandbox))
    finally:
        if not task.done():
            task.cancel()
        await asyncio.wait({task}, timeout=1)


async def _budgeted_roundtrip(backend, report):
    # The CI-only scenario owns its disposable SQLite, HTTP model fixture, CAS
    # and authenticated Mission clients. Production application imports no tests.
    from tests.integration.opensandbox_native_scenario import exercise_budgeted_roundtrip

    outcome = await exercise_budgeted_roundtrip(backend, report["job_id"])
    report["budgeted_roundtrip"] = outcome
    _check(report, "runner_budget_artifact_integrity_terminal", outcome["terminal_status"] == "SUCCEEDED"
           and outcome["evidence_verdict"] == "PASS" and outcome["artifacts"] == 2
           and outcome["model_http_requests"] == 2 and outcome["spent_nano_usd"] == 22_000
           and len(outcome["sandbox_ids"]) == 1 and outcome["sandbox_ids"][0] in report["sandbox_ids"]
           and len(report["sandbox_ids"]) == len(set(report["sandbox_ids"])) == 4)
    for sandbox in outcome["sandbox_ids"]:
        _check(report, "runner_sandbox_cleanup_confirmed", await _confirm_absent(backend.config, sandbox))


async def run_poc(args, api_key):
    report = {"schema": SCHEMA, "job_id": args.job_id, "status": "failed", "stage": "native_success",
              "model_fixture": "deterministic-canonical-model.v1", "paid_provider_called": False,
              "image": WORKLOAD_IMAGE, "sandbox_ids": [], "checks": [], "observations": []}
    backend = ObservedBackend(OpenSandboxConfig(args.endpoint, api_key, enabled=args.enable), report)
    try:
        async with asyncio.timeout(360):
            await _completed_case(backend, report, "success", SUCCESS_SCRIPT)
            report["stage"] = "native_failure"
            await _completed_case(backend, report, "failure", FAILURE_SCRIPT)
            report["stage"] = "native_cancellation"
            await _cancel_case(backend, report)
            _check(report, "fresh_sandboxes_per_execution", len(report["sandbox_ids"]) == 3
                   and len(set(report["sandbox_ids"])) == 3)
            report["stage"] = "budgeted_runner_roundtrip"
            await _budgeted_roundtrip(backend, report)
            report["status"], report["stage"] = "passed", "complete"
    except Exception as exc:  # noqa: BLE001 - retain only the local type, never remote text/headers
        report["error_type"] = type(exc).__name__
        if isinstance(exc, OpenSandboxError):
            report["error"] = exc.diagnostic()
    finally:
        try:
            await backend.close()
            _check(report, "backend_close_confirmed", True)
        except Exception as exc:  # noqa: BLE001 - cleanup failures invalidate otherwise successful evidence
            report["status"] = "failed"
            report["cleanup_error_type"] = type(exc).__name__
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not args.enable or str(UUID(args.job_id)) != args.job_id:
            raise OpenSandboxError("explicit enablement and canonical job identity are required")
        if args.report.exists():
            raise OpenSandboxError("report destination must be new")
        if os.environ.get("AGENTHUB_OPENSANDBOX_WORKLOAD_IMAGE", WORKLOAD_IMAGE) != WORKLOAD_IMAGE:
            raise OpenSandboxError("Native workload image differs from the pinned image")
        result = asyncio.run(run_poc(args, os.environ.get("AGENTHUB_OPENSANDBOX_API_KEY", "")))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as destination:
            json.dump(result, destination, indent=2)
        print(json.dumps({"status": result["status"], "checks": len(result["checks"])}))
        return 0 if result["status"] == "passed" else 1
    except (OpenSandboxError, ValueError, OSError):
        print("Native OpenSandbox experiment refused or unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
