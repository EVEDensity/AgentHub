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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import httpx

from app.cli.project_facts import facts_block_for_objective
from app.cli.events import EventCursor, normalize_event, reorder_events
from app.cli.reducer import SessionViewState, reduce_event

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
    config = config or {}
    # API key: 统一名优先 → 桌面版名 → provider 特定名
    _PROVIDER_ENV_KEYS = {
        "deepseek": "DEEPSEEK_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "zhipu": "ZHIPUAI_API_KEY",
        "doubao": "DOUBAO_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "minimax": "MINIMAX_API_KEY",
    }
    api_key = (
        os.environ.get("AGENTHUB_CLI_MODEL_API_KEY")
        or os.environ.get("AGENTHUB_DESKTOP_MODEL_API_KEY", "")
    )
    provider = (
        provider
        or config.get("provider")
        or os.environ.get("AGENTHUB_CLI_PROVIDER", "")
        or ("openai" if api_key else "mock")
    )
    # 如果统一名没设，再检查 provider 特定名（wizard 会存这个）
    if not api_key and provider in _PROVIDER_ENV_KEYS:
        api_key = os.environ.get(_PROVIDER_ENV_KEYS[provider], "")
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


CONFIG_FILE_NAME = "config.json"


def _load_config(cwd: Path) -> dict[str, Any]:
    config_path = state_dir(cwd) / CONFIG_FILE_NAME
    if not config_path.is_file():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
        }
    )
    if project_instructions_file is not None:
        env[_PROJECT_INSTRUCTIONS_FILE_ENV] = str(project_instructions_file)
    # North-star M1: the developer CLI exposes the public-web search tool
    # by default; packaged desktop deployments keep it off.
    env[_WEB_SEARCH_ENV] = "1" if web_search else "0"
    # North-star I-6b: Codex-style tool permission tiering. Only a
    # resolved tier travels — an invalid tier fails fast at the CLI.
    if tool_permission_mode is not None:
        env[_TOOL_PERMISSION_ENV] = tool_permission_mode
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
    ) -> None:
        self._state_dir = state_dir
        self._workspace_root = workspace_root
        self._model = model
        self._max_total_tokens = max_total_tokens
        self._runner_timeout_seconds = runner_timeout_seconds
        self._project_instructions = project_instructions.strip()
        self._web_search = web_search
        self._tool_permission_mode = tool_permission_mode
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

    def missions(self) -> list[dict[str, Any]]:
        response = self._client.get(
            "/api/v1/missions",
            params={"workspaceId": WORKSPACE_ID, "limit": 200},
            headers=self.headers,
        )
        response.raise_for_status()
        payload = response.json()
        missions = payload.get("missions", [])
        return missions if isinstance(missions, list) else []

    def evidence(self, mission_id: str) -> list[dict[str, Any]]:
        response = self._client.get(
            f"/api/v1/missions/{mission_id}/evidence", headers=self.headers
        )
        response.raise_for_status()
        payload = response.json()
        evidence = payload.get("evidence", [])
        return evidence if isinstance(evidence, list) else []

    def cancel_mission(self, mission_id: str) -> dict[str, Any]:
        """Ask the control plane to gracefully stop this mission (P0-4 Esc)."""
        response = self._client.post(
            f"/api/v1/missions/{mission_id}/cancel", headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def checkpoints(self, mission_id: str) -> list[dict[str, Any]]:
        """Return LLM checkpoint rows (token accounting, tool-calls, etc)."""
        try:
            response = self._client.get(
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

    def decisions(self, mission_id: str) -> list[dict[str, Any]]:
        """Pending human-in-the-loop decisions (P0-3 tool-call HITL)."""
        try:
            response = self._client.get(
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
        response = self._client.post(
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
        response = self._client.get(
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
        """Consume the mission SSE endpoint for a bounded reconnect window.

        The bounded window lets callers handle cancellation and reconnects
        without a permanently blocked ``read`` on a quiet stream.
        """
        timeout = httpx.Timeout(connect=5.0, read=max(1.0, max_seconds + 1), write=10.0, pool=10.0)
        try:
            with self._client.stream(
                "GET",
                f"/api/v1/missions/{mission_id}/events/stream",
                headers=self.headers,
                params={
                    "afterSequence": after_sequence,
                    "pollSeconds": poll_seconds,
                    "maxSeconds": max_seconds,
                },
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                data_lines: list[str] = []
                for line in response.iter_lines():
                    if not line:
                        if data_lines:
                            raw = "\n".join(data_lines)
                            data_lines = []
                            try:
                                event = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(event, dict):
                                yield event
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].strip())
        except (httpx.HTTPError, RuntimeError):
            return


@dataclass
class MissionRunResult:
    """Everything the CLI reports after one mission completes."""

    mission_id: str
    status: str
    objective: str
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
    on_decision_request: Any = None,  # P0-3: 逐 tool-call HITL
    cancel_event: Any = None,  # threading.Event → P0-4 Esc 中途取消
) -> MissionRunResult:
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
    # ADR-0107 gated facts injection: facts sharing a keyword with the
    # current objective are appended to the layered AGENTS.md block.
    facts_block = facts_block_for_objective(state_dir, objective)
    if facts_block:
        project_instructions = (
            f"{project_instructions}\n\n{facts_block}"
            if project_instructions
            else facts_block
        )
    cancelled_by_user = False
    baseline_files = frozenset()
    baseline_commit = None
    attempt_snapshot = None
    try:
        from app.cli.ui import git_head_commit, git_status_snapshot
        baseline_commit = git_head_commit(workspace_root)
        baseline_files = git_status_snapshot(workspace_root)
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
        project_instructions=project_instructions,
        web_search=web_search,
        tool_permission_mode=tool_permission_mode,
    ) as process:
        with MissionControlClient(process.base_url) as client:
            client.login()
            full_objective = objective
            if context_text.strip():
                # I-6c: pre-compacted session context (from /compact)
                # replaces the per-turn chain when present.
                full_objective = f"{context_text.strip()}\n\n---\n\n{objective}"
            elif resume_mission_id:
                context = build_resume_context(client, resume_mission_id)
                full_objective = f"{context}\n\n---\n\n{objective}"
            mission = client.create_and_start_mission(
                title=title,
                objective=full_objective,
                time_seconds=int(runner_timeout_seconds),
            )
            mission_id = str(mission["id"])
            created = time.monotonic()
            if on_status:
                try:
                    on_status(f"启动 mission {mission_id[:20]}...")
                except Exception:  # noqa: BLE001
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
                        poll_seconds=0.5,
                        max_seconds=min(2.0, max(0.5, mission_timeout)),
                    )]
                    for normalized in reorder_events(event for event in batch if event is not None):
                        received = True
                        if not cursor.accept(normalized):
                            continue
                        view_state = reduce_event(view_state, normalized)
                        if on_view_state is not None:
                            try:
                                on_view_state(view_state)
                            except Exception:  # noqa: BLE001
                                pass
                        if on_event is not None:
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
                        if on_text is not None and text_delta and event_type in {"assistant.delta", "message.delta", "text.delta", "model.output.delta"}:
                            try:
                                on_text(str(text_delta))
                            except Exception:  # noqa: BLE001
                                pass
                        if event_type in {"decision.pending", "decision.lifecycle.requested"} and on_decision_request is not None:
                            decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else payload
                            decision_id = str(decision.get("id") or decision.get("decisionId") or "")
                            try:
                                expected_version = int(decision.get("version") or 1)
                            except (TypeError, ValueError):
                                expected_version = 1
                            try:
                                allow = bool(on_decision_request(decision))
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
    changed_files = list_workspace_files(workspace_root)
    if attempt_snapshot is not None:
        attempt_snapshot = attempt_snapshot.finalize()
    try:
        from app.cli.ui import git_changes_since
        mission_changed_files = git_changes_since(workspace_root, baseline_files)
    except Exception:  # noqa: BLE001
        mission_changed_files = []
    return MissionRunResult(
        mission_id=mission_id,
        status=status,
        objective=objective,
        work_unit_statuses=[str(u.get("status")) for u in units],
        artifacts=artifacts,
        workspace_files=changed_files,
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
