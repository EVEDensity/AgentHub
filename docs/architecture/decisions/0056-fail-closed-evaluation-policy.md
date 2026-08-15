# ADR-0056: Fail-Closed Evaluation Policy Admission

> Status: accepted  
> Owner: Mission Control and verification maintainers  
> Date: 2026-08-16  
> Scope: verification discovery, evaluation policy, and PASS Evidence admission

## Context

Verifier discovery exposes acceptance criteria and Artifact metadata, but a
criterion description is not an executable evaluation rule. Allowing a verifier
to choose an evaluator or submit PASS without a rule would make model output or
an arbitrary configuration a second source of success truth. A discovery-only
policy would also be advisory because callers can invoke `/verify` directly.

No separately deployed verifier consumer uses the discovery response yet, so
the policy contract can be added before the first consumer without a dual-read
migration.

## Decision

Mission Contract acceptance-criterion configuration is the source of
evaluation policy. Mission Control resolves exactly one policy applicable to
the WorkUnit kind and exposes the result in verification discovery. Unknown,
malformed, ambiguous, unsupported, or currently unsatisfied policies resolve to
`inconclusive`; they never synthesize a verdict.

The first supported evaluator is `artifact-set.v1`. Its normalized parameters
are a minimum Artifact count and required Artifact kinds. Its canonical
configuration digest is computed from sorted, compact JSON. This evaluator
proves only that the current attempt has the declared, byte-verifiable Artifact
set. It does not prove test execution, semantic correctness, security, or the
truth of an Artifact's contents.

PASS Evidence is admitted only when the submitted criterion ID and exact
configuration digest match a ready policy. Mission Control checks this once
before Artifact byte I/O and again under the Mission/WorkUnit transaction lock
using current Contract and Artifact metadata. Direct `/verify` calls therefore
cannot bypass discovery. FAIL and INCONCLUSIVE Evidence remain admissible so a
verifier can conservatively record failure or uncertainty.

The discovery context payload advances from version 1 to version 2 and requires
`evaluationPolicy`. Mission Control is the producer; the planned isolated
verifier runtime is the first consumer. Rollback requires reverting the schema
and producer together before that consumer is deployed. Existing durable
Mission, WorkUnit, Artifact, and Evidence records need no migration.

## Consequences

Success now fails closed when policy is missing or cannot be reproduced. A2A
inbound and outbound Contract templates explicitly bind their WorkUnit kinds to
`artifact-set.v1`, so imported work can reach the verifier path without a
protocol-specific success rule.

The initial evaluator is intentionally weak: Artifact structure and byte
integrity are necessary but not sufficient evidence. Stronger deterministic
evaluators must be separately implemented and registered before their names are
accepted. A generic model judge cannot issue PASS under this decision.

The next slice is an isolated verifier runtime that reads Artifact bytes and
executes a concrete evaluator, followed by reproducible or signed Evidence
integrity hashes and verifier supervision.

## Alternatives considered

- Trust verifier-supplied evaluator names and digests: rejected because callers
  could define their own success semantics.
- Enforce policy only during discovery: rejected because `/verify` is callable
  independently and remains the durable state-transition boundary.
- Treat an LLM judgment as the default evaluator: rejected because stochastic
  output is not a fail-closed proof and would let model capability replace the
  product's trust boundary.

## Verification

Policy unit tests cover canonical hashing, malformed and ambiguous policies,
unsupported evaluators, and Artifact requirements. API tests prove PASS is
rejected before Artifact I/O without an exact policy binding, revalidated after
I/O under the transaction, and accepted only with the matching criterion and
digest. Contract tests validate both evaluation-policy states and discovery
version 2. A2A API tests prove generated inbound and outbound Contracts resolve
to ready plans when their Artifact requirements are met.

## Supersedes

This decision refines the discovery response version and the evaluation-policy
open item in [ADR-0055](0055-verifier-work-discovery.md); its authorization,
selection, projection, and no-lease decisions remain unchanged.
