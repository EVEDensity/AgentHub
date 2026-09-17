"""Validate the external prerequisites for the opt-in model Runner profile."""

from __future__ import annotations

import argparse
import os
import socket
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.python.runner_service.config import (
    load_mcp_binding_manifest,
    read_secret_file,
)

_IDENTITY_VARIABLES = (
    "AGENTHUB_RUNNER_RUNNER_ID",
    "AGENTHUB_RUNNER_WORKSPACE_ID",
    "AGENTHUB_RUNNER_ASSIGNED_AGENT_ID",
    "AGENTHUB_RUNNER_ASSIGNED_ADAPTER",
    "AGENTHUB_RUNNER_MODEL",
)
_ENDPOINT_VARIABLES = (
    "AGENTHUB_RUNNER_MISSION_CONTROL_URL",
    "AGENTHUB_RUNNER_MODEL_GATEWAY_URL",
    "AGENTHUB_RUNNER_MCP_ENDPOINT",
)
_SECRET_FILE_VARIABLES = (
    "AGENTHUB_RUNNER_MISSION_CONTROL_TOKEN_FILE",
    "AGENTHUB_RUNNER_MODEL_GATEWAY_TOKEN_FILE",
    "AGENTHUB_RUNNER_MCP_TOKEN_FILE",
)
_PATH_VARIABLES = (
    "AGENTHUB_RUNNER_MCP_BINDINGS_FILE",
    "AGENTHUB_RUNNER_ARTIFACT_HOST_PATH",
)


def validate_runner_deployment(
    environment: Mapping[str, str],
    *,
    check_network: bool,
    check_http: bool = False,
    connect_timeout_seconds: float,
) -> list[str]:
    """Return sanitized validation failures without reading secret values."""

    failures = _validate_required_values(environment)
    failures.extend(_validate_secret_files(environment))
    failures.extend(_validate_manifest(environment))
    failures.extend(_validate_artifact_root(environment))
    if check_network:
        failures.extend(_validate_endpoint_connectivity(environment, connect_timeout_seconds))
    if check_http:
        failures.extend(_validate_endpoint_http(environment, connect_timeout_seconds))
    return failures


def _validate_required_values(environment: Mapping[str, str]) -> list[str]:
    failures: list[str] = []
    for name in _IDENTITY_VARIABLES:
        value = environment.get(name, "").strip()
        if not value:
            failures.append(f"{name} is required")
        elif name == "AGENTHUB_RUNNER_ASSIGNED_ADAPTER" and value == "a2a.outbound":
            failures.append(f"{name} cannot be a2a.outbound")

    for name in _ENDPOINT_VARIABLES:
        value = environment.get(name, "").strip()
        if not value:
            failures.append(f"{name} is required")
            continue
        try:
            parsed = httpx.URL(value)
        except httpx.InvalidURL:
            failures.append(f"{name} is not an absolute HTTP(S) URL")
            continue
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            failures.append(f"{name} is not an absolute credential-free HTTP(S) URL")
    return failures


def _validate_secret_files(environment: Mapping[str, str]) -> list[str]:
    failures: list[str] = []
    for name in _SECRET_FILE_VARIABLES:
        raw_path = environment.get(name, "").strip()
        if not raw_path:
            failures.append(f"{name} is required")
            continue
        try:
            read_secret_file(Path(raw_path))
        except ValueError:
            failures.append(f"{name} is not a valid single-value secret file")
    return failures


def _validate_manifest(environment: Mapping[str, str]) -> list[str]:
    raw_path = environment.get("AGENTHUB_RUNNER_MCP_BINDINGS_FILE", "").strip()
    if not raw_path:
        return ["AGENTHUB_RUNNER_MCP_BINDINGS_FILE is required"]
    try:
        load_mcp_binding_manifest(Path(raw_path))
    except (ValueError, OSError):
        return ["AGENTHUB_RUNNER_MCP_BINDINGS_FILE is not a valid manifest"]
    return []


def _validate_artifact_root(environment: Mapping[str, str]) -> list[str]:
    raw_path = environment.get("AGENTHUB_RUNNER_ARTIFACT_HOST_PATH", "").strip()
    if not raw_path:
        return ["AGENTHUB_RUNNER_ARTIFACT_HOST_PATH is required"]
    path = Path(raw_path)
    try:
        mode = path.stat().st_mode
    except OSError:
        return ["AGENTHUB_RUNNER_ARTIFACT_HOST_PATH is not an existing directory"]
    if not stat.S_ISDIR(mode):
        return ["AGENTHUB_RUNNER_ARTIFACT_HOST_PATH is not an existing directory"]
    if not os.access(path, os.W_OK | os.X_OK):
        return ["AGENTHUB_RUNNER_ARTIFACT_HOST_PATH is not writable"]
    return []


def _validate_endpoint_connectivity(
    environment: Mapping[str, str],
    connect_timeout_seconds: float,
) -> list[str]:
    failures: list[str] = []
    for name in _ENDPOINT_VARIABLES:
        value = environment.get(name, "").strip()
        try:
            parsed = httpx.URL(value)
            host = parsed.host
            if host is None:
                continue
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            with socket.create_connection((host, port), timeout=connect_timeout_seconds):
                pass
        except (httpx.InvalidURL, OSError, ValueError):
            failures.append(f"{name} is not reachable over TCP")
    return failures


def _validate_endpoint_http(
    environment: Mapping[str, str], connect_timeout_seconds: float
) -> list[str]:
    failures: list[str] = []
    token_variables = {
        "AGENTHUB_RUNNER_MISSION_CONTROL_URL": "AGENTHUB_RUNNER_MISSION_CONTROL_TOKEN_FILE",
        "AGENTHUB_RUNNER_MODEL_GATEWAY_URL": "AGENTHUB_RUNNER_MODEL_GATEWAY_TOKEN_FILE",
        "AGENTHUB_RUNNER_MCP_ENDPOINT": "AGENTHUB_RUNNER_MCP_TOKEN_FILE",
    }
    for name in _ENDPOINT_VARIABLES:
        value = environment.get(name, "").strip()
        token_variable = token_variables[name]
        if not value or not environment.get(token_variable, "").strip():
            continue
        try:
            token_path = Path(environment[token_variable])
            response = httpx.get(
                value.rstrip("/") + "/healthz",
                headers={"Authorization": "Bearer " + read_secret_file(token_path)},
                timeout=connect_timeout_seconds,
                follow_redirects=False,
            )
            if not 200 <= response.status_code < 300:
                failures.append(f"{name} health probe returned HTTP {response.status_code}")
        except (httpx.HTTPError, OSError, ValueError, KeyError):
            failures.append(f"{name} health probe failed")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate prerequisites for the mission-runner Compose profile."
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="Validate local configuration and files without TCP endpoint checks.",
    )
    parser.add_argument(
        "--connect-timeout-seconds",
        type=float,
        default=5.0,
    )
    parser.add_argument(
        "--http-probe",
        action="store_true",
        help="Perform authenticated GET /healthz probes against all external endpoints.",
    )
    arguments = parser.parse_args(argv)
    if arguments.connect_timeout_seconds <= 0:
        parser.error("--connect-timeout-seconds must be positive")

    failures = validate_runner_deployment(
        os.environ,
        check_network=not arguments.skip_network,
        check_http=arguments.http_probe,
        connect_timeout_seconds=arguments.connect_timeout_seconds,
    )
    if failures:
        for failure in failures:
            print(f"preflight failed: {failure}", file=sys.stderr)
        return 2
    print("Runner deployment preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
