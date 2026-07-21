from __future__ import annotations

from app.services.workflow_contract import validate_workflow_contract


def _node(node_id: str, dependencies: list[str] | None = None) -> dict:
    return {
        "id": node_id,
        "type": "agent",
        "agent": "CodeGen",
        "dependencies": dependencies or [],
        "x": 10,
        "y": 20,
    }


def test_dependencies_are_normalized_to_editor_edges() -> None:
    result = validate_workflow_contract([_node("plan"), _node("code", ["plan"])])

    assert result.valid
    assert result.normalized is not None
    assert result.normalized["schemaVersion"] == 1
    assert result.normalized["edges"] == [
        {"id": "plan->code", "from": "plan", "to": "code", "label": "", "condition": ""}
    ]


def test_explicit_edges_are_authoritative_for_runtime_dependencies() -> None:
    result = validate_workflow_contract(
        [_node("plan"), _node("code", ["stale"])],
        [{"id": "edge-1", "from": "plan", "to": "code"}],
    )

    assert result.valid
    assert result.normalized is not None
    code = next(node for node in result.normalized["nodes"] if node["id"] == "code")
    assert code["dependencies"] == ["plan"]


def test_duplicate_nodes_and_edges_return_structured_issues() -> None:
    result = validate_workflow_contract(
        [_node("same"), _node("same")],
        [
            {"id": "duplicate", "from": "same", "to": "same"},
            {"id": "duplicate", "from": "same", "to": "same"},
        ],
    )

    assert not result.valid
    assert {issue.code for issue in result.issues} >= {
        "duplicate_node_id", "duplicate_edge_id", "duplicate_edge", "self_loop"
    }


def test_missing_endpoint_and_cycle_are_rejected() -> None:
    missing = validate_workflow_contract(
        [_node("a")], [{"from": "missing", "to": "a"}],
    )
    cyclic = validate_workflow_contract(
        [_node("a"), _node("b")],
        [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    )

    assert not missing.valid
    assert any(issue.code == "missing_edge_source" for issue in missing.issues)
    assert not cyclic.valid
    assert any(issue.code == "cycle_detected" for issue in cyclic.issues)


def test_unassigned_agent_is_a_recoverable_warning() -> None:
    result = validate_workflow_contract([{"id": "draft-agent", "type": "agent"}])

    assert result.valid
    assert any(issue.code == "agent_unassigned" and issue.severity == "warning" for issue in result.issues)


def test_empty_workflow_and_incomplete_runtime_config_are_rejected() -> None:
    empty = validate_workflow_contract([])
    incomplete = validate_workflow_contract([{"id": "request", "type": "http"}])

    assert not empty.valid
    assert any(issue.code == "empty_workflow" for issue in empty.issues)
    assert not incomplete.valid
    assert any(issue.code == "missing_node_config" for issue in incomplete.issues)
