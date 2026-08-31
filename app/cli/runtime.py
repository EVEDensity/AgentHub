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

import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR_NAME = ".agenthub"
WORKSPACE_ID = "local-admin"

TERMINAL_MISSION_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}

# Exit codes are part of the CLI contract (used by CI callers of `exec`).
EXIT_OK = 0
EXIT_MISSION_FAILED = 1
EXIT_MISSION_CANCELLED = 2
EXIT_WAIT_TIMEOUT = 3
EXIT_INFRA_ERROR = 4

DEFAULT_SERVER_STARTUP_TIMEOUT = 120.0
DEFAULT_MISSION_TIMEOUT = 420.0
DEFAULT_RUNNER_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_TOTAL_TOKENS = 200_000

DEFAULT_MOCK_MODEL = "mock-llm"


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
    config = config or {}
    api_key = os.environ.get("AGENTHUB_CLI_MODEL_API_KEY") or os.environ.get(
        "AGENTHUB_DESKTOP_MODEL_API_KEY", ""
    )
    provider = (
        provider
        or config.get("provider")
        or os.environ.get("AGENTHUB_CLI_PROVIDER", "")
        or ("openai" if api_key else "mock")
    )
    model = (
        model
        or config.get("model")
        or os.environ.get("AGENTHUB_CLI_MODEL", "")
        or DEFAULT_MOCK_MODEL
    )
    base_url = (
        base_url
        or config.get("base_url")
        or os.environ.get("AGENTHUB_CLI_MODEL_BASE_URL", "")
    )
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
    run_dir: Path,
    workspace_root: Path,
    port: int,
    model: CliModelSettings,
    max_total_tokens: int,
    runner_timeout_seconds: float,
) -> dict[str, str]:
    """Env for the isolated SQLite mission-control subprocess.

    The model API key travels only through the subprocess environment;
    it is never written to any file under ``run_dir``.
    """
    env = os.environ.copy()
    env.update(
        {
            "AGENTHUB_DB_BACKEND": "sqlite",
            "AGENTHUB_SQLITE_PATH": str(run_dir / "db" / "agenthub.db"),
            "AGENTHUB_LOCAL_DATA": str(run_dir / "data"),
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
        }
    )
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
    """Own the isolated SQLite mission-control subprocess lifecycle."""

    def __init__(
        self,
        *,
        run_dir: Path,
        workspace_root: Path,
        model: CliModelSettings,
        max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
        runner_timeout_seconds: float = DEFAULT_RUNNER_TIMEOUT_SECONDS,
        port: int | None = None,
    ) -> None:
        self._run_dir = run_dir
        self._workspace_root = workspace_root
        self._model = model
        self._max_total_tokens = max_total_tokens
        self._runner_timeout_seconds = runner_timeout_seconds
        self.port = port or free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None

    def start(self, *, timeout: float = DEFAULT_SERVER_STARTUP_TIMEOUT) -> None:
        (self._run_dir / "db").mkdir(parents=True, exist_ok=True)
        (self._run_dir / "data").mkdir(parents=True, exist_ok=True)
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        env = build_server_env(
            run_dir=self._run_dir,
            workspace_root=self._workspace_root,
            port=self.port,
            model=self._model,
            max_total_tokens=self._max_total_tokens,
            runner_timeout_seconds=self._runner_timeout_seconds,
        )
        log_path = self._run_dir / "mission-control.log"
        self._log_handle = log_path.open("wb")
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
        )
        self._wait_ready(timeout)
        # Give the post-startup desktop runner task time to log in
        # before the first mission is created.
        time.sleep(3.0)

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(
                    "mission-control subprocess exited during startup "
                    f"(code {self._process.returncode}); see "
                    f"{self._run_dir / 'mission-control.log'}"
                )
            try:
                response = httpx.get(self.base_url + "/", timeout=3)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1.0)
        raise RuntimeError(
            f"mission-control did not become ready at {self.base_url}"
        )

    def stop(self) -> None:
        if self._process is not None:
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

    def __enter__(self) -> MissionControlProcess:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


