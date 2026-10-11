# ADR-0114: Reproduce conclusions from registered report bytes

> Status: implemented
> Owner: verification maintainers
> Last reviewed: 2026-10-09
> Scope: test/security report contracts and independent Evidence admission

## Context

`test-run.v1` and `security-scan.v1` previously counted the expected Artifact
kind without reading report conclusions. `minimumPassRate` and `maxSeverity`
were present in immutable policy digests but did not govern PASS. The public
evaluation-policy schema also listed only `artifact-set.v1`, despite five
registered implementations.

## Decision

Reports use explicit v1 JSON contracts with Mission ID, WorkUnit ID, attempt,
completion conclusion, exit status, and bounded result records. Test counters
must equal individual case results, whose identities are unique across the
aggregate report set. Security counts and maximum severity
must equal the individual findings. Duplicate identities, duplicate JSON keys,
non-finite numbers, unknown fields, incorrect types, and unsupported versions
are rejected. A successful test report must contain at least one passing test;
an empty or entirely skipped suite cannot pass.

The Artifact byte verifier retains at most 1 MiB of report/test-result bytes in
an internal observation after checking registered size and SHA-256. Other
Artifacts continue streaming without body collection. Raw report bytes are
excluded from observation repr, discovery, HTTP responses, Evidence, and logs.
The controlled evaluator rechecks the body digest, size and exact attempt
identity before interpreting any result. Catalog, execution, and HTTP metadata
cannot supply a passing conclusion.

Failed/error test cases, an unsuccessful exit status, an inconclusive result,
a low aggregate pass rate, or findings exceeding the Contract severity limit
produce FAIL. Missing, malformed, oversized, or inconsistent registered reports
also fail acceptance. Missing required Artifact kinds continue through existing
Mission Control policy discovery into its human Decision path. Digest/size or
byte-result closure failures remain integrity errors and admit no Evidence.

The independently authenticated verifier submits the reproduced PASS or FAIL.
Mission Control retains transactional lifecycle ownership and re-evaluates PASS
against its own registered bytes. A client submitting PASS over a failed report
is rejected without Evidence or state mutation. A FAIL admission moves the
WorkUnit and Mission to FAILED through the existing transitions.

## Compatibility and limits

The existing evaluator IDs and configuration keys keep their intended meaning;
this fixes the incomplete implementation of those semantics. The discovery
schema now describes all five existing evaluators. New report bodies carry an
explicit v1 version. Historical Evidence is not rewritten. Pending legacy
free-text or summary-only reports no longer qualify for semantic PASS.

Report producers must emit the new contracts before enabling these acceptance
criteria. Report-producing runners or external scanners own execution; the
verifier reproduces the conclusion recorded in registered bytes. It does not
rerun a test suite or scanner and cannot establish that a report producer ran
the claimed command honestly. An independent verifier identity and byte
integrity are required but are not external execution attestation.

For rollback, stop semantic-policy verifier consumption while replacing the
deployment. Preserve durable WorkUnit, Mission and Evidence state; do not
substitute an Artifact-presence policy as proof that tests or security passed.

## Verification

Unit and public-contract tests cover positive reports, negative cases, skipped
and empty suites, pass-rate and severity boundaries, forged summaries, invalid
versions/types/identities, duplicate records and keys, and byte closure.
HTTP integration exercises real local CAS reads, an authenticated independent
verifier, controlled FAIL/PASS admission, and direct forged PASS rejection with
no mutation. These are integration tests, not claims of real external test or
scanner execution.
