"""Runtime composition for the developer CLI (North Star M0).

Boots an isolated, SQLite-backed Mission Control subprocess with the
in-process desktop local runner enabled, then drives one Mission through
the versioned HTTP API:

    login -> POST /api/v1/missions -> /start -> poll to terminal status.

This mirrors the proven composition used by
``scripts/desktop_token_stress.py``; the CLI adds a stable wrapper,
workspace binding to the current directory, mock-provider fallback when
no model key is present, and structured result reporting.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import httpx

from app.cli.transport import HttpTransport
from app.cli.config import ConfigResolver
from app.cli.sse_client import SseClient
from app.cli.control_api import ArtifactApi, DecisionApi, MissionApi
from app.services.tools.policy import ToolExecutionPolicy, resolve_tool_execution_policy

from app.cli.project_facts import facts_block_for_objective
from app.cli.events import EventCursor, normalize_event, reorder_events
from app.cli.reducer import SessionViewState, reduce_event, render_snapshot
from app.errors import ConfigError
from app.services.workspace_fingerprint import workspace_revision as compute_workspace_revision

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR_NAME = ".agenthub"
WORKSPACE_ID = "local-admin"


def is_frozen() -> bool:
    """True when running as a PyInstaller-frozen binary (npm distribution)."""
    return bool(getattr(sys, "frozen", False))


def server_command(port: int) -> list[str]:
    """Command line that boots the local mission-control subprocess.

    In a source checkout the CLI reuses the ambient interpreter
    (``python -m uvicorn main:app`` from the repository root). In the
    frozen npm distribution there is no interpreter — the binary
    re-invokes itself with the hidden ``_serve`` subcommand, which runs
    the same ``main:app`` ASGI app in-process (``main`` is collected as
    a hidden import, exactly like the mission-control freeze).
    """
    if is_frozen():
        return [sys.executable, "_serve", "--port", str(port)]
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]


def server_cwd() -> str | None:
    """Working directory for the mission-control subprocess.

    ``main:app`` only resolves from the repository root in a source
    checkout; frozen binaries carry their own bundle and run anywhere.
    """
    return None if is_frozen() else str(REPO_ROOT)

TERMINAL_MISSION_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}

# Exit codes are part of the CLI contract (used by CI callers of `exec`).
EXIT_OK = 0
EXIT_MISSION_FAILED = 1
EXIT_MISSION_CANCELLED = 2
EXIT_WAIT_TIMEOUT = 3
EXIT_INFRA_ERROR = 4
# Normative production code; EXIT_INFRA_ERROR remains as a compatibility alias
# for legacy Mission-result callers and existing integrations.
EXIT_USAGE_ERROR = 2
EXIT_INFRASTRUCTURE = 70

DEFAULT_SERVER_STARTUP_TIMEOUT = 120.0
DEFAULT_MISSION_TIMEOUT = 420.0
DEFAULT_RUNNER_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_TOTAL_TOKENS = 200_000

DEFAULT_MOCK_MODEL = "mock-llm"

# Layered project instructions (north-star M1): AGENTS.md files found from
# the workspace root up to the current directory are merged shallowest-first
# so deeper (more specific) entries come last and read as refinements.
AGENTS_MD_NAME = "AGENTS.md"
_PROJECT_INSTRUCTIONS_FILE_ENV = "AGENTHUB_DESKTOP_PROJECT_INSTRUCTIONS_FILE"
_WEB_SEARCH_ENV = "AGENTHUB_DESKTOP_WEB_SEARCH"
_TOOL_PERMISSION_ENV = "AGENTHUB_TOOL_PERMISSION_MODE"


def collect_agents_md_layers(workspace_root: Path, cwd: Path | None = None) -> list[Path]:
    """Return AGENTS.md paths from workspace root toward ``cwd`` (shallow first).

    Both endpoints are resolved and the walk never escapes the workspace
    root. Non-existent files are skipped; the result is ordered so the
    merged prompt reads general → specific.
    """
    root = workspace_root.resolve()
    target = (cwd or workspace_root).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        target = root
    # Walk from the target up to the root, then reverse so the merged
    # prompt reads general (workspace root) → specific (cwd).
    chain: list[Path] = []
    current = target
    while True:
        chain.append(current)
        if current == root or current.parent == current:
            break
        current = current.parent
    layers: list[Path] = []
    for directory in reversed(chain):
        candidate = directory / AGENTS_MD_NAME
        if candidate.is_file():
            layers.append(candidate)
    return layers


def merge_project_instructions(paths: list[Path]) -> str:
    """Merge layered AGENTS.md files into one instruction block."""
    sections: list[str] = []
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not content:
            continue
        sections.append(f"### {path.parent.name or str(path.parent)}/AGENTS.md\n\n{content}")
    return "\n\n".join(sections)


@dataclass(frozen=True)
class CliModelSettings:
    """Model configuration resolved from flags, config file, and env."""

    provider: str
    model: str
    api_key: str
    base_url: str

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"


def resolve_model_settings(
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    config: dict[str, Any] | None,
) -> CliModelSettings:
    """Resolve the model channel without ever writing the key to disk.

    Precedence: explicit flags > config file (no key material stored) >
    environment > mock fallback.
    """
    resolved = ConfigResolver.from_environment().resolve_model(
        provider=provider,
        model=model,
        base_url=base_url,
        config=config,
        default_model=DEFAULT_MOCK_MODEL,
    )
    provider = resolved["provider"]
    model = resolved["model"]
    base_url = resolved["base_url"]
    api_key = resolved["api_key"]
    if provider == "mock":
        # The mock adapter ignores credentials but the desktop runner
        # config loader requires a non-empty key; a sentinel keeps the
        # no-key out-of-box path honest (no fake success — the mock
        # provider simply does not call a real API).
        return CliModelSettings(
            provider="mock", model=model or DEFAULT_MOCK_MODEL,
            api_key=api_key or "mock", base_url=base_url,
        )
    if not api_key:
        raise SystemExit(
            "error: a model API key is required for provider "
            f"{provider!r}. Set AGENTHUB_CLI_MODEL_API_KEY (env-only, "
            "never written to disk), or use --provider mock for the "
            "offline demo channel."
        )
    return CliModelSettings(
        provider=provider, model=model, api_key=api_key, base_url=base_url
    )


def state_dir(cwd: Path) -> Path:
    return cwd / STATE_DIR_NAME


CONFIG_FILE_NAME = "config.json"


def load_config(cwd: Path, *, strict: bool = False) -> dict[str, Any]:
    """Load non-secret CLI configuration with optional strict validation.

    Interactive compatibility callers may use the historical lenient mode;
    production command paths pass ``strict=True`` so malformed state cannot
    silently select a different provider or model.
    """
    config_path = state_dir(cwd) / CONFIG_FILE_NAME
    if not config_path.is_file():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if strict:
            raise ConfigError(f"unable to read {config_path}: {type(exc).__name__}") from exc
        return {}
    if not isinstance(payload, dict):
        if strict:
            raise ConfigError(f"configuration must be a JSON object: {config_path}")
        return {}
    return payload


def _load_config(cwd: Path) -> dict[str, Any]:
    """Backward-compatible lenient config accessor."""
    return load_config(cwd, strict=False)


def build_contract(contract_id: str, time_seconds: int) -> dict[str, Any]:
    """Desktop-style manual contract with the deterministic verifier gate.

    The ``artifact-set.v1`` criterion routes acceptance through the
    independent unattended verifier — the executor cannot self-certify.
    """
    return {
        "id": contract_id,
        "version": 1,
        "repositoryScopes": [],
        "allowedCapabilities": [],
        "budgets": {"timeSeconds": time_seconds, "modelCost": 1, "retries": 0},
        "acceptanceCriteria": [
            {
                "id": "desktop-artifacts",
                "kind": "manual",
                "description": "CLI task artifact verification (files exist, non-empty, byte-identical).",
                "required": True,
                "configuration": {
                    "evaluator": "artifact-set.v1",
                    "workUnitKinds": ["desktop.task"],
                    "minimumArtifacts": 1,
                    "requiredArtifactKinds": ["test-result"],
                },
            }
        ],
        "decisionGates": [],
        "forbiddenActions": [],
    }


def build_server_env(
    *,
    db_path: Path,
    data_dir: Path,
    workspace_root: Path,
    port: int,
    model: CliModelSettings,
    max_total_tokens: int,
    runner_timeout_seconds: float,
    project_instructions_file: Path | None = None,
    web_search: bool = False,
    tool_permission_mode: str | None = None,
    tool_policy: ToolExecutionPolicy | None = None,
    disable_tools: bool = False,
) -> dict[str, str]:
    """Env for the isolated SQLite mission-control subprocess.

    The model API key travels only through the subprocess environment;
    it is never written to any file under the state directory. Project
    instructions (merged layered AGENTS.md), when present, are exposed
    to the desktop model factory through
    ``AGENTHUB_DESKTOP_PROJECT_INSTRUCTIONS_FILE``.
    """
    env = os.environ.copy()
    env.update(
        {
            "AGENTHUB_DB_BACKEND": "sqlite",
            "AGENTHUB_SQLITE_PATH": str(db_path),
            "AGENTHUB_LOCAL_DATA": str(data_dir),
            "AGENTHUB_DESKTOP_LOCAL_RUNNER": "1",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_BASE_URL": f"http://127.0.0.1:{port}",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY": "1",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY_INTERVAL_SECONDS": "1",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_DERIVATION_INTERVAL_SECONDS": "1",
            "AGENTHUB_DESKTOP_WORKSPACE_ROOT": str(workspace_root),
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL": model.model,
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MODEL_BASE_URL": model.base_url,
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_PROVIDER": model.provider,
            "AGENTHUB_DESKTOP_MODEL_API_KEY": model.api_key,
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_ITERATIONS": "8",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOOL_CALLS": "32",
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_MAX_TOTAL_TOKENS": str(max_total_tokens),
            "AGENTHUB_DESKTOP_LOCAL_RUNNER_TIMEOUT_SECONDS": str(
                runner_timeout_seconds
            ),
            # Keep the self-hosted adapter path even if a gateway is
            # configured in the ambient environment.
            "AGENTHUB_LLM_GATEWAY": "",
            "AGENTHUB_DESKTOP_DISABLE_TOOLS": "1" if disable_tools else "0",
        }
    )
    if project_instructions_file is not None:
        env[_PROJECT_INSTRUCTIONS_FILE_ENV] = str(project_instructions_file)
    # North-star M1: the developer CLI exposes the public-web search tool
    # by default; packaged desktop deployments keep it off.
    env[_WEB_SEARCH_ENV] = "1" if web_search else "0"
    # North-star I-6b: Codex-style tool permission tiering. Only a
    # resolved tier travels — an invalid tier fails fast at the CLI.
    if tool_policy is None:
        tool_policy = resolve_tool_execution_policy(
            workspace_root,
            mode=tool_permission_mode,
            environment_value=env.get(_TOOL_PERMISSION_ENV),
        )
    env[_TOOL_PERMISSION_ENV] = tool_policy.mode.value
    return env


def free_port(start: int = 28_100) -> int:
    port = start
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                port += 1
                continue
            return port
    raise RuntimeError("no free port found")


class MissionControlProcess:
    """Own the SQLite mission-control subprocess lifecycle.

    The database and local data live under the persistent ``.agenthub``
    state directory so missions survive across CLI invocations; per-boot
    logs go to ``.agenthub/logs/``.
    """

    def __init__(
        self,
        *,
        state_dir: Path,
        workspace_root: Path,
        model: CliModelSettings,
        max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
        runner_timeout_seconds: float = DEFAULT_RUNNER_TIMEOUT_SECONDS,
        port: int | None = None,
        project_instructions: str = "",
        web_search: bool = False,
        tool_permission_mode: str | None = None,
        disable_tools: bool = False,
    ) -> None:
        self._state_dir = state_dir
        self._workspace_root = workspace_root
        self._model = model
        self._max_total_tokens = max_total_tokens
        self._runner_timeout_seconds = runner_timeout_seconds
        self._project_instructions = project_instructions.strip()
        self._web_search = web_search
        self._tool_permission_mode = tool_permission_mode
        self._tool_policy = resolve_tool_execution_policy(
            workspace_root,
            mode=tool_permission_mode,
            environment_value=os.environ.get(_TOOL_PERMISSION_ENV),
        )
        self._disable_tools = disable_tools
        self.port = port or free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None

    def start(self, *, timeout: float = DEFAULT_SERVER_STARTUP_TIMEOUT) -> None:
        db_dir = self._state_dir / "db"
        data_dir = self._state_dir / "data"
        logs_dir = self._state_dir / "logs"
        for directory in (db_dir, data_dir, logs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        instructions_file: Path | None = None
        if self._project_instructions:
            instructions_file = self._state_dir / "project-instructions.md"
            instructions_file.write_text(
                self._project_instructions + "\n", encoding="utf-8"
            )
        env = build_server_env(
            db_path=db_dir / "agenthub.db",
            data_dir=data_dir,
            workspace_root=self._workspace_root,
            port=self.port,
            model=self._model,
            max_total_tokens=self._max_total_tokens,
            runner_timeout_seconds=self._runner_timeout_seconds,
            project_instructions_file=instructions_file,
            web_search=self._web_search,
            tool_permission_mode=self._tool_permission_mode,
            tool_policy=self._tool_policy,
            disable_tools=self._disable_tools,
        )
        log_path = (
            logs_dir / f"mission-control-{time.strftime('%Y%m%d-%H%M%S')}.log"
        )
        self._log_handle = log_path.open("wb")
        self._process = subprocess.Popen(
            server_command(self.port),
            cwd=server_cwd(),
            env=env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
        )
        self._wait_ready(timeout)

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                log_path = self._log_handle.name if self._log_handle is not None else "<unavailable>"
                raise RuntimeError(
                    "mission-control subprocess exited during startup "
                    f"(code {self._process.returncode}); see "
                    f"{log_path}"
                )
            try:
                response = httpx.get(self.base_url + "/", timeout=3)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            # Poll frequently enough to avoid adding a full second to the
            # first-event benchmark when uvicorn becomes ready between polls.
            time.sleep(0.1)
        raise RuntimeError(
            f"mission-control did not become ready at {self.base_url}"
        )

    def stop(self) -> None:
        if self._process is not None:
            # Terminate the complete child tree.  A shell-spawned uvicorn or
            # PyInstaller child can otherwise survive and keep SQLite/HTTP
            # resources open after Ctrl-C.
            if os.name == "nt" and self._process.poll() is None:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self._process.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    # Fall through to the bounded wait/kill path below. The
                    # CLI must never print a traceback during shutdown.
                    pass
            else:
                self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=10)
            self._process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        _cleanup_local_runtime_artifacts(self._workspace_root)

    def __enter__(self) -> MissionControlProcess:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def _cleanup_local_runtime_artifacts(workspace_root: Path) -> None:
    """Remove only AgentHub-owned transient files after a local run.

    Cleanup is intentionally narrow: user files and externally-created files
    are never traversed or removed.  A failed cleanup is diagnostic-only and
    must not mask the mission result.
    """
    exec_dir = workspace_root / ".agenthub_exec"
    if exec_dir.is_dir():
        for name in ("script.py", "script.sh"):
            try:
                (exec_dir / name).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            exec_dir.rmdir()
        except OSError:
            pass
    transaction_root = workspace_root / ".agenthub" / "change-transactions"
    if transaction_root.is_dir():
        for child in transaction_root.iterdir():
            if child.is_dir() and not any(child.iterdir()):
                try:
                    child.rmdir()
                except OSError:
                    pass
        try:
            transaction_root.rmdir()
        except OSError:
            pass


class MissionControlClient:
    """Typed convenience wrapper over the versioned Mission HTTP API."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._transport = HttpTransport(base_url, timeout)
        self._client = self._transport.client
        self._sse = SseClient(self._transport)
        self.missions_api = MissionApi(self)
        self.decisions_api = DecisionApi(self)
        self.artifacts_api = ArtifactApi(self)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> MissionControlClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def headers(self) -> dict[str, str]:
        return self._transport.headers

    @property
    def _token(self) -> str | None:  # compatibility for existing test seams
        return self._transport._token

    @_token.setter
    def _token(self, value: str | None) -> None:
        self._transport._token = value

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Route API calls through the shared transport.

        A replaced ``_client`` remains a supported unit-test seam; production
        calls cannot bypass request IDs, timeout policy, or retry classification.
        """
        if self._client is not self._transport.client:
            return getattr(self._client, method.lower())(url, **kwargs)
        return self._transport.request(method, url, **kwargs)

    def login(self, name: str = "admin", password: str = "admin123") -> None:
        if self._client is not self._transport.client:
            response = self._client.post(
                "/api/auth/login", json={"name": name, "password": password}
            )
        else:
            response = self._transport.request(
                "POST",
                "/api/auth/login",
                json={"name": name, "password": password},
                require_auth=False,
            )
        response.raise_for_status()
        token = response.json().get("accessToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError("login returned no access token")
        self._transport.set_token(token)

    def create_and_start_mission(
        self,
        *,
        title: str,
        objective: str,
        time_seconds: int,
    ) -> dict[str, Any]:
        mission_id = f"mis-cli-{uuid.uuid4().hex[:12]}"
        contract_id = f"contract-cli-{uuid.uuid4().hex[:12]}"
        response = self._request("POST",
            "/api/v1/missions",
            headers=self.headers,
            json={
                "id": mission_id,
                "title": title,
                "objective": objective,
                "workspaceId": WORKSPACE_ID,
                "source": {"type": "manual"},
                "contract": build_contract(contract_id, time_seconds),
            },
        )
        response.raise_for_status()
        response = self._request("POST",
            f"/api/v1/missions/{mission_id}/start", headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def get_mission(self, mission_id: str) -> dict[str, Any]:
        response = self._request("GET",
            f"/api/v1/missions/{mission_id}", headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def work_units(self, mission_id: str) -> list[dict[str, Any]]:
        response = self._request("GET",
            f"/api/v1/missions/{mission_id}/work-units", headers=self.headers
        )
        response.raise_for_status()
        return response.json().get("workUnits", [])

    def artifacts(self, mission_id: str) -> list[dict[str, Any]]:
        response = self._request("GET",
            f"/api/v1/missions/{mission_id}/artifacts", headers=self.headers
        )
        response.raise_for_status()
        return response.json().get("artifacts", [])

    def missions(self) -> list[dict[str, Any]]:
        response = self._request("GET",
            "/api/v1/missions",
            params={"workspaceId": WORKSPACE_ID, "limit": 200},
            headers=self.headers,
        )
        response.raise_for_status()
        payload = response.json()
        missions = payload.get("missions", [])
        return missions if isinstance(missions, list) else []

    def evidence(self, mission_id: str) -> list[dict[str, Any]]:
        response = self._request("GET",
            f"/api/v1/missions/{mission_id}/evidence", headers=self.headers
        )
        response.raise_for_status()
        payload = response.json()
        evidence = payload.get("evidence", [])
        return evidence if isinstance(evidence, list) else []

    def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        """Ask the control plane to gracefully stop this mission (P0-4 Esc)."""
        response = self._request("POST",
            f"/api/v1/missions/{mission_id}/cancel", headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def checkpoints(self, mission_id: str) -> list[dict[str, Any]]:
        """Return LLM checkpoint rows (token accounting, tool-calls, etc)."""
        try:
            response = self._request("GET",
                f"/api/v1/missions/{mission_id}/checkpoints",
                headers=self.headers,
                params={"limit": 200},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, RuntimeError):
            # Endpoint may not be exposed on every deployment; degrade
            # to an empty list so callers can still aggregate tokens
            # from the artifacts/evidence path if available.
            return []
        rows = payload.get("checkpoints", payload.get("rows", []))
        return rows if isinstance(rows, list) else []

    def lease_work_unit(self, mission_id: str, work_unit_id: str, *, lease_seconds: int = 300) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/lease",
            headers=self.headers,
            json={"leaseSeconds": lease_seconds},
        )
        response.raise_for_status()
        return response.json()

    def recover_work_unit(self, mission_id: str, work_unit_id: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/recover",
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def start_work_unit(self, mission_id: str, work_unit_id: str, *, lease_id: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/start",
            headers=self.headers,
            json={"leaseId": lease_id},
        )
        response.raise_for_status()
        return response.json()

    def heartbeat_work_unit(
        self, mission_id: str, work_unit_id: str, *, lease_id: str, lease_seconds: int = 300
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/heartbeat",
            headers=self.headers,
            json={"leaseId": lease_id, "leaseSeconds": lease_seconds},
        )
        response.raise_for_status()
        return response.json()

    def execution_context(
        self,
        mission_id: str,
        work_unit_id: str,
        *,
        lease_id: str,
    ) -> dict[str, Any]:
        """Read the lease-fenced execution projection used by the Runner.

        Resume validation and execution must observe the same durable
        projection.  This endpoint deliberately returns the bounded prompt
        projection plus the content-minimized checkpoint; it never returns
        raw model prompts, tool arguments, or tool output.
        """
        response = self._request(
            "POST",
            f"/api/v1/missions/{mission_id}/work-units/{work_unit_id}/execution-context",
            headers=self.headers,
            json={"leaseId": lease_id},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def decisions(self, mission_id: str) -> list[dict[str, Any]]:
        """Pending human-in-the-loop decisions (P0-3 tool-call HITL)."""
        try:
            response = self._request("GET",
                f"/api/v1/missions/{mission_id}/decisions", headers=self.headers
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, RuntimeError):
            return []
        rows = payload.get("decisions", [])
        return rows if isinstance(rows, list) else []

    def resolve_decision(
        self, mission_id: str, decision_id: str, *, allow: bool, note: str = "", expected_version: int = 1
    ) -> dict[str, Any]:
        """Answer a pending HITL decision and let the mission continue."""
        response = self._request("POST",
            f"/api/v1/missions/{mission_id}/decisions/{decision_id}/resolve",
            headers=self.headers,
            json={
                "expectedVersion": expected_version,
                "resolution": "RETRY_WORK_UNIT" if allow else "FAIL_MISSION",
                "rationale": note or ("approved by CLI" if allow else "denied by CLI"),
            },
        )
        response.raise_for_status()
        return response.json()

    def events(
        self, mission_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch the ledger of events so far (P0-3 streaming fallback)."""
        response = self._request("GET",
            f"/api/v1/missions/{mission_id}/events",
            headers=self.headers,
            params={"afterSequence": after_sequence, "limit": limit},
        )
        response.raise_for_status()
        payload = response.json()
        events = payload.get("events", [])
        next_seq = int(payload.get("nextSequence", after_sequence))
        if not isinstance(events, list):
            events = []
        return events, next_seq

    def stream_events(
        self,
        mission_id: str,
        *,
        after_sequence: int = 0,
        poll_seconds: float = 0.5,
        max_seconds: float = 2.0,
    ) -> Iterator[dict[str, Any]]:
        """Consume the mission SSE endpoint through the dedicated client."""
        # Preserve the historical test seam that replaces ``_client``.
        if self._client is not self._transport.client:
            self._transport.client = self._client
        yield from self._sse.stream_events(
            mission_id,
            after_sequence=after_sequence,
            poll_seconds=poll_seconds,
            max_seconds=max_seconds,
        )