class MissionControlClient:
    """Typed convenience wrapper over the versioned Mission HTTP API."""

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._token: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MissionControlClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def headers(self) -> dict[str, str]:
        if self._token is None:
            raise RuntimeError("not logged in")
        return {"Authorization": f"Bearer {self._token}"}

    def login(self, name: str = "admin", password: str = "admin123") -> None:
        response = self._client.post(
            "/api/auth/login", json={"name": name, "password": password}
        )
        response.raise_for_status()
        token = response.json().get("accessToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError("login returned no access token")
        self._token = token

    def create_and_start_mission(
        self,
        *,
        title: str,
        objective: str,
        time_seconds: int,
    ) -> dict[str, Any]:
        mission_id = f"mis-cli-{uuid.uuid4().hex[:12]}"
        contract_id = f"contract-cli-{uuid.uuid4().hex[:12]}"
        response = self._client.post(
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
        response = self._client.post(
            f"/api/v1/missions/{mission_id}/start", headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def get_mission(self, mission_id: str) -> dict[str, Any]:
        response = self._client.get(
            f"/api/v1/missions/{mission_id}", headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def work_units(self, mission_id: str) -> list[dict[str, Any]]:
        response = self._client.get(
            f"/api/v1/missions/{mission_id}/work-units", headers=self.headers
        )
        response.raise_for_status()
        return response.json().get("workUnits", [])

    def artifacts(self, mission_id: str) -> list[dict[str, Any]]:
        response = self._client.get(
            f"/api/v1/missions/{mission_id}/artifacts", headers=self.headers
        )
        response.raise_for_status()
        return response.json().get("artifacts", [])


@dataclass
class MissionRunResult:
    """Everything the CLI reports after one mission completes."""

    mission_id: str
    status: str
    objective: str
    work_unit_statuses: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    workspace_files: list[str] = field(default_factory=list)
    wall_seconds: float = 0.0
    waited_timeout: bool = False
    exit_code: int = EXIT_INFRA_ERROR

    def to_json(self) -> dict[str, Any]:
        return {
            "missionId": self.mission_id,
            "status": self.status,
            "objective": self.objective,
            "workUnitStatuses": self.work_unit_statuses,
            "artifactCount": len(self.artifacts),
            "artifactKinds": sorted({str(a.get("kind")) for a in self.artifacts}),
            "workspaceFiles": self.workspace_files,
            "wallSeconds": round(self.wall_seconds, 2),
            "waitedTimeout": self.waited_timeout,
            "exitCode": self.exit_code,
        }


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


def list_workspace_files(workspace_root: Path) -> list[str]:
    files: list[str] = []
    if not workspace_root.exists():
        return files
    for path in sorted(workspace_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace_root).as_posix()
        if relative.startswith(".agenthub/") or relative.startswith(".git/"):
            continue
        files.append(relative)
    return files


def execute_objective(
    *,
    objective: str,
    workspace_root: Path,
    run_dir: Path,
    model: CliModelSettings,
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
    runner_timeout_seconds: float = DEFAULT_RUNNER_TIMEOUT_SECONDS,
    mission_timeout: float = DEFAULT_MISSION_TIMEOUT,
    on_status: Any = None,
) -> MissionRunResult:
    """Run one objective end to end and return the structured result.

    Raises ``RuntimeError`` on infrastructure failures (server did not
    start, HTTP errors); mission-level failure is reported through the
    result status, never by faking success.
    """
    title = objective.strip().splitlines()[0][:80] or "CLI mission"
    with MissionControlProcess(
        run_dir=run_dir,
        workspace_root=workspace_root,
        model=model,
        max_total_tokens=max_total_tokens,
        runner_timeout_seconds=runner_timeout_seconds,
    ) as process:
        with MissionControlClient(process.base_url) as client:
            client.login()
            mission = client.create_and_start_mission(
                title=title,
                objective=objective,
                time_seconds=int(runner_timeout_seconds),
            )
            mission_id = str(mission["id"])
            created = time.monotonic()
            waited_timeout = False
            last_status = str(mission.get("status"))
            while mission.get("status") not in TERMINAL_MISSION_STATUSES:
                if time.monotonic() - created > mission_timeout:
                    waited_timeout = True
                    break
                time.sleep(2.0)
                mission = client.get_mission(mission_id)
                status = str(mission.get("status"))
                if status != last_status:
                    last_status = status
                    if on_status is not None:
                        on_status(status)
            wall_seconds = time.monotonic() - created
            units = client.work_units(mission_id)
            artifacts = client.artifacts(mission_id)

    status = str(mission.get("status"))
    return MissionRunResult(
        mission_id=mission_id,
        status=status,
        objective=objective,
        work_unit_statuses=[str(u.get("status")) for u in units],
        artifacts=artifacts,
        workspace_files=list_workspace_files(workspace_root),
        wall_seconds=wall_seconds,
        waited_timeout=waited_timeout,
        exit_code=status_to_exit_code(status, waited_timeout),
    )
