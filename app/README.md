# Python Control Plane

`app/` contains the Python application and the Mission domain migration
boundary. It is not the permanent home of every Agent feature.

## Ownership

- `domain/`: immutable Mission, Contract, WorkUnit, Artifact, Evidence, and
  transition models.
- `repositories/`: persistence and transaction boundaries.
- `services/`: application use cases, compatibility adapters, and storage ports.
  Artifact byte verification reads Runner-owned content through this boundary;
  Mission Control retains only immutable Artifact metadata.
  Mission delegation accepts only same-Mission registered ArtifactRefs and
  creates causally linked child WorkUnits behind the parent lease fence.
  Delegated Agent selection goes through a scope-aware binding port; Mission
  state stores only the Agent ID, adapter type, and capability snapshot.
  The default binding resolver reads a workspace-scoped, credential-free
  catalog projection; catalog failures fail closed and never fall back to the
  legacy user-scoped registry.
  Catalog mutations use workspace authorization and an atomic expected-version
  compare-and-set; request schemas reject provider credentials and raw config.
  Legacy Registry synchronization reads only Agent ID, adapter type,
  capability tags, and availability status; actor-owned rows take precedence
  over global templates and every synchronized binding uses the same CAS path.
  The Runner uses a replaceable Harness boundary for execution, renews its
  fenced lease, and cancels local work when supervision can no longer prove
  ownership. A bound Runner can atomically claim one ready delegated WorkUnit
  through Mission Control; claim responses are validated against Mission,
  Agent, adapter, lease owner, and attempt before execution. A replaceable
  resolver must turn WorkUnit ArtifactRefs into bounded execution input, and a
  missing resolver fails the claimed unit without synthetic success. Harness
  owns bounded model/tool loops and explicit per-run tool
  grants resolved from Mission Contract and WorkUnit capabilities;
  ModelAdapterPort normalizes provider responses and reports provider usage;
  Harness enforces per-run token and model-cost budgets and emits request-scoped
  checkpoints; Runner must not write repository state directly.
  `mcp_tool_adapter.py` is a stateless MCP client/tool adapter; it forwards
  Mission/WorkUnit/capability context and emits content-free call audit events.
  `build_mcp_capability_binding` composes it with Contract/WorkUnit capability
  resolution so scope is applied at call time. Evidence admission rechecks
  Artifact ownership against the verified WorkUnit and attempt before and
  after byte verification; expired delegated attempts cannot satisfy a retry.
- `api/`: HTTP/WebSocket transport; handlers must delegate to services.
- `schemas/`: versioned request and response validation.
- `compat/`: one-way adapters from legacy Task/DAG data into Mission objects.
- `core/`, `db/`, `utils/`: shared application infrastructure.

New execution behavior must enter through Mission/WorkUnit services. Do not add
new business state to the legacy LangGraph task state machine.

## Change checklist

1. Update domain transitions and persistence together.
2. Append an event for every durable state transition.
3. Add domain, persistence, and API tests as applicable.
4. Update the nearest contract or ADR when ownership changes.
