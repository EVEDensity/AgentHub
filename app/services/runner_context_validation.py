"""Pure claim, identity and budget checks for lease-fenced model context."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.runner_context_policy import supports_model_source
from app.services.runner_protocols import ClaimedWorkResolutionError


def _required_mapping(
    value: Mapping[str, Any],
    key: str,
) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise ClaimedWorkResolutionError(f"execution context has no valid {key}")
    return result


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ClaimedWorkResolutionError(f"execution context has no valid {key}")
    return result


def _required_sequence(
    value: Mapping[str, Any],
    key: str,
) -> Sequence[Any]:
    result = value.get(key)
    if isinstance(result, (str, bytes, bytearray)) or not isinstance(
        result, Sequence
    ):
        raise ClaimedWorkResolutionError(f"execution context has no valid {key}")
    return result


def _required_non_negative_int(value: Mapping[str, Any], key: str) -> int:
    result = value.get(key)
    if type(result) is not int or result < 0:
        raise ClaimedWorkResolutionError(f"execution context has no valid {key}")
    return result


def _optional_string(value: Mapping[str, Any], key: str) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str):
        raise ClaimedWorkResolutionError(f"execution context has no valid {key}")
    return result


def _string_list(value: Mapping[str, Any], key: str) -> list[str]:
    result = [_sequence_string(item, key) for item in _required_sequence(value, key)]
    if len(result) != len(set(result)):
        raise ClaimedWorkResolutionError(f"execution context has duplicate {key}")
    return result


def _sequence_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClaimedWorkResolutionError(
            f"execution context has a non-string {field} entry"
        )
    return value


@dataclass(frozen=True, slots=True)
class _ModelContextProfile:
    source_types: tuple[str, ...]
    work_unit_kind: str
    label: str
    schema: str
    required_capability: str | None = None
    require_ancestry: bool = False
    require_input_refs: bool = False


_A2A_INBOUND_CONTEXT_PROFILE = _ModelContextProfile(
    source_types=("a2a.inbound",),
    work_unit_kind="a2a.inbound",
    label="inbound A2A",
    schema="agenthub.a2a-inbound-context.v1",
    required_capability="a2a.receive",
)
_MISSION_FORK_CONTEXT_PROFILE = _ModelContextProfile(
    source_types=("mission.fork",),
    work_unit_kind="mission.fork",
    label="Mission fork",
    schema="agenthub.mission-fork-context.v1",
    require_ancestry=True,
    require_input_refs=True,
)
_DESKTOP_TASK_CONTEXT_PROFILE = _ModelContextProfile(
    source_types=("manual", "chat"),
    work_unit_kind="desktop.task",
    label="desktop task",
    schema="agenthub.desktop-task-context.v1",
)


def _validate_claim(claimed_work_unit, runner_id, profile):
    claimed_mission_id = _required_string(claimed_work_unit, "missionId")
    claimed_work_unit_id = _required_string(claimed_work_unit, "id")
    if _required_string(claimed_work_unit, "kind") != profile.work_unit_kind:
        raise ClaimedWorkResolutionError(
            f"claimed WorkUnit is not {profile.label}"
        )
    if claimed_work_unit.get("parentWorkUnitId") is not None:
        raise ClaimedWorkResolutionError(
            f"{profile.label} WorkUnit must be a root"
        )
    claimed_agent_id = _required_string(claimed_work_unit, "assignedAgentId")
    claimed_adapter = _required_string(claimed_work_unit, "assignedAdapter")
    if profile.require_ancestry and claimed_adapter == "a2a.outbound":
        raise ClaimedWorkResolutionError(
            "Mission fork cannot use the outbound A2A adapter"
        )
    claimed_attempt = _required_non_negative_int(claimed_work_unit, "attempt")
    if claimed_attempt < 1:
        raise ClaimedWorkResolutionError("claimed WorkUnit has no active attempt")
    claimed_status = _required_string(claimed_work_unit, "status")
    if claimed_status not in {"LEASED", "RUNNING"}:
        raise ClaimedWorkResolutionError("claimed WorkUnit is not actively leased")
    claimed_lease = _required_mapping(claimed_work_unit, "lease")
    claimed_lease_id = _required_string(claimed_lease, "id")
    if _required_string(claimed_lease, "runnerId") != runner_id:
        raise ClaimedWorkResolutionError("claimed WorkUnit belongs to another runner")
    return (claimed_mission_id, claimed_work_unit_id, claimed_agent_id, claimed_adapter, claimed_attempt, claimed_status, claimed_lease_id)


def _validate_mission(context, claimed_mission_id, claimed_adapter, profile):
    mission = _required_mapping(context, "mission")
    mission_id = _required_string(mission, "id")
    if mission_id != claimed_mission_id:
        raise ClaimedWorkResolutionError("execution context Mission does not match claim")
    if _required_string(mission, "status") != "RUNNING":
        raise ClaimedWorkResolutionError("execution context Mission is not RUNNING")
    objective = _required_string(mission, "objective")
    contract_id = _required_string(mission, "contractId")
    mission_contract_version = _required_non_negative_int(
        mission,
        "contractVersion",
    )
    if mission_contract_version < 1:
        raise ClaimedWorkResolutionError("execution context Mission has no Contract version")
    source = _required_mapping(mission, "source")
    if not supports_model_source(_required_string(source, "type"), profile.source_types, claimed_adapter):
        raise ClaimedWorkResolutionError(
            f"execution context source is not {profile.label}"
        )
    return (mission_id, objective, contract_id, mission_contract_version, source)


def _validate_work_unit(context, claimed_work_unit_id, mission_id, claimed_agent_id, claimed_adapter, claimed_status, claimed_attempt, claimed_lease_id, runner_id, profile):
    work_unit = _required_mapping(context, "workUnit")
    if _required_string(work_unit, "id") != claimed_work_unit_id:
        raise ClaimedWorkResolutionError("execution context WorkUnit does not match claim")
    if _required_string(work_unit, "missionId") != mission_id:
        raise ClaimedWorkResolutionError("execution context WorkUnit has another Mission")
    if work_unit.get("parentWorkUnitId") is not None:
        raise ClaimedWorkResolutionError(
            f"{profile.label} WorkUnit must be a root"
        )
    if _required_string(work_unit, "kind") != profile.work_unit_kind:
        raise ClaimedWorkResolutionError(
            f"execution context WorkUnit is not {profile.label}"
        )
    if _required_string(work_unit, "assignedAgentId") != claimed_agent_id:
        raise ClaimedWorkResolutionError("execution context WorkUnit Agent changed")
    context_adapter = _required_string(work_unit, "assignedAdapter")
    if context_adapter != claimed_adapter:
        raise ClaimedWorkResolutionError("execution context WorkUnit adapter changed")
    if profile.require_ancestry and context_adapter == "a2a.outbound":
        raise ClaimedWorkResolutionError(
            "Mission fork cannot use the outbound A2A adapter"
        )
    if _required_string(work_unit, "status") != claimed_status:
        raise ClaimedWorkResolutionError("execution context WorkUnit status changed")
    if _required_non_negative_int(work_unit, "attempt") != claimed_attempt:
        raise ClaimedWorkResolutionError("execution context WorkUnit attempt changed")
    lease = _required_mapping(work_unit, "lease")
    if _required_string(lease, "id") != claimed_lease_id:
        raise ClaimedWorkResolutionError("execution context WorkUnit lease changed")
    if _required_string(lease, "runnerId") != runner_id:
        raise ClaimedWorkResolutionError("execution context lease belongs to another runner")
    return work_unit


def _validate_contract(context, contract_id, mission_contract_version):
    contract = _required_mapping(context, "contract")
    if _required_string(contract, "id") != contract_id:
        raise ClaimedWorkResolutionError("execution context Contract does not match Mission")
    contract_version = _required_non_negative_int(contract, "version")
    if contract_version < 1:
        raise ClaimedWorkResolutionError("execution context Contract has no version")
    if contract_version != mission_contract_version:
        raise ClaimedWorkResolutionError(
            "execution context Contract version does not match Mission"
        )
    return (contract, contract_version)


def _validate_budgets(contract):
    budgets = _required_mapping(contract, "budgets")
    time_seconds = _required_non_negative_int(budgets, "timeSeconds")
    if time_seconds < 1:
        raise ClaimedWorkResolutionError("execution context has no positive time budget")
    retries = _required_non_negative_int(budgets, "retries")
    model_cost = budgets.get("modelCost")
    if isinstance(model_cost, bool) or not isinstance(model_cost, (int, float)):
        raise ClaimedWorkResolutionError("execution context has no valid modelCost")
    try:
        invalid_cost = not math.isfinite(float(model_cost)) or model_cost < 0
    except (OverflowError, ValueError) as exc:
        raise ClaimedWorkResolutionError("execution context has no valid modelCost") from exc
    if invalid_cost:
        raise ClaimedWorkResolutionError("execution context has no valid modelCost")
    return (time_seconds, retries, model_cost)
