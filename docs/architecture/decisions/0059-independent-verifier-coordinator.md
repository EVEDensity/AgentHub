# ADR-0059: Independent Verifier Coordinator

> Status: accepted  
> Owner: verification and Mission Control maintainers  
> Date: 2026-08-16  
> Scope: verifier control port, deterministic evaluation, and polling supervision

## Context

Mission Control now exposes narrow verifier discovery, Contract-derived
evaluation policy, controlled Artifact byte evaluation, and server-owned
Evidence integrity. No independent runtime joined those boundaries. Reusing a
Runner principal or its lease would let the executing service verify its own
output. Treating discovery as a durable claim would create a second work-state
authority and require recovery semantics that Mission Control already owns.

The discovery contract can also return an inconclusive policy without a
criterion ID, while every Evidence submission requires a criterion ID. An
automated verifier cannot safely choose an arbitrary Contract criterion merely
to make the request valid.

## Decision

The application layer owns a verifier coordinator with a separate
`MissionControlVerifierPort`. The port exposes only workspace-scoped discovery
and WorkUnit Evidence admission. Its HTTP adapter uses the verifier bearer
credential and strictly rejects malformed JSON, non-object responses, and
remote errors.

For one ready discovery result, the coordinator:

1. validates the complete version-2 projection and its Mission, WorkUnit,
   attempt, Artifact, policy, and criterion relationships;
2. constructs immutable Artifact descriptors without inventing creator,
   retention, or execution metadata absent from the projection;
3. reads every registered Artifact through the configured byte verifier;
4. executes the registered `artifact-set.v1` evaluator over the exact byte
   observations;
5. submits PASS only from that controlled result; and
6. validates that Mission Control's Evidence and state response closes over
   the submitted Mission, WorkUnit, criterion, verifier, configuration, verdict,
   and Artifact references.

The coordinator never returns a default, model-generated, or synthetic PASS.
An inconclusive discovery policy raises a typed operational failure before
Artifact I/O or Evidence submission. Automatic INCONCLUSIVE Evidence is deferred
until its public contract has an unambiguous criterion attribution. This can
stall a misconfigured WorkUnit, but it does not corrupt acceptance truth.

A process-local worker polls exactly one explicit workspace. Idle discovery and
failures use bounded exponential backoff; a verified item resets the delay.
Snapshots contain counters, timestamps, enum states, and exception types only.
They exclude Mission and WorkUnit IDs, objectives, Artifact metadata and bytes,
remote error text, and credentials.

Discovery creates no verifier lease. Concurrent or repeated evaluation is
allowed; the existing Mission Control verification transaction serializes
admission and rejects stale WorkUnit state. A graceful stop waits for the active
evaluation. Cancellation of the worker task propagates through byte I/O,
evaluation coordination, and the Mission Control call instead of being converted
to a retry.

## Consequences

Runner and verifier identities, credentials, and responsibilities remain
separate. The verifier can independently reproduce the minimum structural proof
before asking Mission Control to reproduce it again inside the durable
transaction. The shared Artifact byte port now depends on the minimum immutable
metadata it consumes, allowing the narrow discovery projection to be verified
without fabricating a full durable Artifact.

This slice provides the pure coordinator and worker boundaries, not a deployable
verifier process. Startup configuration, mounted secret handling, health and
readiness endpoints, shutdown deadlines, and container assets remain the next
composition stage. A later semantic evaluator must receive its own explicit
policy name and implementation; it cannot broaden `artifact-set.v1`.

Repeated control-plane conflicts are ordinary failed polls and may repeat work
because there is no verifier lease. Fleet-level fairness and duplicate-cost
control remain Mission Control concerns if observed load justifies a protocol
change.

## Alternatives considered

- Run verification inside Runner: rejected because the executing principal
  cannot be the sole verifier of its own output.
- Add a verifier lease immediately: rejected because duplicate computation is
  safe and Mission Control already serializes the durable state transition.
- Submit INCONCLUSIVE against the first Contract criterion: rejected because
  criterion order does not establish evaluation ownership.
- Trust Mission Control to perform all byte evaluation without local replay:
  rejected because the independent runtime would contribute no independently
  reproduced observation before requesting PASS.

## Verification

Coordinator tests cover idle behavior, strict response parsing, policy
attribution failure, real local Artifact byte reading, controlled PASS
submission, response closure, narrow HTTP routes, verifier authorization, and
cancellation propagation. Worker tests cover bounded backoff, reset after
success, graceful stop, active-evaluation cancellation, single-run enforcement,
and content-free snapshots. Existing Artifact integrity and evaluator tests
remain regression gates for the generalized metadata ports.

## Supersedes

This decision implements the isolated-runtime follow-up from
[ADR-0056](0056-fail-closed-evaluation-policy.md) while preserving the no-lease
discovery boundary in [ADR-0055](0055-verifier-work-discovery.md) and the
controlled PASS rules in [ADR-0057](0057-controlled-byte-evaluation.md).
