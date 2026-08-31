"""Model composition for the desktop local runner (split module).

Reads the admin-configured model configuration, builds the request-scoped
ModelPort and the per-task Harness factory with guidance injection.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.services.desktop_guidance import (
    GuidanceInjectingModel,
    GuidanceSourcePort,
)
from app.services.harness_checkpoint import (
    HarnessCheckpointPort,
    HarnessExecutionContext,
)
from app.services.harness_service import (
    FunctionCallingHarness,
    FunctionTool,
    HarnessPort,
    ModelPort,
)
from app.services.model_port import (
    DEFAULT_CONTEXT_CHAR_BUDGET,
    ModelAdapterPort,
    build_function_tool_schemas,
)
from app.services.runner.settings import (
    _DEFAULT_CONTEXT_CHAR_BUDGET,
    _DEFAULT_MAX_ITERATIONS,
    _DEFAULT_MAX_TOOL_CALLS,
    _DEFAULT_MAX_TOTAL_TOKENS,
    DESKTOP_SYSTEM_PROMPT,
    MODEL_BASE_URL_ENV,
    MODEL_ENV,
    PROVIDER_ENV,
    DesktopRunnerError,
)
from app.services.runner_composition import (
    CapabilityBindingFactoryPort,
    HarnessModelFactoryPort,
)

logger = logging.getLogger("agenthub.desktop_local_runner")

# ── Model composition ────────────────────────────────────────────────────

# Optional project instructions appended to the desktop system prompt.
# The file content is expected to be plain text (typically the merged
# layered AGENTS.md produced by the CLI/desktop shell); it is read once
# per factory construction. Empty/missing files are ignored silently so
# deployments without project instructions keep the default prompt.
PROJECT_INSTRUCTIONS_FILE_ENV = "AGENTHUB_DESKTOP_PROJECT_INSTRUCTIONS_FILE"
_PROJECT_INSTRUCTIONS_MAX_CHARS = 20_000


def _load_project_instructions() -> str:
    """Read optional project instructions for the desktop system prompt.

    The file is read once per call site; oversized content is truncated
    with an explicit note so the model knows instructions were cut short.
    """
    path = os.environ.get(PROJECT_INSTRUCTIONS_FILE_ENV, "").strip()
    if not path:
        return ""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        logger.warning("project instructions file unreadable: %s", path)
        return ""
    content = content.strip()
    if len(content) > _PROJECT_INSTRUCTIONS_MAX_CHARS:
        content = content[:_PROJECT_INSTRUCTIONS_MAX_CHARS] + (
            "\n\n... [项目指令过长，已截断至前 "
            f"{_PROJECT_INSTRUCTIONS_MAX_CHARS} 字符]"
        )
    return content


def compose_desktop_system_prompt(base_prompt: str) -> str:
    """Append project instructions (if any) to the desktop system prompt."""
    instructions = _load_project_instructions()
    if not instructions:
        return base_prompt
    return (
        f"{base_prompt}\n\n"
        "## 项目指令（AGENTS.md 合并结果，优先级高于以上默认约定）\n"
        f"{instructions}"
    )



@dataclass(frozen=True)
class DesktopModelConfig:
    """One admin-configured model resolved for the desktop runner."""

    provider: str
    model: str
    api_key: str
    base_url: str


class DesktopModelConfigLoader(Protocol):
    async def __call__(self, model_name: str | None) -> DesktopModelConfig: ...


async def load_default_model_config(
    model_name: str | None = None,
) -> DesktopModelConfig:
    """Read the newest active admin model configuration from the database.

    The API key is decrypted with the same secret mechanism
    ``/api/admin/models`` writes with; the desktop-injected
    ``AGENTHUB_DESKTOP_MODEL_API_KEY`` fills in when a configuration has no
    key of its own.
    """
    from app.db.session import afetch_all
    from app.services.secret_service import decrypt_secret

    rows = await afetch_all(
        "SELECT provider, model_name, api_key, base_url FROM model_configs "
        "WHERE is_active = 1 ORDER BY id DESC"
    )
    selected: Mapping[str, Any] | None = None
    for row in rows:
        if model_name is None or row.get("model_name") == model_name:
            selected = row
            break
    key_env = os.environ.get("AGENTHUB_DESKTOP_MODEL_API_KEY", "")
    model_env = os.environ.get(MODEL_ENV, "").strip()
    base_url_env = os.environ.get(MODEL_BASE_URL_ENV, "").strip()
    provider_env = os.environ.get(PROVIDER_ENV, "").strip()
    if selected is None:
        # Pure-environment fallback: no admin model configuration rows yet,
        # so the desktop-injected key/model/base URL define the provider.
        if not key_env or not model_env:
            raise DesktopRunnerError(
                "no active admin model configuration is available for the "
                "desktop local runner"
            )
        return DesktopModelConfig(
            provider=provider_env or "openai",
            model=model_env,
            api_key=key_env,
            base_url=base_url_env,
        )
    api_key = decrypt_secret(str(selected.get("api_key") or ""))
    if not api_key:
        api_key = key_env
    return DesktopModelConfig(
        provider=str(selected.get("provider") or "") or provider_env or "openai",
        model=str(selected.get("model_name") or "") or model_env,
        api_key=api_key,
        base_url=str(selected.get("base_url") or "") or base_url_env,
    )


class DesktopModelFactory(HarnessModelFactoryPort):
    """Build a request-scoped ModelPort from the admin model configuration."""

    def __init__(
        self,
        config: DesktopModelConfig,
        *,
        context_char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET,
    ) -> None:
        if not config.model.strip():
            raise DesktopRunnerError(
                "desktop model configuration has an empty model name"
            )
        if context_char_budget < 1:
            raise DesktopRunnerError("context_char_budget must be positive")
        self._config = config
        self._context_char_budget = context_char_budget
        from app.services.adapter_manager import adapter_manager

        self._adapter = adapter_manager.get_adapter(config.provider)

    def build(self, tools: Sequence[FunctionTool]) -> ModelPort:
        return ModelAdapterPort(
            self._adapter,
            model=self._config.model,
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            system_prompt=compose_desktop_system_prompt(DESKTOP_SYSTEM_PROMPT),
            tools=build_function_tool_schemas(list(tools)),
            context_char_budget=self._context_char_budget,
        )


class _NoCapabilityBindings(CapabilityBindingFactoryPort):
    """The desktop whitelist is bound directly, not through capabilities."""

    def build(self, execution: HarnessExecutionContext) -> Sequence[Any]:
        del execution
        return []


class DesktopTaskHarnessFactory:
    """Build the request-scoped Harness for one claimed desktop task."""

    def __init__(
        self,
        model_factory: HarnessModelFactoryPort,
        *,
        tools: Sequence[FunctionTool],
        checkpoint_factory: Any | None = None,
        guidance_source: GuidanceSourcePort | None = None,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
        max_total_tokens: int | None = _DEFAULT_MAX_TOTAL_TOKENS,
        max_model_cost: float | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        self._model_factory = model_factory
        self._tools = list(tools)
        self._checkpoint_factory = checkpoint_factory
        self._guidance_source = guidance_source
        self._max_iterations = max_iterations
        self._max_tool_calls = max_tool_calls
        self._max_total_tokens = max_total_tokens
        self._max_model_cost = max_model_cost

    def build(self, context: Mapping[str, Any]) -> HarnessPort:
        work_unit = context.get("workUnit")
        mission = context.get("mission")
        if not isinstance(work_unit, Mapping) or not isinstance(mission, Mapping):
            raise TypeError("desktop task execution context is incomplete")
        execution = HarnessExecutionContext(
            mission_id=str(mission.get("id", "")),
            work_unit_id=str(work_unit.get("id", "")),
            attempt=int(work_unit.get("attempt", 0)),
        )
        checkpoint_port: HarnessCheckpointPort | None = None
        if self._checkpoint_factory is not None:
            lease = work_unit.get("lease")
            lease_id = lease.get("id") if isinstance(lease, Mapping) else None
            if not isinstance(lease_id, str) or not lease_id:
                raise ValueError("desktop task context has no lease id")
            checkpoint_port = self._checkpoint_factory.build(
                execution,
                lease_id=lease_id,
            )
        model = self._model_factory.build(self._tools)
        if self._guidance_source is not None:
            model = GuidanceInjectingModel(
                model,
                self._guidance_source,
                mission_id=str(mission.get("id", "")),
            )
        model_cost_limit = self._max_model_cost
        contract = context.get("contract")
        if isinstance(contract, Mapping):
            budgets = contract.get("budgets")
            if isinstance(budgets, Mapping):
                contract_cost = budgets.get("modelCost")
                if isinstance(contract_cost, (int, float)) and contract_cost >= 0:
                    model_cost_limit = (
                        min(float(contract_cost), model_cost_limit)
                        if model_cost_limit is not None
                        else float(contract_cost)
                    )
        return FunctionCallingHarness(
            model,
            self._tools,
            max_iterations=self._max_iterations,
            max_tool_calls=self._max_tool_calls,
            max_total_tokens=self._max_total_tokens,
            max_model_cost=model_cost_limit,
            checkpoint_port=checkpoint_port,
        )