@dataclass
class MissionRunResult:
    """Everything the CLI reports after one mission completes."""

    mission_id: str
    status: str
    objective: str
    assistant_text: str = ""
    work_unit_statuses: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    workspace_files: list[str] = field(default_factory=list)
    mission_changed_files: list[str] = field(default_factory=list)
    baseline_commit: str | None = None
    baseline_changed_files: list[str] = field(default_factory=list)
    attempt_snapshot_id: str | None = None
    wall_seconds: float = 0.0
    waited_timeout: bool = False
    exit_code: int = EXIT_INFRA_ERROR
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cancelled: bool = False  # True when the mission was gracefully stopped

    def to_json(self) -> dict[str, Any]:
        return {
            "missionId": self.mission_id,
            "status": self.status,
            "objective": self.objective,
            "assistantText": self.assistant_text,
            "workUnitStatuses": self.work_unit_statuses,
            "artifactCount": len(self.artifacts),
            "artifactKinds": sorted({str(a.get("kind")) for a in self.artifacts}),
            "workspaceFiles": self.workspace_files,
            "missionChangedFiles": self.mission_changed_files,
            "baselineCommit": self.baseline_commit,
            "baselineChangedFiles": self.baseline_changed_files,
            "attemptSnapshotId": self.attempt_snapshot_id,
            "wallSeconds": round(self.wall_seconds, 2),
            "waitedTimeout": self.waited_timeout,
            "exitCode": self.exit_code,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens,
            "cancelled": self.cancelled,
        }


