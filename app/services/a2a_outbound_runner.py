from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from app.domain import (
    Mission,
    MissionContract,
    MissionSourceType,
    MissionStatus,
    WorkUnit,
    WorkUnitStatus,
)
from app.services.runner_service import (
    ClaimedWorkResolutionError,
    MissionControlRunnerPort,
)

A2A_OUTBOUND_ADAPTER = "a2a.outbound"
A2A_SEND_CAPABILITY = "a2a.send"
_MAX_CAPABILITY_COUNT = 64


class A2ARemoteTaskState(str, Enum):
    """Bounded remote states understood by outbound supervision."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in {
            A2ARemoteTaskState.COMPLETED,
            A2ARemoteTaskState.FAILED,
            A2ARemoteTaskState.CANCELED,
        }


@dataclass(frozen=True, slots=True)
class A2ARemoteTaskReference:
    """Credential-free routing identity for one remote A2A task."""

    target_agent_url: str
    source_agent_url: str
    workspace_id: str
    task_id: str

    def __post_init__(self) -> None:
        _validate_http_url(self.target_agent_url, "target_agent_url")
        normalized_source = _normalize_http_origin(
            self.source_agent_url,
            "source_agent_url",
        )
        object.__setattr__(self, "source_agent_url", normalized_source)
        _validate_text(self.workspace_id, "workspace_id", max_length=255)
        _validate_text(self.task_id, "task_id", max_length=255)


@dataclass(frozen=True, slots=True)
class A2AOutboundTaskCommand:
    """Bounded, credential-free input to a stateless A2A send call."""

    reference: A2ARemoteTaskReference
    objective: str
    required_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.reference, A2ARemoteTaskReference):
            raise TypeError("reference must be an A2ARemoteTaskReference")
        _validate_text(self.objective, "objective", max_length=10_000)
        if not isinstance(self.required_capabilities, tuple):
            raise TypeError("required_capabilities must be a tuple")
        if len(self.required_capabilities) > _MAX_CAPABILITY_COUNT:
            raise ValueError("required_capabilities exceeds the bounded count")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("required_capabilities must be unique")
        for capability in self.required_capabilities:
            _validate_text(capability, "required_capability", max_length=255)
        if A2A_SEND_CAPABILITY in self.required_capabilities:
            raise ValueError(
                "a2a.send is local transport authority, not a peer capability"
            )

    def to_send_params(self) -> dict[str, Any]:
        """Build the protocol params without credentials or local lease metadata."""
        return {
            "id": self.reference.task_id,
            "workspaceId": self.reference.workspace_id,
            "sourceAgentUrl": self.reference.source_agent_url,
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": self.objective}],
            },
            "requiredCapabilities": list(self.required_capabilities),
        }


@dataclass(frozen=True, slots=True)
class A2ARemoteTaskSnapshot:
    """Content-free remote lifecycle projection used before result import."""

    task_id: str
    state: A2ARemoteTaskState
    status_message: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.task_id, "task_id", max_length=255)
        if not isinstance(self.state, A2ARemoteTaskState):
            raise TypeError("state must be a supported A2A remote task state")
        if self.status_message is not None:
            _validate_text(
                self.status_message,
                "status_message",
                max_length=2_000,
            )


class A2AOutboundTransportPort(Protocol):
    """Stateless A2A transport; implementations retain no task lifecycle truth."""

    async def send(self, command: A2AOutboundTaskCommand) -> A2ARemoteTaskSnapshot: ...

    async def get(
        self,
        reference: A2ARemoteTaskReference,
    ) -> A2ARemoteTaskSnapshot: ...

    async def get_result(
        self,
        reference: A2ARemoteTaskReference,
    ) -> Mapping[str, Any]: ...

    async def cancel(
        self,
        reference: A2ARemoteTaskReference,
    ) -> A2ARemoteTaskSnapshot: ...


@dataclass(frozen=True, slots=True)
class A2AOutboundClaimedWork:
    """Lease-fenced local identity plus the only command allowed for this attempt."""

    mission_id: str
    work_unit_id: str
    attempt: int
    lease_id: str
    timeout_seconds: float
    command: A2AOutboundTaskCommand

    def __post_init__(self) -> None:
        _validate_text(self.mission_id, "mission_id", max_length=255)
        _validate_text(self.work_unit_id, "work_unit_id", max_length=255)
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        _validate_text(self.lease_id, "lease_id", max_length=255)
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive and finite")
        if not isinstance(self.command, A2AOutboundTaskCommand):
            raise TypeError("command must be an A2AOutboundTaskCommand")


class A2AOutboundClaimedWorkResolver:
    """Resolve a claimed outbound root without invoking a Harness or transport."""

    def __init__(
        self,
        control: MissionControlRunnerPort,
        *,
        runner_id: str,
        source_agent_url: str,
        max_request_bytes: int = 32_768,
        max_timeout_seconds: float = 3_600.0,
    ) -> None:
        _validate_text(runner_id, "runner_id", max_length=255)
        if max_request_bytes < 1:
            raise ValueError("max_request_bytes must be positive")
        if not math.isfinite(max_timeout_seconds) or max_timeout_seconds <= 0:
            raise ValueError("max_timeout_seconds must be positive and finite")
        self._control = control
        self._runner_id = runner_id
        self._source_agent_url = _normalize_http_origin(
            source_agent_url,
            "source_agent_url",
        )
        self._max_request_bytes = max_request_bytes
        self._max_timeout_seconds = max_timeout_seconds

    async def resolve(
        self,
        claimed_work_unit: Mapping[str, Any],
    ) -> A2AOutboundClaimedWork:
        claim = parse_a2a_outbound_claim(
            claimed_work_unit,
            runner_id=self._runner_id,
        )
        payload = await self._control.get_execution_context(
            claim.mission_id,
            claim.work_unit_id,
            runner_id=self._runner_id,
            lease_id=claim.lease_id,
        )
        context = _required_mapping(payload, "executionContext")
        if type(context.get("version")) is not int or context["version"] != 1:
            raise ClaimedWorkResolutionError("unsupported execution context version")
        mission, contract, work_unit = _parse_domain_context(context)
        _validate_context_identity(
            claim,
            mission=mission,
            contract=contract,
            work_unit=work_unit,
        )

        target_agent_url = mission.source.reference
        task_id = mission.source.external_id
        if target_agent_url is None or task_id is None:
            raise ClaimedWorkResolutionError(
                "outbound Mission has incomplete remote routing identity"
            )
        try:
            reference = A2ARemoteTaskReference(
                target_agent_url=target_agent_url,
                source_agent_url=self._source_agent_url,
                workspace_id=mission.workspace_id,
                task_id=task_id,
            )
            command = A2AOutboundTaskCommand(
                reference=reference,
                objective=mission.objective,
                required_capabilities=tuple(
                    capability
                    for capability in work_unit.required_capabilities
                    if capability != A2A_SEND_CAPABILITY
                ),
            )
        except ValueError as exc:
            raise ClaimedWorkResolutionError(
                "outbound A2A command failed bounded validation"
            ) from exc
        encoded_params = json.dumps(
            command.to_send_params(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded_params) > self._max_request_bytes:
            raise ClaimedWorkResolutionError("outbound A2A command exceeds size limit")

        return A2AOutboundClaimedWork(
            mission_id=mission.id,
            work_unit_id=work_unit.id,
            attempt=work_unit.attempt,
            lease_id=claim.lease_id,
            timeout_seconds=min(
                float(contract.budgets.time_seconds),
                self._max_timeout_seconds,
            ),
            command=command,
        )


@dataclass(frozen=True, slots=True)
class A2AOutboundClaimIdentity:
    """Validated lease fence extracted from one claimed outbound WorkUnit."""

    mission_id: str
    work_unit_id: str
    attempt: int
    lease_id: str
    status: WorkUnitStatus
    runner_id: str
    assigned_agent_id: str
    required_capabilities: tuple[str, ...]


def parse_a2a_outbound_claim(
    value: Mapping[str, Any],
    *,
    runner_id: str,
) -> A2AOutboundClaimIdentity:
    """Parse only the identity needed to fence resolution and failure recovery."""

    if not isinstance(value, Mapping):
        raise TypeError("value must be a Mapping")
    _validate_text(runner_id, "runner_id", max_length=255)
    mission_id = _required_string(value, "missionId")
    work_unit_id = _required_string(value, "id")
    if value.get("kind") != "a2a.delegate" or value.get("parentWorkUnitId") is not None:
        raise ClaimedWorkResolutionError("claimed WorkUnit is not an outbound A2A root")
    if value.get("assignedAdapter") != A2A_OUTBOUND_ADAPTER:
        raise ClaimedWorkResolutionError(
            "claimed WorkUnit is not bound to a2a.outbound"
        )
    assigned_agent_id = _required_string(value, "assignedAgentId")
    try:
        status = WorkUnitStatus(_required_string(value, "status"))
    except ValueError as exc:
        raise ClaimedWorkResolutionError(
            "claimed WorkUnit has an invalid status"
        ) from exc
    if status not in {WorkUnitStatus.LEASED, WorkUnitStatus.RUNNING}:
        raise ClaimedWorkResolutionError("claimed WorkUnit is not actively leased")
    attempt = value.get("attempt")
    if type(attempt) is not int or attempt < 1:
        raise ClaimedWorkResolutionError("claimed WorkUnit has no active attempt")
    lease = _required_mapping(value, "lease")
    lease_id = _required_string(lease, "id")
    if _required_string(lease, "runnerId") != runner_id:
        raise ClaimedWorkResolutionError("claimed WorkUnit belongs to another runner")
    required_capabilities = _required_string_sequence(value, "requiredCapabilities")
    if A2A_SEND_CAPABILITY not in required_capabilities:
        raise ClaimedWorkResolutionError("claimed WorkUnit lacks a2a.send")
    return A2AOutboundClaimIdentity(
        mission_id=mission_id,
        work_unit_id=work_unit_id,
        attempt=attempt,
        lease_id=lease_id,
        status=status,
        runner_id=runner_id,
        assigned_agent_id=assigned_agent_id,
        required_capabilities=required_capabilities,
    )


def _parse_domain_context(
    context: Mapping[str, Any],
) -> tuple[Mission, MissionContract, WorkUnit]:
    try:
        mission = Mission.model_validate(_required_mapping(context, "mission"))
        contract = MissionContract.model_validate(
            _required_mapping(context, "contract")
        )
        work_unit = WorkUnit.model_validate(_required_mapping(context, "workUnit"))
    except (TypeError, ValidationError) as exc:
        raise ClaimedWorkResolutionError(
            "claimed execution context failed domain validation"
        ) from exc
    return mission, contract, work_unit


def _validate_context_identity(
    claim: A2AOutboundClaimIdentity,
    *,
    mission: Mission,
    contract: MissionContract,
    work_unit: WorkUnit,
) -> None:
    if mission.id != claim.mission_id or work_unit.id != claim.work_unit_id:
        raise ClaimedWorkResolutionError("execution context does not match the claim")
    if work_unit.mission_id != mission.id:
        raise ClaimedWorkResolutionError(
            "execution context WorkUnit has another Mission"
        )
    if mission.status != MissionStatus.RUNNING:
        raise ClaimedWorkResolutionError("execution context Mission is not RUNNING")
    if mission.source.type != MissionSourceType.A2A:
        raise ClaimedWorkResolutionError("execution context source is not outbound A2A")
    if contract.id != mission.contract_id:
        raise ClaimedWorkResolutionError(
            "execution context Contract does not match Mission"
        )
    if work_unit.parent_work_unit_id is not None or work_unit.kind != "a2a.delegate":
        raise ClaimedWorkResolutionError(
            "execution context WorkUnit is not an outbound root"
        )
    if work_unit.assigned_adapter != A2A_OUTBOUND_ADAPTER:
        raise ClaimedWorkResolutionError("execution context WorkUnit adapter changed")
    if work_unit.assigned_agent_id != claim.assigned_agent_id:
        raise ClaimedWorkResolutionError("execution context WorkUnit Agent changed")
    if work_unit.status != claim.status or work_unit.attempt != claim.attempt:
        raise ClaimedWorkResolutionError("execution context WorkUnit attempt changed")
    if work_unit.lease is None or work_unit.lease.id != claim.lease_id:
        raise ClaimedWorkResolutionError("execution context WorkUnit lease changed")
    if work_unit.lease.runner_id != claim.runner_id:
        raise ClaimedWorkResolutionError(
            "execution context lease belongs to another runner"
        )
    if work_unit.required_capabilities != claim.required_capabilities:
        raise ClaimedWorkResolutionError(
            "execution context WorkUnit capabilities changed"
        )
    if A2A_SEND_CAPABILITY not in work_unit.required_capabilities:
        raise ClaimedWorkResolutionError("outbound WorkUnit lacks a2a.send")
    if work_unit.input_refs:
        raise ClaimedWorkResolutionError(
            "outbound Artifact inputs are not supported by the current transport contract"
        )
    if tuple(
        (output.kind, output.required) for output in work_unit.expected_outputs
    ) != (("a2a.result", True),):
        raise ClaimedWorkResolutionError(
            "outbound WorkUnit has an unsupported output contract"
        )

    grants = {grant.capability: grant for grant in contract.allowed_capabilities}
    if len(grants) != len(contract.allowed_capabilities):
        raise ClaimedWorkResolutionError(
            "execution context has duplicate capability grants"
        )
    if not set(work_unit.required_capabilities).issubset(grants):
        raise ClaimedWorkResolutionError(
            "WorkUnit capabilities exceed the Mission Contract"
        )
    target_agent_url = mission.source.reference
    if target_agent_url is None:
        raise ClaimedWorkResolutionError("outbound Mission has no target Agent URL")
    for capability in work_unit.required_capabilities:
        if grants[capability].scope.get("agentUrl") != target_agent_url:
            raise ClaimedWorkResolutionError(
                "outbound capability scope does not match the target Agent"
            )


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise ClaimedWorkResolutionError(f"execution context has no valid {key}")
    return result


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ClaimedWorkResolutionError(f"execution context has no valid {key}")
    return result


def _required_string_sequence(
    value: Mapping[str, Any],
    key: str,
) -> tuple[str, ...]:
    result = value.get(key)
    if isinstance(result, (str, bytes, bytearray)) or not isinstance(result, Sequence):
        raise ClaimedWorkResolutionError(f"execution context has no valid {key}")
    values = tuple(result)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ClaimedWorkResolutionError(f"execution context has invalid {key} entries")
    if len(values) != len(set(values)):
        raise ClaimedWorkResolutionError(f"execution context has duplicate {key}")
    return values


def _validate_text(value: str, field: str, *, max_length: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{field} must contain 1 to {max_length} characters")


def _validate_http_url(value: str, field: str) -> None:
    _validate_text(value, field, max_length=2_048)
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} has an invalid port") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ValueError(f"{field} must be an absolute credential-free HTTP(S) URL")


def _normalize_http_origin(value: str, field: str) -> str:
    _validate_http_url(value, field)
    parsed = urlsplit(value)
    if parsed.path not in {"", "/"} or parsed.query:
        raise ValueError(f"{field} must contain only an HTTP(S) origin")
    host = parsed.hostname
    assert host is not None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host.lower()
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


__all__ = [
    "A2AOutboundClaimIdentity",
    "A2AOutboundClaimedWork",
    "A2AOutboundClaimedWorkResolver",
    "A2AOutboundTaskCommand",
    "A2AOutboundTransportPort",
    "A2ARemoteTaskReference",
    "A2ARemoteTaskSnapshot",
    "A2ARemoteTaskState",
    "parse_a2a_outbound_claim",
]
