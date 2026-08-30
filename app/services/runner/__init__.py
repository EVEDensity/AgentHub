"""Desktop local runner package (split from ``desktop_local_runner.py``).

Modules:
- ``settings``   — env constants, fixed identities, ``DesktopLocalRunnerSettings``
- ``model``      — admin model config loading and model/harness factories
- ``auth``       — runner identity resolution
- ``loops``      — derivation and unattended verification helpers
- ``controller`` — ``DesktopLocalRunnerController`` + lifespan wiring

``app.services.desktop_local_runner`` is a thin re-export facade over this
package, so the historical import path keeps working unchanged.
"""

from __future__ import annotations

from app.services.runner.auth import (
    DesktopAuthenticator,
    DesktopRunnerIdentity,
)
from app.services.runner.controller import (
    DesktopLocalRunnerController,
    DesktopVerifierControlPort,
    desktop_local_runner_settings,
    shutdown_desktop_local_runner,
    startup_desktop_local_runner,
)
from app.services.runner.loops import (
    DesktopMissionSourcePort,
    MissionControlDesktopMissionSource,
    VerifyCommandOutcome,
    derive_desktop_task_work_units,
    extract_run_commands,
    extract_verify_commands,
    run_verify_command,
)
from app.services.runner.sandbox import (
    build_sandbox_policy,
    get_sandbox_runner,
    run_sandboxed,
    sandbox_enabled,
)
from app.services.runner.model import (
    DesktopModelConfig,
    DesktopModelConfigLoader,
    DesktopModelFactory,
    DesktopTaskHarnessFactory,
    load_default_model_config,
)
from app.services.mission_service import DESKTOP_TASK_WORK_UNIT_KIND
from app.services.runner.settings import (
    ADMIN_NAME_ENV,
    ADMIN_PASSWORD_ENV,
    BASE_URL_ENV,
    CONTEXT_CHAR_BUDGET_ENV,
    DESKTOP_ADAPTER_TYPE,
    DESKTOP_AGENT_ID,
    DESKTOP_RUNNER_LABEL,
    DESKTOP_SYSTEM_PROMPT,
    DESKTOP_VERIFIER_ID,
    DESKTOP_VERIFIER_VERSION,
    DESKTOP_WORKSPACE_ID,
    ENABLE_ENV,
    INPROCESS_GUIDANCE_ENV,
    MCP_CONFIG_ENV,
    MODEL_BASE_URL_ENV,
    MODEL_ENV,
    PROVIDER_ENV,
    RUN_COMMAND_MARKER,
    SANDBOX_ENV,
    TOKEN_ENV,
    TOKEN_FILE_ENV,
    USER_ID_ENV,
    VERIFY_COMMAND_MARKER,
    VERIFY_COMMAND_OUTPUT_TAIL_CHARS,
    VERIFY_COMMAND_TIMEOUT_ENV,
    VERIFY_ENV,
    VERIFY_INTERVAL_ENV,
    WORKERS_ENV,
    WORKSPACE_ROOT_ENV,
    DesktopLocalRunnerSettings,
    DesktopRunnerError,
)

__all__ = [
    "ADMIN_NAME_ENV",
    "ADMIN_PASSWORD_ENV",
    "BASE_URL_ENV",
    "CONTEXT_CHAR_BUDGET_ENV",
    "DESKTOP_ADAPTER_TYPE",
    "DESKTOP_AGENT_ID",
    "DESKTOP_RUNNER_LABEL",
    "DESKTOP_SYSTEM_PROMPT",
    "DESKTOP_TASK_WORK_UNIT_KIND",
    "DESKTOP_VERIFIER_ID",
    "DESKTOP_VERIFIER_VERSION",
    "DESKTOP_WORKSPACE_ID",
    "ENABLE_ENV",
    "INPROCESS_GUIDANCE_ENV",
    "MCP_CONFIG_ENV",
    "MODEL_BASE_URL_ENV",
    "MODEL_ENV",
    "PROVIDER_ENV",
    "RUN_COMMAND_MARKER",
    "SANDBOX_ENV",
    "TOKEN_ENV",
    "TOKEN_FILE_ENV",
    "USER_ID_ENV",
    "VERIFY_COMMAND_MARKER",
    "VERIFY_COMMAND_OUTPUT_TAIL_CHARS",
    "VERIFY_COMMAND_TIMEOUT_ENV",
    "VERIFY_ENV",
    "VERIFY_INTERVAL_ENV",
    "WORKERS_ENV",
    "WORKSPACE_ROOT_ENV",
    "DesktopAuthenticator",
    "DesktopLocalRunnerController",
    "DesktopLocalRunnerSettings",
    "DesktopMissionSourcePort",
    "DesktopModelConfig",
    "DesktopModelConfigLoader",
    "DesktopModelFactory",
    "DesktopRunnerError",
    "DesktopRunnerIdentity",
    "DesktopTaskHarnessFactory",
    "DesktopVerifierControlPort",
    "MissionControlDesktopMissionSource",
    "VerifyCommandOutcome",
    "build_sandbox_policy",
    "derive_desktop_task_work_units",
    "desktop_local_runner_settings",
    "extract_run_commands",
    "extract_verify_commands",
    "get_sandbox_runner",
    "load_default_model_config",
    "run_sandboxed",
    "run_verify_command",
    "sandbox_enabled",
    "shutdown_desktop_local_runner",
    "startup_desktop_local_runner",
]