@dataclass(frozen=True)
class ResumeExecutionPlan:
    """Fail-closed execution handoff produced before Harness re-entry."""

    mission_id: str
    work_unit_id: str
    attempt: int
    lease_id: str | None
    checkpoint: dict[str, Any] | None
    pending_decision: dict[str, Any] | None
    receipt_decision: str
    can_resume: bool
    refusal_reason: str | None = None
    resume_input: Any | None = None
    execution_context: dict[str, Any] | None = None


def prepare_resume_execution(
    client: MissionControlClient,
    mission_id: str,
    workspace_root: Path,
    *,
    runner_id: str = WORKSPACE_ID,
    lease_seconds: int = 300,
    expected_context_manifest_digest: str | None = None,
    receipt_store: Any | None = None,
) -> ResumeExecutionPlan:
    """Acquire a fresh lease and validate all execution resume fences.

    This function performs no tool or model work.  It is the only safe handoff
    into a Harness resume: a stale lease is recovered, a new lease is claimed,
    pending decisions are surfaced, and ambiguous receipts fail closed.
    """
    gate = resume_work_unit(
        client,
        mission_id,
        workspace_root,
        strict=True,
        expected_context_manifest_digest=expected_context_manifest_digest,
        receipt_store=receipt_store,
    )
    units = [u for u in gate["workUnits"] if isinstance(u, dict)]
    checkpoint = gate.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return ResumeExecutionPlan(mission_id, "", 0, None, None, None, "not_checked", False, "no durable checkpoint")
    work_unit_id = str(checkpoint.get("workUnitId") or checkpoint.get("work_unit_id") or "")
    unit = next((u for u in units if str(u.get("id")) == work_unit_id), None)
    if unit is None:
        return ResumeExecutionPlan(mission_id, work_unit_id, 0, None, checkpoint, None, "not_checked", False, "checkpoint work unit is not part of mission")
    checkpoint_attempt = int(checkpoint.get("attempt") or 0)
    attempt = int(unit.get("attempt") or checkpoint_attempt or 0)
    if attempt < 1:
        return ResumeExecutionPlan(mission_id, work_unit_id, attempt, None, checkpoint, None, "not_checked", False, "invalid execution attempt")
    status = str(unit.get("status") or "")
    def _lease_expired(payload: Mapping[str, Any]) -> bool:
        value = payload.get("expiresAt") or payload.get("expires_at")
        if not isinstance(value, str) or not value:
            return True
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)

    def _lease_from_unit(value: Mapping[str, Any]) -> Mapping[str, Any]:
        lease_value = value.get("lease")
        return lease_value if isinstance(lease_value, Mapping) else {}

    try:
        if status == "RUNNING":
            lease = _lease_from_unit(unit)
            lease_id = str(lease.get("id") or lease.get("leaseId") or "")
            if not lease_id:
                return ResumeExecutionPlan(mission_id, work_unit_id, attempt, None, checkpoint, None, gate["receiptDecision"], False, "running work unit has no lease")
            if _lease_expired(lease):
                client.recover_work_unit(mission_id, work_unit_id)
                renewed = client.lease_work_unit(mission_id, work_unit_id, lease_seconds=lease_seconds)
            else:
                renewed = client.heartbeat_work_unit(mission_id, work_unit_id, lease_id=lease_id, lease_seconds=lease_seconds)
        else:
            if status == "LEASED":
                lease = _lease_from_unit(unit)
                lease_id = str(lease.get("id") or lease.get("leaseId") or "")
                if lease_id and not _lease_expired(lease):
                    renewed = client.heartbeat_work_unit(mission_id, work_unit_id, lease_id=lease_id, lease_seconds=lease_seconds)
                else:
                    if lease_id:
                        client.recover_work_unit(mission_id, work_unit_id)
                    renewed = client.lease_work_unit(mission_id, work_unit_id, lease_seconds=lease_seconds)
            else:
                if status in {"FAILED", "RETRYING"}:
                    client.recover_work_unit(mission_id, work_unit_id)
                renewed = client.lease_work_unit(mission_id, work_unit_id, lease_seconds=lease_seconds)
        lease_payload = renewed.get("lease") if isinstance(renewed, dict) else None
        lease_id = str((lease_payload or {}).get("id") or (lease_payload or {}).get("leaseId") or renewed.get("leaseId") or "")
        if not lease_id:
            raise RuntimeError("lease response did not contain a lease id")
        leased_attempt = int((lease_payload or {}).get("attempt") or renewed.get("attempt") or attempt)
        if leased_attempt < 1:
            raise RuntimeError("lease response contained an invalid attempt")
        if checkpoint_attempt and leased_attempt != checkpoint_attempt:
            return ResumeExecutionPlan(mission_id, work_unit_id, leased_attempt, lease_id, checkpoint, None, gate["receiptDecision"], False, "checkpoint belongs to a different execution attempt")
        attempt = leased_attempt
    except (httpx.HTTPError, RuntimeError, KeyError, TypeError, ValueError) as exc:
        return ResumeExecutionPlan(mission_id, work_unit_id, attempt, None, checkpoint, None, gate["receiptDecision"], False, f"lease acquisition failed: {exc}")

    decisions = client.decisions(mission_id)
    pending = [d for d in decisions if isinstance(d, dict) and str(d.get("status") or d.get("decisionStatus") or "PENDING") == "PENDING"]
    next_action = checkpoint.get("nextAction") or checkpoint.get("next_action") or {}
    next_call_id = str(next_action.get("callId") or next_action.get("call_id") or "") if isinstance(next_action, dict) else ""
    checkpoint_key = str(checkpoint.get("idempotencyKey") or checkpoint.get("idempotency_key") or "")
    def _decision_related(item: dict[str, Any]) -> bool:
        if str(item.get("workUnitId") or item.get("work_unit_id") or work_unit_id) != work_unit_id:
            return False
        decision_attempt = item.get("attempt") or item.get("executionAttempt")
        if decision_attempt is not None and int(decision_attempt) != attempt:
            return False
        decision_call = str(item.get("callId") or item.get("toolCallId") or item.get("tool_call_id") or "")
        decision_key = str(item.get("idempotencyKey") or item.get("idempotency_key") or "")
        if next_call_id and decision_call and decision_call != next_call_id:
            return False
        if checkpoint_key and decision_key and decision_key != checkpoint_key:
            return False
        return True
    related = [d for d in pending if _decision_related(d)]
    if len(related) > 1:
        return ResumeExecutionPlan(mission_id, work_unit_id, attempt, lease_id, checkpoint, None, gate["receiptDecision"], False, "multiple pending decisions for work unit")
    if gate["receiptDecision"] in {"unknown_outcome", "already_succeeded"}:
        can_resume = gate["receiptDecision"] == "already_succeeded"
        reason = None if can_resume else "tool receipt outcome is unknown"
    else:
        can_resume, reason = True, None
    execution_context: dict[str, Any] | None = None
    context_reader = getattr(client, "execution_context", None)
    if callable(context_reader):
        try:
            context_payload = context_reader(
                mission_id,
                work_unit_id,
                lease_id=lease_id,
            )
            if isinstance(context_payload, dict):
                execution_context = context_payload
                projected = context_payload.get("executionContext")
                projected_checkpoint = (
                    projected.get("checkpoint")
                    if isinstance(projected, dict)
                    else None
                )
                if isinstance(projected_checkpoint, dict) and isinstance(checkpoint, dict):
                    projected_id = str(
                        projected_checkpoint.get("id")
                        or projected_checkpoint.get("checkpointId")
                        or ""
                    )
                    checkpoint_id = str(
                        checkpoint.get("id")
                        or checkpoint.get("checkpointId")
                        or ""
                    )
                    projected_attempt = int(projected_checkpoint.get("attempt") or 0)
                    if checkpoint_id and projected_id and checkpoint_id != projected_id:
                        return ResumeExecutionPlan(
                            mission_id, work_unit_id, attempt, lease_id, checkpoint,
                            None, gate["receiptDecision"], False,
                            "execution checkpoint changed during lease acquisition",
                            None, execution_context,
                        )
                    if projected_attempt and projected_attempt != attempt:
                        return ResumeExecutionPlan(
                            mission_id, work_unit_id, attempt, lease_id, checkpoint,
                            None, gate["receiptDecision"], False,
                            "execution checkpoint attempt changed during lease acquisition",
                            None, execution_context,
                        )
        except (httpx.HTTPError, RuntimeError, KeyError, TypeError, ValueError) as exc:
            return ResumeExecutionPlan(
                mission_id,
                work_unit_id,
                attempt,
                lease_id,
                checkpoint,
                None,
                gate["receiptDecision"],
                False,
                f"execution context acquisition failed: {exc}",
            )
    resume_input = None
    if can_resume:
        from app.services.harness_service import HarnessResumeInput
        checkpoint_iteration = int(checkpoint.get("iteration") or 0)
        if checkpoint_iteration < 1:
            return ResumeExecutionPlan(
                mission_id, work_unit_id, attempt, lease_id, checkpoint,
                related[0] if related else None, gate["receiptDecision"],
                False, "checkpoint iteration must be greater than zero",
                None, execution_context,
            )
        recovered: tuple[Any, ...] = ()
        if isinstance(next_action, dict) and next_call_id and gate["receiptDecision"] == "already_succeeded":
            from app.services.model_contract import ToolResult
            recovered = (ToolResult(call_id=next_call_id, name=str(next_action.get("toolName") or next_action.get("tool_name") or "recovered-tool"), success=True, content="[tool result recovered from durable receipt; side effect not replayed]"),)
        resume_input = HarnessResumeInput(
            checkpoint_id=str(checkpoint.get("id") or checkpoint.get("checkpointId") or ""),
            attempt=attempt,
            next_action=next_action if isinstance(next_action, dict) else None,
            recovered_tool_results=recovered,
            start_iteration=checkpoint_iteration,
        )
    return ResumeExecutionPlan(
        mission_id,
        work_unit_id,
        attempt,
        lease_id,
        checkpoint,
        related[0] if related else None,
        gate["receiptDecision"],
        can_resume,
        reason,
        resume_input,
        execution_context,
    )


