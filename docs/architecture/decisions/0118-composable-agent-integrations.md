# ADR-0118: Compose external capabilities behind Mission-owned execution

> Status: accepted\
> Owner: architecture and platform maintainers\
> Date: 2026-10-17\
> Last reviewed: 2026-10-17\
> Scope: external executors, execution backends, tools, verification, and trigger adapters

## Context

AgentHub already owns Mission/Contract/WorkUnit lifecycle, transactional state,
leases, Artifact metadata, Evidence and Decisions. Mature external components
can supply execution, browser automation, parsing and infrastructure. Importing
their complete platforms would duplicate task state, authorization, budgets,
verification and recovery semantics.

Current entry-point acceptance and failure-honesty gaps must be corrected before
component adoption expands public capability claims. The accepted delivery plan
is [Composable Agent Platform Plan](../../roadmaps/composable-agent-platform-plan.md).

## Decision

Adopt the following boundaries; runtime changes require tested implementation slices:

1. Mission Control remains the only durable business lifecycle authority.
   External task/session/checkpoint state belongs only to its bound WorkUnit
   attempt and cannot directly complete a Mission.
2. Preserve Native Harness. Prefer existing ModelPort, FunctionTool, Runner and
   checkpoint interfaces. Add versioned contracts only for genuinely new
   semantics and maintain producer/consumer compatibility.
3. A full external agent loop is an Executor for one WorkUnit. A tool is a
   bounded operation without hidden model loops. A Skill is instructions and
   resources, never an authorization grant.
4. Runner owns ExecutionBackend lifecycle, resource policy, cancellation and
   cleanup. A backend sandbox status is not a WorkUnit status. Required
   isolation failing must reject execution, rather than degrade silently.
5. Executor completion means results await verification. Trusted independent
   report producers bind actual checks to immutable code snapshots and Artifact
   bytes. In the first implementation, the report producer runs while the
   WorkUnit is RUNNING, returns independently attributable results to Runner,
   and Runner registers all reports and the final manifest under its live lease
   before completing into VERIFYING. Verifier never borrows that execution lease
   or registers late reports. Async verification artifacts require a separate
   admission contract. Mission Control retains transactional Evidence admission. Manual
   acceptance must have explicit authorized semantics, not synthetic test PASS.
6. Permission is the intersection of workspace ACL, immutable Contract scope
   and executor capabilities. Provider/service credentials never become public
   manifests or model context. Third-party untrusted hooks do not run inside
   Mission Control.
7. Mission budgets cover all child WorkUnits, model calls, retries, review and
   verification through reservation and idempotent usage settlement. Unknown
   cost is not zero. One optional supplier gateway is selected per deployment;
   task-level model policy remains separate from supplier transport.
8. Schedule/webhook adapters admit ordinary Missions using durable deduplication
   and authorization. They do not run separate business state machines.
9. External recovery support is declared honestly. Existing private v2 resume
   guarantees do not automatically extend to other executors or expired leases.
10. Version/license/provenance, conformance tests, maintainer and rollback are
    required before a component is included in supported delivery profiles.

Review findings are feedback Artifacts with explicit role-level acceptance,
not automatic terminal FAIL Evidence. Bounded revision commands must precede
final verification and retain the immutable output version. Existing terminal
FAIL behavior is preserved; a failed Mission cannot be revived by orchestration.

The first candidates are OpenSandbox as an execution backend, Playwright for
browser tools/verification, Agent Skills format for procedures, and one optional
OpenHands SDK executor comparison. These are PoC selections, not adopted runtime
dependencies. Existing optional new-api behavior under ADR-0104 is preserved.

## Consequences

This preserves existing domain and persistence investment while reducing
repeated implementation of execution details. It makes upstream replacement
possible without rewriting Mission history or treating upstream success as proof.

Integration still costs identity translation, dependency isolation, version
maintenance, budget reconciliation, cancellation and artifact provenance work.
An upstream component that cannot meet a profile's hard constraints remains
experimental or is rejected. More components are not an acceptance criterion.

CLI/Web form the first shared product path. Desktop and editor integrations
reuse that path. Team-only identity, shared storage, strong isolation and optional
knowledge services stay separate from the minimal local installation.

## Alternatives considered

- Replace Mission Control with a complete external agent/workflow platform:
  duplicates or discards existing state, authorization and verification work.
- Run multiple agent frameworks as peer schedulers: creates competing lifecycle
  and budget authorities and unprovable cancellation/recovery behavior.
- Copy a reference project's core into AgentHub: creates language, licensing and
  long-term fork maintenance costs without demonstrating user-flow improvement.
- Keep all capability implementations in-house: retains unnecessary browser,
  sandbox and provider integration maintenance.

## Verification

Before implementation is accepted, demonstrate one real repository task through
the shared Contract/HTTP path, a version-pinned executor and isolated workspace,
an immutable patch, independently executed tests registered during RUNNING,
the final manifest, subsequent Evidence admission during VERIFYING and
a reviewable result. Failure and negative cases must include old attempt/lease,
tampered patch/report, unavailable backend, denied capability, cancellation,
parallel budget reservations and preserved user changes.

Compare Native and a candidate executor on the same held-out tasks; record
quality, real usage/cost, elapsed time and interventions. Plugin removal must
disable new admission without erasing history or silently migrating checkpoints.
Production status requires applicable current-environment evidence separately.

## Supersedes

None. This ADR refines integration boundaries without replacing accepted
Mission ownership, ADR-0104 optional supplier gateway, ADR-0107 memory slimming,
or ADR-0115/0117 recovery and lease semantics. New human-acceptance, budget,
manifest and trigger persistence contracts require their own implementation ADRs.
