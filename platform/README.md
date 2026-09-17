# Platform Contracts

`platform/` contains cross-process contracts, capability declarations, and
deployment-neutral platform metadata. It is not a dumping ground for service
implementation details.

Contract changes require:

- an explicit version or backward-compatibility statement;
- producer and consumer inventory;
- migration and rollback behavior;
- contract tests;
- an ADR when ownership or semantics change.

Mission and WorkUnit semantics are defined by the domain and API contracts.
A2A and MCP schemas describe integration boundaries and must map to those
objects without duplicating their lifecycle.

`decision.schema.json` is the durable human-governance projection. It binds a
Mission and WorkUnit attempt to immutable context, offered resolutions, and an
optimistically versioned lifecycle. Protocol adapters and verifiers may expose
or trigger a server-owned Decision but cannot resolve it or translate it into
PASS Evidence. EXPIRED is a distinct fail-closed terminal status with service
closure metadata; it is not a human resolution or cancellation.
