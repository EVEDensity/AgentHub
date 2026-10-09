# ADR-0111: Consolidate deterministic CI and explicitly enable external evidence

> Status: accepted  
> Owner: repository maintainers  
> Date: 2026-10-09  
> Scope: validation ownership, external evidence and release automation

## Context

Overlapping contract, migration and CLI workflows repeated partial tests with
different dependency installations. The main Python job collected no service
tests and ignored static failures while omitting the complete application suite.
PR review required an unconfigured paid model key, blocking unrelated changes.
Fixed historical size exemptions had drifted from the actual base revision.

## Decision

- Own deterministic PR validation in one main workflow with a stable `CI Gate`.
  Always run the complete Python application suite, quality gates and real
  PostgreSQL integration. Select expensive language, platform and packaging
  checks by versioned dependency paths; run them all when CI itself changes.
- A required or selected check must finish successfully. Missing selection,
  failed/cancelled jobs and unexpected skips fail the aggregate gate.
  Do not use ignored failures to claim successful validation.
- Reject size/complexity regressions against the base revision, with explicit
  stricter bounds for new code, instead of enlarging static debt exemptions.
  Retain the full audit for separate debt remediation.
- Provision checksummed tokenizer assets before offline tests. A missing or
  corrupted native asset fails its selected check rather than passing a skip.
- Combine paid providers and configured external recovery/vision probes under
  explicit enablement. Once enabled, missing configuration and actual failures
  fail. Deterministic CI owns no production provider-readiness claim.
- Keep CLI/desktop releases and installation lifecycle checks separate because
  their version inputs and publishing permissions differ from PR validation.
- Remove unvalidated third-party automatic-edit workflows and keep the existing
  AgentHub review loop as an optional same-repository, trusted-author integration.

## Consequences

Maintainers have one required PR status with complete application coverage and
real database checks. Unaffected optional runtimes can skip without weakening
the gate. Branch protection must be migrated to `CI Gate`; deleting a workflow
does not change repository settings.

Style/type/format steps without a configured passing baseline are removed from
the default workflow. Python runtime errors, Go vet/race, TypeScript, production
compilation and actual tests remain enforced. The CI guide records the remaining
style/type/format debt explicitly. No execution ownership, protocol or durable
business state changes are introduced by this automation decision.

## Verification

Selection tests cover dependency changes, all-check runs and invalid Git bases.
Gate tests reject failed, cancelled, missing and unexpectedly skipped checks.
Asset tests reject corrupted cache/download bytes and verify atomic fallback
downloads. `actionlint` validates the complete workflow expressions and graphs.
Hosted runs remain the evidence for Linux containers, PostgreSQL and Windows
platform behavior; local workflow validation alone does not establish that result.
