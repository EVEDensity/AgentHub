from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from app.schemas.dag import DAGConfig, DAGEdge, DAGNode
from app.schemas.workflow import DAGValidationIssue, DAGValidationResult


MAX_WORKFLOW_NODES = 200
MAX_WORKFLOW_EDGES = 400


def validate_workflow_contract(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    *,
    schema_version: int = 1,
) -> DAGValidationResult:
    """Validate and normalize editor data without touching persistence."""
    issues: list[DAGValidationIssue] = []
    raw_edges = edges or []
    if len(nodes) > MAX_WORKFLOW_NODES:
        issues.append(_issue("node_limit", f"Workflow exceeds {MAX_WORKFLOW_NODES} nodes"))
    if len(raw_edges) > MAX_WORKFLOW_EDGES:
        issues.append(_issue("edge_limit", f"Workflow exceeds {MAX_WORKFLOW_EDGES} edges"))

    parsed_nodes: list[DAGNode] = []
    for index, raw in enumerate(nodes[: MAX_WORKFLOW_NODES + 1]):
        try:
            node = DAGNode.model_validate(raw)
        except ValidationError as exc:
            issues.append(_issue("invalid_node", f"Node {index}: {exc.errors()[0]['msg']}"))
            continue
        node.id = node.id.strip()
        if not node.id:
            issues.append(_issue("empty_node_id", "Node ID cannot be empty", node_id=node.id))
        parsed_nodes.append(node)

    if not parsed_nodes:
        issues.append(_issue("empty_workflow", "Workflow must contain at least one node"))

    id_counts = Counter(node.id for node in parsed_nodes if node.id)
    for node_id, count in id_counts.items():
        if count > 1:
            issues.append(_issue("duplicate_node_id", f"Duplicate node ID: {node_id}", node_id=node_id))
    node_ids = set(id_counts)

    parsed_edges: list[DAGEdge] = []
    if raw_edges:
        for index, raw in enumerate(raw_edges[: MAX_WORKFLOW_EDGES + 1]):
            try:
                edge = DAGEdge.model_validate(raw)
            except ValidationError as exc:
                issues.append(_issue("invalid_edge", f"Edge {index}: {exc.errors()[0]['msg']}"))
                continue
            edge.source = edge.source.strip()
            edge.target = edge.target.strip()
            edge.id = edge.id.strip() or f"{edge.source}->{edge.target}"
            parsed_edges.append(edge)
    else:
        for node in parsed_nodes:
            for dependency in node.dependencies:
                parsed_edges.append(
                    DAGEdge(id=f"{dependency}->{node.id}", **{"from": dependency, "to": node.id})
                )

    edge_id_counts = Counter(edge.id for edge in parsed_edges)
    pair_counts = Counter((edge.source, edge.target) for edge in parsed_edges)
    for edge in parsed_edges:
        if edge_id_counts[edge.id] > 1:
            issues.append(_issue("duplicate_edge_id", f"Duplicate edge ID: {edge.id}", edge_id=edge.id))
        if pair_counts[(edge.source, edge.target)] > 1:
            issues.append(_issue("duplicate_edge", f"Duplicate edge: {edge.source} -> {edge.target}", edge_id=edge.id))
        if edge.source == edge.target:
            issues.append(_issue("self_loop", "An edge cannot connect a node to itself", edge_id=edge.id))
        if edge.source not in node_ids:
            issues.append(_issue("missing_edge_source", f"Edge source does not exist: {edge.source}", edge_id=edge.id))
        if edge.target not in node_ids:
            issues.append(_issue("missing_edge_target", f"Edge target does not exist: {edge.target}", edge_id=edge.id))

    incoming: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in parsed_edges:
        if edge.source in node_ids and edge.target in node_ids and edge.source != edge.target:
            incoming[edge.target].append(edge.source)
    if raw_edges:
        for node in parsed_nodes:
            node.dependencies = list(dict.fromkeys(incoming.get(node.id, [])))
    else:
        for node in parsed_nodes:
            for dependency in node.dependencies:
                if dependency not in node_ids:
                    issues.append(_issue("missing_dependency", f"Dependency does not exist: {dependency}", node_id=node.id))

    _validate_cycles(incoming, issues)
    start_nodes = [node for node in parsed_nodes if node.type == "start"]
    if len(start_nodes) > 1:
        issues.append(_issue("multiple_start_nodes", "A workflow can contain at most one start node"))
    if parsed_nodes and not any(node.type in {"agent", "tool", "code", "http", "knowledge", "human", "end"} for node in parsed_nodes):
        issues.append(_issue("no_executable_node", "Workflow has no executable or end node"))
    for node in parsed_nodes:
        if node.type == "agent" and not (node.agent or node.domain):
            issues.append(_issue("agent_unassigned", "Agent node has no assigned agent", "warning", node_id=node.id))
        config = node.config or (node.model_extra or {}).get(f"{node.type}Config", {})
        required_field = {
            "code": "code",
            "http": "url",
            "knowledge": "collectionId",
            "human": "prompt",
        }.get(node.type)
        if required_field and not str(config.get(required_field, "")).strip():
            issues.append(
                _issue(
                    "missing_node_config",
                    f"{node.type} node requires {required_field}",
                    node_id=node.id,
                )
            )

    has_errors = any(issue.severity == "error" for issue in issues)
    normalized = None
    if not has_errors:
        dag = DAGConfig(
            schema_version=schema_version,
            version=1,
            total=len(parsed_nodes),
            nodes=parsed_nodes,
            edges=parsed_edges,
        )
        normalized = dag.model_dump(mode="json", by_alias=True)
    return DAGValidationResult(valid=not has_errors, normalized=normalized, issues=_dedupe_issues(issues))


def require_valid_workflow(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None, *, schema_version: int = 1,
) -> dict[str, Any]:
    result = validate_workflow_contract(nodes, edges, schema_version=schema_version)
    if not result.valid or result.normalized is None:
        messages = "; ".join(issue.message for issue in result.issues if issue.severity == "error")
        raise ValueError(messages or "Invalid workflow")
    return result.normalized


def _validate_cycles(incoming: dict[str, list[str]], issues: list[DAGValidationIssue]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        cyclic = any(visit(dependency) for dependency in incoming.get(node_id, []))
        visiting.remove(node_id)
        visited.add(node_id)
        return cyclic

    if any(visit(node_id) for node_id in incoming):
        issues.append(_issue("cycle_detected", "Workflow contains a dependency cycle"))


def _issue(
    code: str,
    message: str,
    severity: str = "error",
    *,
    node_id: str | None = None,
    edge_id: str | None = None,
) -> DAGValidationIssue:
    return DAGValidationIssue(
        code=code, message=message, severity=severity, nodeId=node_id, edgeId=edge_id,
    )


def _dedupe_issues(issues: list[DAGValidationIssue]) -> list[DAGValidationIssue]:
    seen: set[tuple[str, str | None, str | None]] = set()
    result: list[DAGValidationIssue] = []
    for issue in issues:
        key = (issue.code, issue.nodeId, issue.edgeId)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result
