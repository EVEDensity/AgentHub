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

`opensandbox-execution-report.schema.json` is an additive diagnostic Artifact
format produced by the experimental Native sandbox Harness. Consumers are the
disposable conformance script and integrity-verified Artifact readers; Mission
Control and Verifier do not interpret it as a test verdict or PASS Evidence.
It records actual command identity/exit and sanitized combined output only after
confirmed sandbox destruction. Existing `artifact-set.v1` acceptance remains
byte integrity. Disabling the experimental factory does not rewrite historical
reports; see [ADR-0125](../docs/architecture/decisions/0125-experimental-sandbox-native-composition.md).
No existing report format or database schema is migrated.

`browser-execution-report.schema.json` is an additive diagnostic Artifact from
the experimental anonymous browser-read Native Harness. It binds ordered host
observations to Mission, WorkUnit attempt, Contract revision, operator profile,
runtime digest, pinned Playwright/upstream versions and allowed origins. Each
read retains its bounded worker result, timestamps and cleanup observation;
failed or empty reads and unconfirmed cleanup remain diagnostic states. The
nested worker result matches the strict `BrowserReadResult` DTO. Consumers are
conformance tests and integrity-verified Artifact readers. Neither schema
validation nor page/model text verifies factual accuracy, trusted tests or a
Mission verdict; `acceptance` is always `artifact-integrity-only`. Mission
Control and Verifier retain the existing `artifact-set.v1` byte-integrity
acceptance. Disabling this operator-only composition leaves historical reports
readable and requires no migration of existing report formats or persistence.
