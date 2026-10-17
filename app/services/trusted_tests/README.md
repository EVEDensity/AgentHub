# Trusted test production experiment

This package is an explicit `trusted-local` vertical slice. No default Chat,
CLI or Desktop composition enables it. An operator freezes a source snapshot,
an external acceptance suite and a versioned test profile, creates a matching
Mission Contract policy, and injects `TrustedRepositoryHarness` into a Runner.
Mission Control remains the owner of state and final verification.

## Ownership and data flow

- `snapshot.py` captures dirty/untracked regular files, with bounded file count
  and sizes. Known secret names (`.env*`, keys, tokens, credential JSON), VCS,
  dependency caches and runtime directories are omitted. Operator exclusions can
  add patterns; these filters do not discover credentials embedded in arbitrary
  source files. Links, hardlinks, special files, ambiguous paths and changed
  inventories are refused. Copy roots must not overlap. Snapshots have explicit
  `close()`/context-manager lifetimes.
- `profile.py` freezes an operator-owned external Python test suite before Agent
  execution. Its digest binds suite bytes/modes, argv, interpreter bytes, pytest
  version and process limits. The only supported driver is the current process's
  Python interpreter, supplied as an absolute path resolving to `sys.executable`,
  followed by `-m pytest` and a small allowlist of pytest options. Other
  interpreters are refused; the recorded pytest version belongs to this runtime.
  Neither objectives nor Agent output supply commands or acceptance tests.
- `harness.py` materializes an independent candidate, injects its directory into
  a caller-provided native Harness factory, captures a real Git binary patch,
  and invokes the producer. Resume is refused. Its deadline covers candidate
  execution and production; cancellation cleans owned temporary directories.
- `producer.py` reconstructs the same base plus patch in another directory and
  runs only the frozen external suite. Pytest config/conftest and plugin autoload
  from the candidate are disabled. Both executed trees are checked after tests;
  changed input/suite bytes cannot produce a passing report.
- `process.py` defines a replaceable backend port. Local execution whitelists the
  child environment, caps captured output, polls file/count limits, applies a
  wall deadline and cleans its process group after normal exit, cancellation or
  timeout. Windows
  also assigns a kill-on-close Job Object with process-count and memory limits.
  Filesystem polling is a detected limit, not a hard disk quota.
- `junit.py` checks XML bounds, declarations, testcase identities/outcomes and
  counters. Missing/malformed/contradictory XML, no tests, all skipped tests,
  timeouts and output/file limits cannot be passing test reports.
- `models.py`, `signing.py`, `policy.py` and `evaluator.py` bind exact Contract
  revision, attempt, snapshot/candidate/profile and artifact digests to an
  operator-authorized producer key. The parent signs; no private key or Runner
  token is passed in test argv/environment/files.

Production returns exactly five byte artifacts: Git binary patch, canonical
`TestRunReport.v1`, bounded log, signed receipt and final execution manifest.
Only the Runner publishes/registers them under its active RUNNING lease; the
producer has no Mission Control command client. A prepared bundle means execution
completed, while an independent verifier must accept or reject the test result.

## Trust and verification limits

This local backend executes trusted repository and acceptance code under the
same OS account. It does not prevent malicious code from reading host files,
using the network, attacking a same-user parent or producing consistently forged
JUnit. Keeping the signer object outside the child input is not OS isolation.
Production use requires a separate principal, read-only operator inputs, protected
signing, network/filesystem controls and a bounded execution backend; that backend
can implement `TestProcessBackend` without changing Mission ownership.

The local gate requires Python/pytest and Git. Service tests execute real small
repositories and external suites, including pass/fail, Agent test replacement or
deletion, inert candidate conftest, environment filtering, runtime tree tampering,
timeout/cancellation, output/file bounds and patch/snapshot corruption. POSIX
FIFO/symlink checks skip explicitly on hosts that cannot create those objects.

## Operator trust and replay boundary

Both Mission Control and Verifier read `AGENTHUB_TRUSTED_TEST_KEYS_FILE`, an
operator-controlled JSON document with `schemaVersion: 1` and `keys` containing
`producerId`, `keyId`, `publicKeyBase64` and a `testProfileDigests` allowlist.
Keys authorize only explicitly listed profiles. Missing or invalid configuration
trusts nobody; Contracts and reports cannot supply their own trust roots.
Receipt and manifest bind actual Contract ID/version and the normalized
`configurationDigest`. Changing the revision or policy cannot replay an otherwise
valid signature.

The exact five artifacts are `diff` patch, `test-result` canonical report, `log`
process output, `report` signed receipt and `report` execution manifest. Manifest
references the four others by canonical ID/digest and is registered last while
Runner still owns its RUNNING lease. Independent Verifier reads registered bytes
only; Mission Control independently re-evaluates PASS. Byte-integrity failures
admit no Evidence. Invalid signatures/profile/snapshot bindings and failed,
empty or all-skipped reports never establish PASS.

For this policy, Mission Control checks the complete registered current-attempt
Artifact set both before byte verification and under the admission transaction
locks. A verifier cannot select five passing refs while omitting another report
or another bundle registered for that attempt.

The local `source-snapshot.v2` manifest binds file bytes/modes and source
provenance: `sourceType` is `git-commit`, `git-unborn` or `non-git`, with
`baselineCommit` containing the actual resolved Git HEAD commit only in the
first case and explicit null otherwise. Dirty and untracked files retain their
actual captured bytes. A separate `tree_digest` identifies bytes/modes alone,
so reconstruction remains independent of the presence of Git metadata.

HEAD/state is sampled before and after both file inventories; detected changes
or corrupt/unavailable Git metadata refuse capture and clean temporary storage.
The frozen manifest remains bound to its recorded commit if the live repository
later advances. This is provenance for a captured possibly dirty source tree,
not a claim that its files match a clean checkout or that concurrent repository
writers are locked out. Existing receipt/manifest `inputSnapshotDigest` fields
sign the digest of this provenance-bearing snapshot manifest; no receipt schema
field or additional Artifact was introduced. The operator retains the complete
`source-snapshot.v2` manifest and source bytes for the `FrozenSnapshot` lifetime;
the exact-five Artifact bundle does not export the initial source snapshot.
Historical replay requires retaining that input snapshot/provenance, because
patch plus Git HEAD alone cannot recover dirty or untracked initial bytes. This
experiment does not complete the full M1 platform milestone.

Wire/evaluator checks are in `tests/contracts/test_trusted_test_contracts.py`
and `tests/services/test_trusted_test_evaluator.py`. SQLite verification is in
`tests/integration/test_trusted_test_verification_sqlite.py`; real
producer/Runner/HTTP/SQLite composition is exercised by
`tests/integration/test_trusted_repository_runner_sqlite.py`.
