# ADR-0125: Compose the Native Harness with an explicit sandbox capability

> Status: accepted
> Owner: Runner and Mission Control maintainers
> Last reviewed: 2026-10-18
> Scope: M1c experimental budgeted Native/OpenSandbox shell execution

## Context

[ADR-0122](0122-experimental-opensandbox-http-port.md) establishes a pinned
OpenSandbox conformance experiment, while [ADR-0124](0124-strict-in-process-provider-budget-profile.md)
connects an explicit direct model profile to Mission-owned accounting. Neither
establishes the complete Native tool-loop to sandbox and independent Artifact
verification flow. A report from a shell command is not a test verdict.

Moving candidate code and pytest into the same container does not separate
their Python execution or report-writing authority. This slice does not add a
remote trusted-test mode or reinterpret container execution as independent
code-correctness evidence. The existing trusted-local assumptions remain in
[ADR-0121](0121-trusted-local-test-bundles.md).

## Decision

Add an operator-only `BudgetedOpenSandboxHarnessFactory`. It reuses the existing
native claim validation for manual/chat desktop root WorkUnits, actual Contract
revision, attempt and Runner lease. The WorkUnit must require exactly
`sandbox.execute`; a unique Contract grant must bind the exact scope returned
by `sandbox_capability_scope()`: `opensandbox-shell.v1` and the pinned workload
image. Host repository scopes are unsupported and rejected, not mounted.

The factory privately constructs the existing BudgetedModelPort and a Native
FunctionCallingHarness with model retry disabled. Provider keys stay outside
the container. Model metadata cannot choose its lease, endpoint, image or price.
The operator's existing reviewed billing bounds and frozen prices still apply;
this composition does not verify an arbitrary supplier's billing behavior.

`SandboxNativeHarness` allocates one fresh sandbox per single-use attempt and
injects only a bounded `run_shell` function. The initial working directory is
`/workspace`; scripts can access the container filesystem, so this is not a
path restriction to that directory. Image, resource limits, deny-default network
policy and absence of host mounts come from the fixed backend configuration.
The model never receives lifecycle credentials or direct sandbox management.
Resume and streaming are refused in this first factory.

Commands are serialized, with explicit call, script, timeout and output limits.
An early failed command can be followed by a successful repair command; every
observation remains visible. Success requires at least one actual command, a
successful last command, bounded output, model success and confirmed destruction.
Fatal backend or output errors prevent success.
The overall execution deadline covers provisioning, model/tool work and normal
cleanup. Failure/cancellation allows only a bounded cleanup grace for the owned
sandbox. Late or cancellation-resistant results cannot publish artifacts.

Successful destruction must be acknowledged before returning the two-Artifact
bundle: model text (`test-result`, text/plain, following the existing default
contract) and a versioned execution observation (`report`, application/json).
An injected native Harness returning another Artifact bundle is refused. Runner
alone publishes and registers these bytes under the active lease; an independent
Verifier and Mission Control continue to own acceptance and final transitions.

The diagnostic observation binds Mission, WorkUnit, attempt, sandbox/job identity,
fixed image, command IDs, script SHA-256, host observation times, actual exit codes,
and bounded combined output. It never fabricates separate stdout/stderr streams.
`outputBytes` counts sanitized UTF-8 bytes before truncation. Scripts and manager
credentials are not included. The schema defines a diagnostic format, not a
TestRunReport, signature, PASS Evidence or new business state.

## Verification and limits

Local tests use the actual HTTP adapter with simulated sandbox wire responses
and real loopback model HTTP, SQLite budget transactions, authenticated Runner
commands, CAS and independent verifier admission. The explicit Linux gate runs
the same budgeted Runner scenario against real OpenSandbox/Docker, plus direct
Native success, exit 7 and cancellation cases. Its model is a named deterministic
fixture; it does not call a paid supplier.

`--native-composition` adds a separate native report to the existing disposable
wrapper. The original 19 conformance checks remain mandatory. Native checks
require distinct fresh sandbox IDs, actual output/exit, confirmed cancellation
and destruction, and the budget/Artifact/Verifier terminal result. Both reports'
resource IDs participate in exact-scope cleanup. Any residual managed resource,
missing required check or unconfirmed cleanup fails the job.

This remains an explicitly enabled trusted-code container experiment. It does
not establish hostile-workload protection, immutable in-container reporters,
multi-tenant production isolation or real supplier pricing conformance. Lost
create acknowledgements can leave resources whose IDs the client never learned;
the disposable CI wrapper independently collects job-labelled resources and
checks for residues, while operator deployment still needs durable orphan
reconciliation. Default clients and the standalone Runner are not switched.

There is no database migration. The new report is additive and existing
`artifact-set.v1` consumers continue byte verification. Disabling the factory
stops new admission; historical artifacts and evidence retain their meaning.
This slice does not complete all M1 acceptance gates or advance M2-M5 to done.
