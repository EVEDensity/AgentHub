"""Default local-task acceptance shared by CLI and Chat admission.

This policy proves output availability and byte integrity only. It does not
prove that a program is correct or that any test command actually executed.
Desktop carries the same projection, checked by the cross-language contract
test; tool grants and repository scopes remain empty for every entry point.
"""

from __future__ import annotations

from typing import Any


def build_default_task_contract(
    contract_id: str,
    time_seconds: int = 300,
    *,
    model_cost: float = 1,
    retries: int = 0,
) -> dict[str, Any]:
    """Build fresh protocol fields without granting execution capabilities.

    ``test-result`` is the existing Runner's output kind, including final model
    text. Its kind alone is not evidence that tests ran; artifact-set.v1 checks
    only registered bytes through the independent verifier, including size and
    SHA-256. It imposes no minimum byte size.
    """
    return {
        "id": contract_id,
        "version": 1,
        "repositoryScopes": [],
        "allowedCapabilities": [],
        "budgets": {"timeSeconds": time_seconds, "modelCost": model_cost, "retries": retries},
        "acceptanceCriteria": [{
            "id": "desktop-artifacts",
            "kind": "manual",
            "description": (
                "Output integrity only: artifacts exist and match registered size and SHA-256. "
                "Does not prove code correctness or test execution."
            ),
            "required": True,
            "configuration": {
                "evaluator": "artifact-set.v1",
                "workUnitKinds": ["desktop.task"],
                "minimumArtifacts": 1,
                "requiredArtifactKinds": ["test-result"],
            },
        }],
        "decisionGates": [],
        "forbiddenActions": [],
    }