def status_to_exit_code(status: str, waited_timeout: bool) -> int:
    """Map the Mission terminal status onto the CLI exit-code contract."""
    if waited_timeout:
        return EXIT_WAIT_TIMEOUT
    if status == "SUCCEEDED":
        return EXIT_OK
    if status == "FAILED":
        return EXIT_MISSION_FAILED
    if status == "CANCELLED":
        return EXIT_MISSION_CANCELLED
    return EXIT_WAIT_TIMEOUT


# Default exclude patterns — mirrors the repo's .gitignore.
# Matched against the path relative to workspace_root (posix-style).
_EXCLUDED_DIRS = frozenset({
    ".git", ".agenthub",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".venv", ".env", "venv", "env",
    "node_modules", "frontend/node_modules",
    "frontend/.next", "frontend/.next-codex",
    "data", "assets/tokenizers",
    ".tmppytest", ".runs",
})
_EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".sqlite3", ".db", ".key", ".key.pub")
_EXCLUDED_PREFIXES = (".env",)


def list_workspace_files(workspace_root: Path) -> list[str]:
    """List all project files except common non-source directories.

    Mirrors the repo's ``.gitignore`` — keeps Python source, configs, docs,
    frontend TSX, and excludes caches, venvs, node_modules, runtime data.
    """
    files: list[str] = []
    if not workspace_root.exists():
        return files
    for path in sorted(workspace_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace_root).as_posix()
        # Dir-level exclude: any path component matches _EXCLUDED_DIRS
        parts = relative.split("/")
        if any(p in _EXCLUDED_DIRS for p in parts[:-1]):
            continue
        if relative.startswith(".agenthub/") or relative.startswith(".git/"):
            continue
        # Suffix exclude
        if any(relative.endswith(suf) for suf in _EXCLUDED_SUFFIXES):
            continue
        # Prefix exclude (e.g. .env.local, .env.production)
        filename = parts[-1]
        if any(filename.startswith(pfx) for pfx in _EXCLUDED_PREFIXES):
            continue
        # .tmp/ directory (our venv lives here)
        if ".tmp/" in relative or relative.startswith(".tmp"):
            continue
        files.append(relative)
    return files


