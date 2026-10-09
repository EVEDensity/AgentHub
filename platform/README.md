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

`test-run-report.schema.json` and `security-scan-report.schema.json` define
versioned report bodies registered as `test-result` and `report` Artifacts.
Runner or external test/scanner producers must provide exact Mission, WorkUnit,
and attempt identity plus individual case/finding records and consistent
summaries. No built-in producer is implied by the schema. The independent
verifier and Mission Control read the same integrity-verified bytes; raw report
content is not added to discovery, Evidence, or HTTP projections. Existing
free-text reports fail semantic acceptance until producers migrate. See ADR-0114
for failure behavior and the boundary between report verification and trusted
test/scanner execution.
