"""The additive target is bounded and never supplies lease ownership."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.schemas.mission import WorkspaceWorkUnitClaimRequest


SCHEMA = json.loads((Path(__file__).parents[2] / "platform/contracts/v1/workspace-work-unit-claim-request.schema.json").read_text())
BASE = {"workspaceId": "workspace", "agentId": "executor", "adapterType": "function-calling",
        "supportedWorkUnitKinds": ["desktop.task"]}


@pytest.mark.parametrize("target", [None, "mission-1"])
def test_legacy_and_targeted_claims_match_public_schema(target):
    body = dict(BASE)
    if target is not None:
        body["resumeMissionId"] = target
    Draft202012Validator(SCHEMA).validate(body)
    request = WorkspaceWorkUnitClaimRequest.model_validate(body)
    assert request.resume_mission_id == target


@pytest.mark.parametrize("target", [None, "", " ", " mission", "mission ", "x" * 256, 1])
def test_invalid_target_is_rejected_by_schema_and_http_model(target):
    body = dict(BASE, resumeMissionId=target)
    assert list(Draft202012Validator(SCHEMA).iter_errors(body))
    with pytest.raises(ValidationError):
        WorkspaceWorkUnitClaimRequest.model_validate(body)