def build_resume_context(client: MissionControlClient, mission_id: str) -> str:
    """Build prior-mission context for ``--resume``.

    Reads the prior Mission's objective, terminal status, and its first
    registered artifact bytes (the desktop runner deposits the final
    model summary there). Missing history degrades to an empty string —
    resume never invents context.
    """
    try:
        mission = client.get_mission(mission_id)
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"cannot resume: mission {mission_id} not found") from exc
    objective = str(mission.get("objective") or "").strip()
    status = str(mission.get("status") or "UNKNOWN")
    summary = ""
    try:
        artifacts = client.artifacts(mission_id)
    except httpx.HTTPError:
        artifacts = []
    if artifacts:
        address = str(artifacts[0].get("contentAddress") or "")
        # contentAddress is "local:sha256/<digest>"; read through the
        # artifact store root derived from the same state directory.
        digest = address.split("/")[-1]
        artifact_path = None
        for candidate in _artifact_search_roots():
            candidate_path = candidate / digest[:2] / digest
            if candidate_path.is_file():
                artifact_path = candidate_path
                break
        if artifact_path is not None:
            try:
                summary = artifact_path.read_text(encoding="utf-8").strip()[:4_000]
            except (OSError, UnicodeDecodeError):
                summary = ""
    lines = [
        f"先前任务 {mission_id}（状态：{status}）的目标：",
        objective or "（无目标记录）",
    ]
    if summary:
        lines += ["先前任务的执行总结：", summary]
    return "\n".join(lines)


