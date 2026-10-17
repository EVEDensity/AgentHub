# ADR-0119: Share explicit output-integrity acceptance across entry points

> Status: accepted\
> Owner: Mission Control and client maintainers\
> Date: 2026-10-17\
> Last reviewed: 2026-10-17\
> Scope: default CLI, Chat and Desktop task admission

## Context

CLI's default task contract selected `artifact-set.v1`, while Chat and Desktop
created an unconfigured manual criterion. The latter could reach a Decision
whose supported actions did not include accepting the output. Clients also
advertised unsupported input controls or sent fields outside the Chat DTO.

## Decision

Python admission uses `app/services/default_mission_contract.py`. Desktop's
JavaScript projection is checked against the executed Python builder in a
cross-language contract test. The default `desktop.task` criterion keeps the
existing criterion ID and `manual` kind, with explicit configuration selecting
the deterministic `artifact-set.v1` evaluator. No new criterion version or
state transition is introduced.

Acceptance proves that at least one required `test-result` Artifact is registered
and that its bytes match its declared size and SHA-256. The existing evaluator
permits empty bytes with the matching digest. A historical Artifact kind named
`test-result` is not proof that tests ran. This default does not establish code
correctness, sandbox isolation, a real provider call, or trusted test provenance.
Those require the later independently produced reports in ADR-0118.

The default verifier does not interpret `RUN:` or `VERIFY:` text in the Mission
objective as shell authorization. Executing commands requires a separate
explicit execution/verification contract; natural-language content cannot
expand the default's empty grants. The legacy command helpers remain available
for explicitly composed callers, outside default Artifact integrity evaluation.
The CLI review command therefore labels clean candidate findings `UNVERIFIED`
and returns exit 4 until trusted review evidence is available. Model-reported
blocking findings still return exit 1; absence of trusted validation is not a
passing review.

The builder preserves each entry point's existing time/retry limits and empty
capability and repository grants. Clients cannot expand permissions with UI
controls. Unsupported attachments, references and execution-policy fields are
rejected by the strict Chat DTO before persistence. Web prevents these sends
and preserves the draft; the editor sends the supported text contract and uses
the correct pending confirmation and admitted Mission cancellation commands.

Only newly admitted Missions use the shared default. Existing immutable
Contracts, waiting Decisions and terminal Missions are not migrated. Explicit
custom contracts and manual-policy behavior are unchanged.

## Verification

Contract tests execute both builders. HTTP tests assert unsupported fields leave
Mission/session/pending tables empty. SQLite integration tests admit through
Chat, acquire/start a Runner lease, publish real CAS bytes, complete into
VERIFYING and use a separate Verifier credential to submit Evidence through
Mission Control. Missing/tampered bytes cannot produce success; cancellation
prevents verification discovery. Client tests cover text requests and errors.

## Consequences and rollback

The default task can finish with an honest, narrow acceptance claim. It is not
the repository coding acceptance planned for M1. Rollback affects only admission
of new tasks; never rewrite old Contracts or Evidence to make them pass. A new
acceptance policy must be versioned and tested before replacing this default.
