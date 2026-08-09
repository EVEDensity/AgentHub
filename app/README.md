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
  The Runner uses a replaceable Harness boundary for execution, renews its
  fenced lease, and cancels local work when supervision can no longer prove
  ownership. Harness owns bounded model/tool loops and explicit per-run tool
  grants resolved from Mission Contract and WorkUnit capabilities;
  ModelAdapterPort normalizes provider responses; Runner must not write
  repository state directly.
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
