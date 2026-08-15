# ADR-0061: Inconclusive Policy Criterion Attribution

> Status: accepted  
> Owner: verification maintainers  
> Date: 2026-08-16  
> Scope: verifier discovery contract and Evidence admission

## Context

Verifier discovery version 2 returned a reason when Mission Control could not
produce one executable evaluation policy, but did not identify which Contract
criteria caused or were affected by that result. A verifier therefore could
not report the condition without inventing a single criterion. Repeated
discovery also had no stable input for a future Mission Control Decision gate.

Criterion attribution must not let a verifier choose a policy, create durable
workflow state, or bypass Contract validation. Artifact storage may be remote
or expensive, so structurally invalid Evidence should also fail before byte
I/O for every verdict.

## Decision

The verification discovery context advances from version 2 to version 3.
`evaluationPolicy.status=inconclusive` now requires `criterionIds`, a sorted,
unique set containing only identifiers from the projected Contract. The set
may contain zero, one, or multiple identifiers. It describes the criteria
implicated by policy resolution; it is not a selected criterion, verdict,
Decision, or authorization to submit Evidence.

Mission Control derives attribution deterministically:

- invalid configurations identify the criteria containing those configurations;
- no applicable policy identifies configured criteria, or all Contract
  criteria when none has policy configuration;
- ambiguous policies identify all applicable criteria;
- an unsupported evaluator or unmet Artifact requirement identifies its one
  applicable criterion.

Ready policies continue to carry exactly one `criterionId` and are forbidden
from carrying `criterionIds`. The independent verifier strictly consumes
discovery version 3 and validates every attributed ID against the Contract. It
still submits only controlled PASS Evidence and treats inconclusive discovery
as a failed poll. Durable Decision state and human resolution are deferred to
a separate Mission Control-owned lifecycle change.

For every Evidence verdict, Mission Control validates that the requested
criterion belongs to the Mission Contract before Artifact byte verification.
Controlled PASS admission retains its transactional policy revalidation.

The response-schema change is intentionally breaking for the narrow internal
verifier port. Mission Control is the producer; `verifier_service.py`, its
worker/service composition, contract tests, and API tests are the current
consumers. They are upgraded in the same release. Rollback requires rolling
Mission Control and verifier processes back together; mixed v2/v3 processes
fail closed rather than guessing attribution.

## Consequences

Future Decision creation can reference an exact, reproducible criterion set
without trusting verifier inference. Invalid criteria fail before storage I/O,
and an inconclusive policy cannot be misrepresented as proof for one criterion.

This version does not stop repeated inconclusive discovery because no durable
Decision or `WAITING_DECISION` state exists yet. It also does not define human
approval, policy editing, or automatic retry semantics. Those remain the next
Mission Control lifecycle slice.

## Alternatives considered

- Reuse a single `criterionId`: rejected because ambiguity and fully
  unconfigured Contracts can affect more than one criterion.
- Let the verifier choose an attributed criterion: rejected because policy and
  lifecycle authority belong to Mission Control.
- Create a partial Decision table in this change: rejected because persistence,
  transitions, authorization, and resolution must be introduced atomically.
- Keep discovery v2 and infer attribution client-side: rejected because clients
  could disagree and drift from Contract resolution rules.

## Verification

Policy tests cover unconfigured, invalid, nonmatching, ambiguous, unsupported,
and unmet-requirement attribution. Schema tests require `criterionIds` only for
inconclusive policies and forbid it for ready policies. Verifier projection
tests reject unknown or duplicate attributed IDs. API tests prove a
non-Contract Evidence criterion is rejected before Artifact byte verification
or durable writes.

## Supersedes

This decision evolves the discovery projection introduced by
[ADR-0055](0055-verifier-work-discovery.md) and preserves the fail-closed policy
ownership established by [ADR-0056](0056-fail-closed-evaluation-policy.md).