def workspace_revision(workspace_root: Path) -> str:
    """Return a deterministic workspace revision for resume fencing.

    The revision combines the current Git commit and porcelain status.  It is
    content-free and therefore safe to include in checkpoints and diagnostics.
    Non-Git workspaces still receive a stable hash of relative file metadata.
    """
    return compute_workspace_revision(workspace_root)


def resume_work_unit(
    client: MissionControlClient,
    mission_id: str,
    workspace_root: Path,
    *,
    strict: bool = False,
    expected_context_manifest_digest: str | None = None,
    receipt_store: Any | None = None,
) -> dict[str, Any]:
    """Validate an execution checkpoint before a resume can replay work.

    This is deliberately a read-only gate.  It never starts a WorkUnit or
    executes a tool.  Callers must acquire a fresh lease and pass the returned
    identity to the Runner.  Legacy checkpoints without a workspace fence are
    reported as ``legacy`` and are rejected when ``strict`` is true.
    """
    mission = client.get_mission(mission_id)
    if not isinstance(mission, dict):
        raise RuntimeError(f"cannot safely resume mission {mission_id}: invalid mission payload")
    units = client.work_units(mission_id) if callable(getattr(client, "work_units", None)) else []
    checkpoints = client.checkpoints(mission_id) if callable(getattr(client, "checkpoints", None)) else []
    current_revision = workspace_revision(workspace_root)
    latest = max(
        (row for row in checkpoints if isinstance(row, dict)),
        key=lambda row: int(row.get("sequence", 0) or 0),
        default=None,
    )
    recorded_revision = "" if latest is None else str(latest.get("workspaceRevision") or latest.get("workspace_revision") or "")
    recorded_context_digest = "" if latest is None else str(
        latest.get("contextManifestDigest")
        or latest.get("context_manifest_digest")
        or ""
    )
    # A checkpoint is legacy when either execution fence is absent. Strict
    # resume rejects these rows until the durable schema carries both
    # fingerprints; lenient callers retain historical summary behavior.
    legacy = not bool(recorded_revision and recorded_context_digest)
    revision_matches = bool(recorded_revision) and recorded_revision == current_revision
    context_matches = (
        expected_context_manifest_digest is None
        or recorded_context_digest == expected_context_manifest_digest
    )
    if strict and (legacy or not revision_matches or not context_matches):
        if legacy:
            reason = "checkpoint has no workspace revision"
        elif not revision_matches:
            reason = "workspace revision changed since checkpoint"
        else:
            reason = "context manifest changed since checkpoint"
        raise RuntimeError(f"cannot safely resume mission {mission_id}: {reason}")

    next_action = (latest or {}).get("nextAction") or (latest or {}).get("next_action")
    idempotency_key = (latest or {}).get("idempotencyKey") or (latest or {}).get("idempotency_key")
    receipt_decision = "not_checked"
    if receipt_store is not None and idempotency_key:
        replay = getattr(receipt_store, "replay_decision", None)
        if callable(replay):
            receipt_decision = str(replay(str(idempotency_key), strict=True))
    if strict and next_action is not None:
        from app.services.runner_checkpoint import (
            ResumeProtocol,
            ResumeValidationError,
            validate_resume_protocol,
        )

        if not isinstance(next_action, dict):
            raise ResumeValidationError("checkpoint next action must be an object")

        validate_resume_protocol(
            ResumeProtocol(
                next_action=next_action,
                idempotency_key=str(idempotency_key) if idempotency_key else None,
                workspace_revision=recorded_revision or None,
                context_manifest_digest=recorded_context_digest or None,
            ),
            workspace_revision=current_revision,
            expected_idempotency_prefix=f"{mission_id}/",
        )
    return {
        "missionId": mission_id,
        "missionStatus": mission.get("status"),
        "workUnits": units,
        "checkpoint": latest,
        "workspaceRevision": current_revision,
        "recordedWorkspaceRevision": recorded_revision or None,
        "revisionMatches": revision_matches,
        "recordedContextManifestDigest": recorded_context_digest or None,
        "contextManifestMatches": context_matches,
        "legacy": legacy,
        "nextAction": next_action,
        "idempotencyKey": idempotency_key,
        "receiptDecision": receipt_decision,
    }


def _mission_digest(client: MissionControlClient, mission_id: str) -> str:
    """One mission's (id, status, objective, summary) digest block."""
    try:
        mission = client.get_mission(mission_id)
    except httpx.HTTPError:
        return f"- {mission_id}：（记录不可读）"
    objective = " ".join(
        str(mission.get("objective") or "").split()
    )[:160]
    status = str(mission.get("status") or "UNKNOWN")
    summary = ""
    try:
        artifacts = client.artifacts(mission_id)
    except httpx.HTTPError:
        artifacts = []
    if artifacts:
        address = str(artifacts[0].get("contentAddress") or "")
        digest = address.split("/")[-1]
        for candidate in _artifact_search_roots():
            candidate_path = candidate / digest[:2] / digest
            if candidate_path.is_file():
                try:
                    summary = (
                        candidate_path.read_text(encoding="utf-8").strip()[:600]
                    )
                except (OSError, UnicodeDecodeError):
                    summary = ""
                break
    block = f"- {mission_id}（{status}）：{objective or '（无目标记录）'}"
    if summary:
        block += f"\n  总结：{summary}"
    return block


def build_compact_context(
    client: MissionControlClient, mission_ids: list[str]
) -> str:
    """Compact a chain of missions into one structured context document.

    I-6c interactive compact: instead of chaining every prior mission
    turn by turn, one document carries each mission's objective,
    status, and deposited summary. Everything comes from the local
    mission records — compacting never invents history.
    """
    valid_ids = [mid for mid in mission_ids if mid]
    if not valid_ids:
        return ""
    blocks = [_mission_digest(client, mid) for mid in valid_ids]
    lines = [
        "以下是本会话先前任务的压缩上下文（/compact 生成，"
        "目标/状态/总结均来自本地任务记录）：",
        *blocks,
    ]
    # Keep injected context bounded so compaction cannot consume the next
    # turn's entire model budget when a session has many missions.
    return "\n".join(lines)[:12_000]


def build_compact_manifest(client: MissionControlClient, mission_ids: list[str]) -> dict[str, Any]:
    """Return a source-addressable compact summary for observability."""
    missions: list[dict[str, Any]] = []
    for mission_id in [mid for mid in mission_ids if mid]:
        try:
            mission = client.get_mission(mission_id)
        except httpx.HTTPError:
            missions.append({"missionId": mission_id, "status": "unavailable", "limitations": ["mission read failed"]})
            continue
        missions.append({
            "missionId": mission_id,
            "coveredMissionIds": [mission_id],
            "objective": str(mission.get("objective") or ""),
            "status": str(mission.get("status") or "UNKNOWN"),
            "decisions": [], "filesChanged": [], "artifacts": [],
            "openQuestions": [], "failures": [], "eventIds": [],
        })
    return {"schemaVersion": 1, "coveredMessageRange": None, "coveredMissions": missions, "generatedBy": "context-compiler"}


