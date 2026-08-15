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
  legacy user-scoped registry. Inbound A2A admission uses the same catalog to
  select one enabled binding that has `a2a.receive` plus every requested
  capability. The Agent ID and adapter are stored on the WorkUnit as an
  execution snapshot; retries do not rebind existing work.
  Catalog mutations use workspace authorization and an atomic expected-version
  compare-and-set; request schemas reject provider credentials and raw config.
  Legacy Registry synchronization reads only Agent ID, adapter type,
  capability tags, and availability status; actor-owned rows take precedence
  over global templates and every synchronized binding uses the same CAS path.
  The Runner uses a replaceable Harness boundary for execution, renews its
  fenced lease, and cancels local work when supervision can no longer prove
  ownership. A bound Runner can atomically claim one ready delegated WorkUnit
  or catalog-bound root `a2a.inbound` WorkUnit through Mission Control. Other
  root kinds remain ineligible. Claim responses are validated against Mission,
  Agent, adapter, lease owner, and attempt before execution. A replaceable
  resolver must turn durable Mission/WorkUnit references into bounded execution
  input, and a missing resolver fails the claimed unit without synthetic
  success. Harness
  owns bounded model/tool loops and explicit per-run tool
  grants resolved from Mission Contract and WorkUnit capabilities;
  ModelAdapterPort normalizes provider responses and reports provider usage;
  Harness enforces per-run token and model-cost budgets and emits request-scoped
  checkpoints; Runner must not write repository state directly. Claiming an
  inbound root only creates a fenced lease. The inbound resolver reads a
  lease-fenced Mission/Contract/WorkUnit projection and compiles a bounded JSON
  prompt with the peer objective marked as untrusted intent. Capability scope,
  criterion configuration, credentials, and Artifact bytes are excluded;
  actual tool grants remain an independent Harness input. Claimed execution
  uses a request-scoped execution plan, so its Harness is built from the exact
  Contract, WorkUnit, and attempt rather than falling back to Runner's fixed
  Sandbox Harness. `a2a.receive` remains an admission marker; other required
  capabilities must resolve to concrete per-attempt bindings or execution fails.
  `runner_worker.py` supervises polling for one explicitly configured Mission.
  It owns only process-local readiness, bounded idle/error backoff, and graceful
  stop. It does not persist a queue, discover Missions, or replace lease state.
  Mission Control now provides a separate workspace-scoped ready-work claim
  that authorizes scope, balances by active/unverified Mission load, and locks
  the selected Mission and WorkUnit atomically. The worker has not yet migrated
  to that endpoint, so fixed-Mission polling remains the deployed behavior.
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
