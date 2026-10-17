# ADR-0121: Admit signed test bundles under the execution lease

> Status: accepted
> Owner: execution and verification maintainers
> Date: 2026-10-17
> Last reviewed: 2026-10-18
> Scope: M1 trusted-local repository execution experiment

## Context

The default `artifact-set.v1` proves only byte integrity. Existing `test-run.v1`
checks report semantics but has no producer identity, frozen input or fixed test
profile. Neither permits claiming that an independent trusted process tested a
particular patch. Objective text is not command authorization (ADR-0119).

## Decision

Add the explicit `trusted-test-run.v1` policy. Preserve all existing evaluators
and defaults. A single aggregate policy checks a five-Artifact closure: patch,
canonical `TestRunReport` v1, log, signed receipt, and a final manifest. Reuse
`diff`, `test-result`, `log`, and `report` Artifact kinds. Do not add a new
Mission, lease or Evidence authority.

An operator freezes a bounded source snapshot including admitted dirty/untracked
files, the actual Git HEAD (or explicit unborn/non-Git identity), a separate
acceptance suite, Python/pytest command profile and resource limits before
execution. Secrets, runtime metadata and special/link files are
excluded or rejected. The executor receives a candidate copy. The producer
reconstructs the same snapshot plus binary patch and runs the frozen external
suite in another temporary directory. Workspace pytest configuration, plugins
and local tests do not define acceptance. The snapshot digest also binds its
source provenance. The five-Artifact bundle does not export the initial source
snapshot: operators must retain that input for historical replay, especially
when dirty files differ from Git HEAD.

The receipt binds Mission, WorkUnit, attempt, Contract ID/version, policy digest,
input and candidate snapshot digests, test profile digest, patch/report/log
digests, actual exit/timeout and ordered timestamps. Ed25519 signs a canonical,
domain-separated envelope. The manifest binds exact Artifact references without
a circular digest. Signing keys are never serialized into test inputs. Test
failure, no tests, all skipped, unreadable/malformed reports and unknown results
cannot produce PASS.

Only the operator trust registry authorizes a producer key and exact test-profile
digest. A Contract, receipt or model cannot add trusted keys. Missing, malformed,
revoked or out-of-profile trust fails closed. Both Verifier and Mission Control
read registered bytes and enforce the same signature and binding checks.
For this aggregate policy, Mission Control requires the submitted five refs to
match the complete registered Artifact set of the current attempt, before byte
verification and again inside final transactional admission. A verifier cannot
hide an additional report by selecting a passing subset. Existing evaluators
retain their own subset semantics.

Runner supervises the complete attempt: execution, test producer, publication,
registration and completion share one deadline and heartbeat. Register the
manifest last, then complete into VERIFYING with every ref. A shared local fence
blocks new dispatch after cancellation or deadline even when an awaited port
returns late. Terminal HTTP commands may already have committed; a bounded
500 ms ACK grace accepts only an actual response. An unconfirmed terminal ACK
remains unknown and never triggers a competing failure command. Cancellation
drain and final flush each have a 250 ms cooperative bound, beyond the execution
deadline; synchronous code and already dispatched requests cannot be revoked
by these async bounds. Producers never
receive the Runner credential/lease; Verifier cannot register late Artifacts.
Partial publication cannot establish acceptance. Interrupted publication keeps
the existing recoverable RUNNING lease semantics; expired leases and unsupported
resume fail closed. There is no transparent trusted-local resume in this slice.

## Trust and deployment limits

`trusted-local` is an explicitly selected experiment for trusted repositories.
Separate directories, subprocess groups, bounded files/logs and an environment
allowlist prevent accidental coupling, but same-user host processes are not a
hostile-code security boundary. In particular this does not prove that arbitrary
malicious code cannot inspect the parent or tamper with its OS account. Strong
isolation and separate identities remain required before enabling untrusted or
team workloads. OpenSandbox has its own conformance gate and is not silently
substituted for this producer backend.

The experiment is injected into the existing Native Harness/Runner ports. It
does not activate for default Chat, CLI, Desktop or externally supplied prompts.
Mission-wide money reservation and external executor admission remain separate
M1 delivery slices; these test bundles do not establish accounting correctness.

## Verification and rollback

Test the complete Native Harness, real external pytest, CAS, RUNNING registration,
VERIFYING discovery and independent authenticated SQLite Evidence flow. Negative
cases cover failed/empty/skipped suites, changed workspace tests, modified patch,
snapshot/profile/Contract/attempt binding, untrusted signatures, partial bundles,
deadline, cancellation and lease loss during publication. Recheck existing
recovery and default-integrity contracts.

Disable new experimental admission and revoke the profile/key to roll back.
Preserve immutable registered bundles, Contracts and historical Evidence. Do not
replace failed semantic verification with the default integrity-only evaluator.
