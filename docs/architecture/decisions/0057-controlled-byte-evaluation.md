# ADR-0057: Controlled Artifact Byte Evaluation

> Status: accepted  
> Owner: Mission Control and verification maintainers  
> Date: 2026-08-16  
> Scope: Artifact byte verification and PASS evaluation execution

## Context

ADR-0056 binds PASS Evidence to a Contract-derived evaluation plan, and the
Artifact byte verifier streams each registered object to verify its digest and
size. Mission Control previously treated a non-throwing `verify_all` call as
sufficient and discarded its results. A faulty or substituted verifier port
could therefore return an empty, duplicate, or unrelated result set without
preventing PASS.

The policy plan, Artifact metadata, and byte-derived observations need one
controlled evaluator boundary before Evidence can change durable state.

## Decision

Mission Control owns a `VerificationEvaluator` port. The default strict
implementation registers only `artifact-set.v1`; evaluator selection is taken
from the resolved Mission Contract policy and cannot be supplied by the API
caller.

`artifact-set.v1` consumes the resolved plan, exact current-attempt Artifacts,
and the `ArtifactByteVerification` records computed while streaming their
content. It requires:

- the policy's minimum count and required Artifact kinds;
- unique Artifact and verification IDs;
- an exact one-to-one closure between both ID sets;
- byte-derived digest and size values matching registered metadata.

The evaluator canonicalizes successful observations by Artifact ID and returns
an internal PASS result bound to the criterion, evaluator, and configuration
digest. Mission Control executes the pure evaluator once after byte I/O and
again inside the Evidence-admission transaction after current Contract,
WorkUnit, attempt, and Artifact metadata are revalidated. Any mismatch fails
before Evidence, events, or lifecycle state are written.

FAIL and INCONCLUSIVE Evidence remain conservative verifier observations and do
not require a successful PASS evaluator result. Artifact bytes remain in the
Artifact store; Mission Control receives only bounded verification records and
does not persist content.

## Consequences

A successful byte-verifier call is no longer enough: PASS requires a complete,
reproducible result closure. Reordered results are accepted and canonicalized;
missing, extra, duplicate, wrong-digest, and wrong-size results fail closed.

This evaluator proves storage availability, byte integrity, and declared
Artifact-set shape only. It does not inspect test semantics, source behavior,
security properties, or natural-language claims. Content-aware evaluators must
be independently implemented and registered rather than mapped to
`artifact-set.v1`.

There is no public schema change in this slice. The canonical internal result
is the input for the next Evidence integrity-hash decision.

## Alternatives considered

- Continue trusting a non-throwing byte verifier: rejected because port output
  completeness would remain unchecked.
- Accept evaluator output from the verifier API caller: rejected because the
  caller could create its own success proof.
- Load all Artifact bytes into Mission Control: rejected because it expands
  memory, sensitivity, and ownership boundaries without benefiting this
  structural evaluator.

## Verification

Evaluator unit tests cover canonical success, unsupported and unsatisfied
plans, exact set closure, duplicates, digest mismatch, and size mismatch. API
tests prove incomplete byte results cannot write Evidence or transition the
WorkUnit or Mission. Existing PASS, FAIL, INCONCLUSIVE, Artifact-integrity, and
transaction-race tests remain regression gates.

## Supersedes

This decision implements the controlled evaluator execution follow-up from
[ADR-0056](0056-fail-closed-evaluation-policy.md). ADR-0056's policy ownership
and fail-closed PASS admission remain in force.