def _artifact_search_roots() -> list[Path]:
    """Candidate artifact CAS roots for the local state directory."""
    roots: list[Path] = []
    local_data = os.environ.get("AGENTHUB_LOCAL_DATA", "").strip()
    if local_data:
        roots.append(Path(local_data) / "data" / "artifacts")
    roots.append(Path("data") / "artifacts")
    return roots


def list_recent_missions(
    *,
    state_dir: Path,
    workspace_root: Path,
    model: CliModelSettings,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List missions recorded in the persistent local state database."""
    with MissionControlProcess(
        state_dir=state_dir,
        workspace_root=workspace_root,
        model=model,
    ) as process:
        with MissionControlClient(process.base_url) as client:
            client.login()
            return client.missions()[:limit]


def execute_objective(
    *,
    objective: str,
    workspace_root: Path,
    state_dir: Path,
    model: CliModelSettings,
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
    runner_timeout_seconds: float = DEFAULT_RUNNER_TIMEOUT_SECONDS,
    mission_timeout: float = DEFAULT_MISSION_TIMEOUT,
    project_instructions: str = "",
    resume_mission_id: str = "",
    web_search: bool = True,
    tool_permission_mode: str | None = None,
    context_text: str = "",
    on_status: Any = None,
    on_text: Any = None,
    on_event: Any = None,
    on_view_state: Any = None,
    on_snapshot: Any = None,
    on_decision_request: Any = None,  # P0-3: 逐 tool-call HITL
    cancel_event: Any = None,  # threading.Event → P0-4 Esc 中途取消
    capture_attempt_snapshot: bool = True,
    disable_tools: bool = False,
) -> MissionRunResult:
    # CLI argument namespaces may intentionally use None to mean "use the
    # configured default" (notably the no-subcommand chat entrypoint).
    if max_total_tokens is None:
        max_total_tokens = DEFAULT_MAX_TOTAL_TOKENS
    """Run one objective end to end and return the structured result.

    ``resume_mission_id`` prepends the prior Mission's objective, status
    and deposited summary as read-only context. Raises ``RuntimeError``
    on infrastructure failures (server did not start, HTTP errors);
    mission-level failure is reported through the result status, never
    by faking success.

    Optional callbacks for the richer CLI UX (docs/roadmaps/north-star-developer-cli-experience.md):

    - ``on_status``                — periodic status updates
    - ``on_decision_request(dict)`` — a tool call needs user confirmation;
                                      blocking (mission waits until answered)
    - ``cancel_event``              — threading.Event; set it to gracefully
                                      stop the mission mid-flight
    """
    title = objective.strip().splitlines()[0][:80] or "CLI mission"
    from app.services.context_compiler import ContextCompiler
    from app.services.project_manifest import ProjectManifest

    context_compiler = ContextCompiler(
        state_dir,
        provider=model.provider,
        model=model.model,
    )
    # ADR-0107 gated facts injection remains selective, but all model-facing
    # layers now flow through the compiler instead of ad-hoc prompt joins.
    facts_block = facts_block_for_objective(state_dir, objective)
    manifest_prompt = ProjectManifest.discover(workspace_root).to_prompt(
        provider=model.provider,
        model=model.model,
    )
    compiled_project_context = context_compiler.compile(
        conversation="",
        project=project_instructions,
        facts=facts_block,
        manifest=manifest_prompt,
    )
    cancelled_by_user = False
    baseline_files = frozenset()
    baseline_commit = None
    attempt_snapshot = None
    assistant_text_seen = ""
    try:
        from app.cli.ui import git_head_commit, git_status_snapshot
        baseline_commit = git_head_commit(workspace_root)
        baseline_files = git_status_snapshot(workspace_root)
        if capture_attempt_snapshot:
            from app.cli.snapshots import capture_attempt
            attempt_snapshot = capture_attempt(workspace_root, state_dir / "attempt-snapshots")
    except Exception:  # noqa: BLE001
        pass
    with MissionControlProcess(
        state_dir=state_dir,
        workspace_root=workspace_root,
        model=model,
        max_total_tokens=max_total_tokens,
        runner_timeout_seconds=runner_timeout_seconds,
        project_instructions=compiled_project_context.render(),
        web_search=web_search,
                tool_permission_mode=tool_permission_mode,
                disable_tools=disable_tools,
    ) as process:
        with MissionControlClient(process.base_url) as client:
            client.login()
            mission_context = ""
            conversation_context = context_text.strip()
            resume_plan: ResumeExecutionPlan | None = None
            if resume_mission_id and not conversation_context:
                # Resume is an execution handoff, never a new Mission.  The
                # strict gate validates workspace/context fences, reacquires
                # the WorkUnit lease and reconciles receipts before polling.
                from app.services.tools.receipts import SQLiteToolReceiptStore
                try:
                    resume_plan = prepare_resume_execution(
                        client,
                        resume_mission_id,
                        workspace_root,
                        expected_context_manifest_digest=None,
                        receipt_store=SQLiteToolReceiptStore(state_dir / "tool-receipts.sqlite3"),
                    )
                except Exception as exc:
                    raise RuntimeError(f"cannot safely resume mission {resume_mission_id}: {type(exc).__name__}") from exc
                if not resume_plan.can_resume:
                    raise RuntimeError(
                        f"cannot safely resume mission {resume_mission_id}: {resume_plan.refusal_reason or 'resume gate rejected'}"
                    )
                # The prior Mission objective remains authoritative.  Do not
                # inject a lossy summary or create a replacement Mission.
                mission_context = ""
            elif resume_mission_id and conversation_context:
                # Explicit compact context is a compatibility projection
                # used by chat chaining; it is not an execution resume.
                mission_context = conversation_context
            compiled_objective = context_compiler.compile(
                current=objective,
                conversation=conversation_context,
                mission=mission_context,
            )
            if resume_plan is not None:
                mission = client.get_mission(resume_mission_id)
                if not isinstance(mission, dict) or str(mission.get("id") or "") != resume_mission_id:
                    raise RuntimeError("resume mission payload does not match requested mission")
            else:
                mission = client.create_and_start_mission(
                    title=title,
                    objective=compiled_objective.render(),
                    time_seconds=int(runner_timeout_seconds),
                )
            mission_id = str(mission["id"])
            created = time.monotonic()
            if on_status:
                try:
                    on_status(f"启动 mission {mission_id[:20]}...")
                except Exception:  # noqa: BLE001
                    pass
            # A crash can leave an already-persisted Decision without a new
            # SSE event. Resolve that decision before polling so resume never
            # hangs waiting for an event that was emitted before the restart.
            if resume_plan is not None and resume_plan.pending_decision is not None:
                decision = resume_plan.pending_decision
                decision_id = str(decision.get("id") or decision.get("decisionId") or "")
                try:
                    expected_version = int(decision.get("version") or 1)
                except (TypeError, ValueError):
                    expected_version = 1
                try:
                    allow = bool(on_decision_request(decision)) if on_decision_request is not None else False
                    if decision_id:
                        client.resolve_decision(
                            mission_id,
                            decision_id,
                            allow=allow,
                            note="resumed CLI decision",
                            expected_version=expected_version,
                        )
                except Exception:  # noqa: BLE001 - resume decisions fail closed
                    if decision_id:
                        try:
                            client.resolve_decision(
                                mission_id,
                                decision_id,
                                allow=False,
                                note="resume decision handling failed; denied safely",
                                expected_version=expected_version,
                            )
                        except Exception:
                            pass
            waited_timeout = False
            last_status = str(mission.get("status"))
            try:
                cursor = EventCursor()
                view_state = SessionViewState()
                budget_notices: set[int] = set()
                token_total_seen = 0
                while mission.get("status") not in TERMINAL_MISSION_STATUSES:
                    # P0-4: check external cancel signal (Esc / Ctrl+C)
                    if cancel_event is not None and cancel_event.is_set():
                        try:
                            client.cancel_mission(mission_id)
                        except Exception:  # noqa: BLE001
                            pass
                        cancelled_by_user = True
                        if on_status:
                            try:
                                on_status("status: CANCELLED (user requested)")
                            except Exception:  # noqa: BLE001
                                pass
                        break
                    if time.monotonic() - created > mission_timeout:
                        waited_timeout = True
                        break
                    received = False
                    batch = [normalize_event(event) for event in client.stream_events(
                        mission_id,
                        after_sequence=cursor.sequence,
                        poll_seconds=0.2,
                        # Keep reconnect windows short so a terminal mission
                        # is observed promptly instead of waiting through
                        # several two-second SSE batches.
                        max_seconds=min(0.75, max(0.25, mission_timeout)),
                    )]
                    for normalized in reorder_events(event for event in batch if event is not None):
                        received = True
                        if not cursor.accept(normalized):
                            continue
                        view_state = reduce_event(view_state, normalized)
                        assistant_text_seen = view_state.assistant_text
                        snapshot = render_snapshot(view_state)
                        if on_snapshot is not None:
                            try:
                                on_snapshot(snapshot)
                            except Exception:  # noqa: BLE001 - renderer must not break execution
                                pass
                        if on_view_state is not None and on_snapshot is None:
                            try:
                                on_view_state(view_state)
                            except Exception:  # noqa: BLE001
                                pass
                        if on_event is not None and on_snapshot is None:
                            try:
                                on_event(normalized.raw)
                            except Exception:  # noqa: BLE001
                                pass
                        # The server cursor advances only on mission
                        # aggregate events; work-unit sequences are separate
                        # and must not cause mission events to be skipped.
                        event_type = normalized.event_type
                        payload = normalized.payload
                        try:
                            token_total_seen = max(
                                token_total_seen,
                                int(payload.get("promptTokens") or payload.get("prompt_tokens") or 0)
                                + int(payload.get("completionTokens") or payload.get("completion_tokens") or 0),
                            )
                        except (TypeError, ValueError):
                            pass
                        if max_total_tokens > 0 and on_status is not None:
                            ratio = token_total_seen / max_total_tokens
                            for threshold in (70, 85, 95):
                                if ratio >= threshold / 100 and threshold not in budget_notices:
                                    budget_notices.add(threshold)
                                    try:
                                        on_status(f"token budget {threshold}% ({token_total_seen:,}/{max_total_tokens:,})")
                                    except Exception:  # noqa: BLE001
                                        pass
                        text_delta = normalized.text_delta
                        if on_text is not None and on_snapshot is None and text_delta and event_type in {"assistant.delta", "message.delta", "text.delta", "model.output.delta"}:
                            try:
                                on_text(str(text_delta))
                            except Exception:  # noqa: BLE001
                                pass
                        if event_type in {"decision.pending", "decision.lifecycle.requested"}:
                            decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else payload
                            decision_id = str(decision.get("id") or decision.get("decisionId") or "")
                            try:
                                expected_version = int(decision.get("version") or 1)
                            except (TypeError, ValueError):
                                expected_version = 1
                            try:
                                # Headless/CI callers do not provide an
                                # interactive callback: deny by default so a
                                # pending Decision can never hang a mission.
                                allow = bool(on_decision_request(decision)) if on_decision_request is not None else False
                                if decision_id:
                                    client.resolve_decision(
                                        mission_id,
                                        decision_id,
                                        allow=allow,
                                        note="interactive CLI decision",
                                        expected_version=expected_version,
                                    )
                            except Exception:  # noqa: BLE001 - deny/fail closed
                                if decision_id:
                                    try:
                                        client.resolve_decision(
                                            mission_id,
                                            decision_id,
                                            allow=False,
                                            note="CLI decision handling failed; denied safely",
                                            expected_version=expected_version,
                                        )
                                    except Exception:
                                        pass
                        status = normalized.status or ""
                        if status and status != last_status:
                            last_status = status
                            if on_status is not None:
                                try:
                                    on_status(f"status: {status}")
                                except Exception:  # noqa: BLE001
                                    pass
                    # SSE is the primary update path. A bounded mission read
                    # closes the gap when an older deployment emits no events.
                    mission = client.get_mission(mission_id)
                    if not received:
                        time.sleep(0.1)
            except KeyboardInterrupt:
                # P0-4: graceful Esc / Ctrl+C mid-flight → don't exit REPL
                # from here; propagate a CANCELLED result so callers see it.
                try:
                    client.cancel_mission(mission_id)
                except Exception:  # noqa: BLE001
                    pass
                cancelled_by_user = True
                if on_status:
                    try:
                        on_status("status: CANCELLED (user requested)")
                    except Exception:  # noqa: BLE001
                        pass
                # Give the engine up to 3s to actually transition; then
                # fall through and build the result with whatever state
                # we observe.
                for _ in range(6):
                    time.sleep(0.5)
                    try:
                        mission = client.get_mission(mission_id)
                    except Exception:  # noqa: BLE001
                        break
                    if str(mission.get("status")) in TERMINAL_MISSION_STATUSES:
                        break
            wall_seconds = time.monotonic() - created
            units = client.work_units(mission_id)
            artifacts = client.artifacts(mission_id)

            # P0-1: aggregate token usage from checkpoints
            prompt_tokens = 0
            completion_tokens = 0
            try:
                for cp in client.checkpoints(mission_id):
                    prompt_tokens += int(cp.get("prompt_tokens") or 0)
                    completion_tokens += int(cp.get("completion_tokens") or 0)
            except Exception:  # noqa: BLE001 - degrade to 0 on missing endpoint
                pass
            total_tokens = prompt_tokens + completion_tokens

    status = str(mission.get("status"))
    # Report only files changed by this Mission.  ``list_workspace_files``
    # describes the whole workspace and would incorrectly classify every
    # repository source file as a Mission deliverable.
    if attempt_snapshot is not None:
        attempt_snapshot = attempt_snapshot.finalize()
        try:
            attempt_snapshot.write_manifest(work_units=units, artifacts=artifacts)
        except OSError:
            # Snapshot restore remains valid even if review metadata cannot be written.
            pass
    try:
        from app.cli.ui import git_changes_since
        mission_changed_files = git_changes_since(workspace_root, baseline_files)
    except Exception:  # noqa: BLE001
        mission_changed_files = []
    return MissionRunResult(
        mission_id=mission_id,
        status=status,
        objective=objective,
        assistant_text=assistant_text_seen,
        work_unit_statuses=[str(u.get("status")) for u in units],
        artifacts=artifacts,
        workspace_files=mission_changed_files,
        mission_changed_files=mission_changed_files,
        baseline_commit=baseline_commit,
        baseline_changed_files=sorted(baseline_files),
        attempt_snapshot_id=(attempt_snapshot.id if attempt_snapshot is not None else None),
        wall_seconds=wall_seconds,
        waited_timeout=waited_timeout,
        exit_code=status_to_exit_code(status, waited_timeout),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cancelled=cancelled_by_user,
    )
