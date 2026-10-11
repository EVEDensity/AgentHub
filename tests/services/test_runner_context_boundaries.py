"""Controlled context compilation rejects inconsistent domain metadata."""
from __future__ import annotations

import copy

import pytest

from app.services.runner_service import (
    ClaimedWorkResolutionError,
    compile_mission_fork_context,
)
from tests.services.test_runner_service import (
    mission_fork_claim_payload,
    mission_fork_execution_context,
)


@pytest.mark.parametrize("field", ["reference", "externalId"])
@pytest.mark.parametrize("value", ["", "  ", "\t\n"])
def test_fork_ancestry_requires_nonblank_identifiers(field, value):
    context = mission_fork_execution_context()
    context["mission"]["source"][field] = value
    with pytest.raises(ClaimedWorkResolutionError, match=f"valid source.{field}"):
        compile_mission_fork_context(
            context, claimed_work_unit=mission_fork_claim_payload(), runner_id="runner-1",
        )


@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_unrepresentable_model_cost_is_a_classified_context_rejection(value):
    context = mission_fork_execution_context()
    context["contract"]["budgets"]["modelCost"] = value
    with pytest.raises(ClaimedWorkResolutionError, match="valid modelCost"):
        compile_mission_fork_context(
            context, claimed_work_unit=mission_fork_claim_payload(), runner_id="runner-1",
        )


def test_acceptance_criterion_identity_must_be_unique():
    context = mission_fork_execution_context()
    criteria = context["contract"]["acceptanceCriteria"]
    criteria.append(copy.deepcopy(criteria[0]))
    with pytest.raises(ClaimedWorkResolutionError, match="duplicate acceptance criteria"):
        compile_mission_fork_context(
            context, claimed_work_unit=mission_fork_claim_payload(), runner_id="runner-1",
        )


def test_existing_identity_validation_precedes_later_budget_validation():
    context = mission_fork_execution_context()
    context["workUnit"]["assignedAgentId"] = "another-agent"
    context["contract"]["budgets"]["modelCost"] = 10**400
    with pytest.raises(ClaimedWorkResolutionError, match="WorkUnit Agent changed"):
        compile_mission_fork_context(
            context, claimed_work_unit=mission_fork_claim_payload(), runner_id="runner-1",
        )
