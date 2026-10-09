"""Resolve a leased controlled root into a scoped model Harness."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.runner_context_validation import (
    _A2A_INBOUND_CONTEXT_PROFILE,
    _DESKTOP_TASK_CONTEXT_PROFILE,
    _MISSION_FORK_CONTEXT_PROFILE,
    _ModelContextProfile,
    _required_mapping,
    _required_string,
)
from app.services.runner_model_context import _compile_model_context
from app.services.runner_protocols import (
    ClaimedHarnessFactoryPort,
    ClaimedWorkExecution,
    ClaimedWorkResolutionError,
    ClaimedWorkResolver,
    MissionControlRunnerPort,
    RunnerExecutionInput,
)


class KindAwareClaimedWorkResolver:
    """Route claimed work through the resolver registered for its durable kind."""

    def __init__(self, resolvers: Mapping[str, ClaimedWorkResolver]) -> None:
        if not resolvers:
            raise ValueError("claimed WorkUnit resolvers must be non-empty")
        if len(resolvers) > 32:
            raise ValueError("claimed WorkUnit resolver count exceeds limit")
        if any(
            not isinstance(kind, str)
            or not kind.strip()
            or kind != kind.strip()
            or len(kind) > 255
            for kind in resolvers
        ):
            raise ValueError("claimed WorkUnit resolver kind is invalid")
        if any(
            not callable(getattr(resolver, "resolve", None))
            for resolver in resolvers.values()
        ):
            raise TypeError("claimed WorkUnit resolver is invalid")
        self._resolvers = dict(resolvers)
        self._supported_work_unit_kinds = tuple(sorted(self._resolvers))

    @property
    def supported_work_unit_kinds(self) -> tuple[str, ...]:
        return self._supported_work_unit_kinds

    async def resolve(
        self,
        work_unit: Mapping[str, Any],
    ) -> ClaimedWorkExecution:
        kind = _required_string(work_unit, "kind")
        resolver = self._resolvers.get(kind)
        if resolver is None:
            raise ClaimedWorkResolutionError(
                f"claimed WorkUnit kind is not supported: {kind}"
            )
        return await resolver.resolve(work_unit)


class _ClaimedModelWorkResolver:
    """Resolve one exact claimed root into model input and a scoped Harness."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        *,
        runner_id: str,
        harness_factory: ClaimedHarnessFactoryPort,
        profile: _ModelContextProfile,
        max_context_chars: int = 32_768,
        max_timeout_seconds: float = 300.0,
    ) -> None:
        if max_context_chars < 1:
            raise ValueError("max_context_chars must be positive")
        if max_timeout_seconds <= 0:
            raise ValueError("max_timeout_seconds must be positive")
        self._control = control
        self._runner_id = runner_id
        self._harness_factory = harness_factory
        self._profile = profile
        self._max_context_chars = max_context_chars
        self._max_timeout_seconds = max_timeout_seconds

    async def resolve(
        self,
        work_unit: Mapping[str, Any],
    ) -> ClaimedWorkExecution:
        mission_id = _required_string(work_unit, "missionId")
        work_unit_id = _required_string(work_unit, "id")
        if work_unit.get("kind") != self._profile.work_unit_kind:
            raise ClaimedWorkResolutionError(
                f"claimed WorkUnit is not {self._profile.label}"
            )
        if work_unit.get("parentWorkUnitId") is not None:
            raise ClaimedWorkResolutionError(
                f"{self._profile.label} WorkUnit must be a root"
            )
        lease = _required_mapping(work_unit, "lease")
        lease_id = _required_string(lease, "id")

        payload = await self._control.get_execution_context(
            mission_id,
            work_unit_id,
            runner_id=self._runner_id,
            lease_id=lease_id,
        )
        context = _required_mapping(payload, "executionContext")
        prompt, timeout = _compile_model_context(
            context,
            claimed_work_unit=work_unit,
            runner_id=self._runner_id,
            max_context_chars=self._max_context_chars,
            max_timeout_seconds=self._max_timeout_seconds,
            profile=self._profile,
        )
        harness = self._harness_factory.build(context)
        if not callable(getattr(harness, "execute", None)):
            raise ClaimedWorkResolutionError(
                "claimed Harness factory returned an invalid Harness"
            )
        resume_input = None
        checkpoint = context.get("checkpoint")
        if isinstance(checkpoint, Mapping):
            restore = getattr(harness, "restore_resume", None)
            if not callable(restore):
                raise ClaimedWorkResolutionError("claimed Harness cannot restore a complete checkpoint")
            try:
                resume_input = restore(checkpoint, code=prompt, timeout=timeout, language="text")
            except (ValueError, RuntimeError) as exc:
                close = getattr(harness, "close_recovery", None)
                if callable(close):
                    close()
                raise ClaimedWorkResolutionError("checkpoint cannot be safely restored") from exc
        return ClaimedWorkExecution(
            execution_input=RunnerExecutionInput(
                code=prompt,
                language="text",
                timeout=timeout,
                cwd=getattr(harness, "workspace_root", None),
                resume=resume_input,
            ),
            harness=harness,
        )


class A2AInboundClaimedWorkResolver(_ClaimedModelWorkResolver):
    """Compile a bounded inbound prompt from lease-fenced Mission context."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        *,
        runner_id: str,
        harness_factory: ClaimedHarnessFactoryPort,
        max_context_chars: int = 32_768,
        max_timeout_seconds: float = 300.0,
    ) -> None:
        super().__init__(
            control,
            runner_id=runner_id,
            harness_factory=harness_factory,
            profile=_A2A_INBOUND_CONTEXT_PROFILE,
            max_context_chars=max_context_chars,
            max_timeout_seconds=max_timeout_seconds,
        )


class MissionForkClaimedWorkResolver(_ClaimedModelWorkResolver):
    """Resolve a claimed Mission fork without starting its WorkUnit."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        *,
        runner_id: str,
        harness_factory: ClaimedHarnessFactoryPort,
        max_context_chars: int = 32_768,
        max_timeout_seconds: float = 300.0,
    ) -> None:
        super().__init__(
            control,
            runner_id=runner_id,
            harness_factory=harness_factory,
            profile=_MISSION_FORK_CONTEXT_PROFILE,
            max_context_chars=max_context_chars,
            max_timeout_seconds=max_timeout_seconds,
        )


class DesktopTaskClaimedWorkResolver(_ClaimedModelWorkResolver):
    """Resolve one claimed desktop task root into bounded local execution."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        *,
        runner_id: str,
        harness_factory: ClaimedHarnessFactoryPort,
        max_context_chars: int = 32_768,
        max_timeout_seconds: float = 300.0,
    ) -> None:
        super().__init__(
            control,
            runner_id=runner_id,
            harness_factory=harness_factory,
            profile=_DESKTOP_TASK_CONTEXT_PROFILE,
            max_context_chars=max_context_chars,
            max_timeout_seconds=max_timeout_seconds,
        )
