# Desktop Runner composition

This package composes desktop authentication, model context, scoped built-in
tools, Harness budgets/checkpoints, claim supervision and independent artifact
verification. Durable state changes go through Mission Control.

Shared Runner interfaces and immutable inputs/results live in
`app/services/runner_protocols.py`; `runner_client.py` owns the HTTP command
adapter. `runner_service.py` preserves its public imports, while
`_runner_service_impl.py` retains lease supervision. Pure claim/identity/budget
checks live in `runner_context_validation.py`, while `runner_model_context.py`
assembles the minimal model projection. `runner_model_resolver.py` binds that
projection to an injected request-scoped Harness; `runner_sync.py` isolates the
legacy standalone benchmark entry. Historical imports remain compatibility
aliases. Context compilation validates unique criterion identity, nonblank fork
ancestry and finite representable model cost before any model/tool call.
Moving these definitions does not change command ordering, execution identity,
lease ownership or verification authority. Capability advertisements on a
workspace claim are optional and emitted only when explicitly supplied.
`WorkUnitRunner` and `build_kind_aware_workspace_runner` accept an explicit
bounded, unique `supported_capabilities` tuple (default empty). This declaration
is used for contact matching; tool execution still requires Contract scope and
the existing permission policy. A RUNNING claim must restore a complete journal
for its exact attempt before execution. Missing manifests or anchors cannot
fall back to a fresh model loop. A concurrently held recovery lock returns
`capacity_saturated` without failing the active WorkUnit. After acquiring its
private recovery scope, a RUNNING resume synchronously renews and validates the
original lease before executing. A claim fetched before the previous owner
completed cannot replay cached output after that owner releases the lock. Only
Mission Control's exact 403 `Active Runner lease ownership required` rejection
maps that stale workspace poll to `idle`; general authorization, missing-resource
and service errors remain explicit errors.

All desktop workers use the authenticated principal as lease owner; local
worker indices are not server identities. Ordinary workspace claims prefer
ready work over owned busy attempts. Explicit recovery uses the optional
`resumeMissionId` fence to select only that Mission's original live lease.
Reclaiming it adds no tenant concurrency, and expired leases do not consume
capacity on either database backend. Admission locks retain tenant-before-row
ordering. See [ADR-0117](../../../docs/architecture/decisions/0117-fair-claims-and-targeted-resume.md).

Login retries connection refusal within a bounded readiness window because the
post-startup Runner task can start before Uvicorn binds its socket. HTTP credential
rejection remains immediate, and local authentication bypasses environment proxies.

`tool_approval.py` translates the resolved workspace permission policy into
explicit grants for the canonical Harness tool gateway. Suggest mode denies
workspace mutation; edit mode permits bounded workspace edits; auto mode also
permits the built-in code executor. Built-in handlers retain path and execution
limits. Objective text never grants command execution or extra tool authority.
Arbitrary remote, network and MCP side effects receive no implicit grant.
Child desktop Harness runs inherit the same permission policy. A factory without
an injected policy cannot approve side effects.

Verified by `tests/services/test_desktop_tool_approval.py` and
`tests/services/test_desktop_local_runner.py`.

Desktop factories with a complete credential-free model manifest use the
private recovery binding in `recovery.py`. It journals the bounded request,
ordered pending calls, real results, usage and elapsed budgets before admitting
only a digest through Mission Control. Private state is outside the tool
workspace. The exact admitted anchor is restored behind the same live lease;
uncertain receipts/model calls, context changes and legacy incomplete records
are refused. Local OS locks prevent simultaneous execution of an owned attempt.
Guidance-enabled journals also retain the mission event cursor, consumed event
IDs and the guidance blocks actually injected by that execution. Restoration
merges those IDs into the shared worker ledger and reads only later events;
previous blocks remain private audit context and are not injected again.
The durable guidance reader fails closed on malformed or unavailable events.
Its private history is limited to 4,096 visited mission events and 512 KiB;
overflow is refused before another model invocation. Retries within one model
round retain the same injected request. Attempt locks remain held through
Artifact reporting and release on setup, reporting and cancellation failures.
See [ADR-0115](../../../docs/architecture/decisions/0115-private-runner-resume-images.md)
and `tests/integration/test_recovery_process.py` for actual kill/restart evidence.

Harness DTOs and bounded repair transactions live in `harness_types.py` and
`harness_repair.py`. `harness_loop.py` owns each resumable turn's counters and
pending queue; `harness_service.py` preserves public imports and model/tool
adapters. A final no-tools summary is also checkpointed and charged to budgets.

Durable desktop tool feedback uses `tool_feedback.py` with a frozen policy and
the saved visible result prefix. The normal loop and a successful-receipt gap
apply the same limiter, so restarting preserves both feedback and its remaining
character budget. Its scoped executor retains gateway permission/hook services
and leaves the process-global result counter to compatibility callers.

## Default verification boundary

`controller.py` checks registered content bytes for availability, exact size and
SHA-256. Zero-byte outputs are valid when those facts match. The default
`artifact-set.v1` policy proves no code correctness or external test execution.
It never executes objective `RUN:` or `VERIFY:` markers. `loops.py` retains the
compatibility parsers and low-level `run_verify_command` helper; that helper
does not authorize execution or create trusted test Evidence on its own.
Future test producers need an explicit Contract boundary and attributable
reports before the Runner completes its lease-bound artifact set.

The opt-in `trusted_tests` package supplies an operator-frozen repository/test
profile and a signed five-Artifact bundle; see
[ADR-0121](../../../docs/architecture/decisions/0121-trusted-local-test-bundles.md).
`HarnessResult.artifacts` carries immutable candidate bytes. Runner validates
and publishes the whole bundle, registers every entry under the live lease,
then completes with all refs. Its deadline and heartbeat cover the Harness,
producer and publication together. A shared monotonic abort/deadline fence is
checked before and after heartbeat calls, after execution and publication, before each registration, and before
the terminal command dispatch. A port returning after cancellation cannot
dispatch another Artifact registration or completion through the Runner.
Default executors still publish their single
text output. A publication interruption preserves the existing recoverable
RUNNING lease, and a partial bundle cannot establish Evidence.

Terminal HTTP dispatch cannot be rolled back by cancelling its local await.
If a heartbeat loses its lease while completion is in flight, or the local
deadline expires, Runner waits at most another 500 ms for the actual transaction
ACK. Only that response confirms the exact lease-fenced completion and refs;
missing/rejected ACKs raise `RunnerTerminalAckUncertainError` and never invent
success or send a second failure command. Cancellation after terminal dispatch
propagates and logs that Mission Control may already have committed. Recovery
and independent verification must use the durable state.

Cancellation drain is bounded to 250 ms, with up to 250 ms for a best-effort
final state flush. Normal successful execution flushes its state before
publication under the supervised deadline. These are cooperative async bounds,
not a hard wall-clock process limit: synchronous callbacks cannot be preempted,
and ports retain responsibility for their process cleanup. An already dispatched
HTTP command can still commit after local cancellation. Regression tests in
`tests/services/test_runner_supervision_regressions.py` and
`tests/integration/test_runner_supervision_sqlite_http.py` cover delayed real
SQLite completion ACKs, stale leases, cancellation-resistant ports and late
publication rejection; the existing reporting cancellation tests retain the
recoverable RUNNING behavior.

## Experimental OpenSandbox HTTP port

`../opensandbox_backend.py` and `../opensandbox_protocol.py` implement a default-off,
standalone OpenSandbox 1.1.1 experiment. They do not change platform sandbox
selection, desktop execution, Mission state, recovery, or verification authority.
The explicit caller owns Contract authorization. The port requires authentication,
HTTPS or loopback HTTP, pinned Linux workloads, deny-default networking, bounded
commands with independently polled terminal status, and confirmed destruction.
File transfers are bounded, digest-checked, and refuse traversal and symlinks;
this serial PoC does not establish a filesystem boundary against malicious
concurrent background processes. There is no local fallback or SDK dependency.
See [the experiment guide](../../../deploy/opensandbox/README.md) for opt-in
commands, the real Linux Docker gate, limitations, and rollback.

The controller tests cover CAS bytes and inert objective markers.
`tests/integration/test_default_task_acceptance_sqlite.py` exercises the real
controller through authenticated HTTP Evidence admission and durable SQLite
transitions, including empty outputs.

## Experimental strict provider budget composition

`strict_budget.StrictBudgetHarnessFactory` is an explicit in-process factory for
`DesktopTaskClaimedWorkResolver`. It privately binds the authenticated Runner and
actual claim identity, then uses `strict_provider.BudgetedModelPort` for direct
HTTP calls. It is absent from the default Desktop/CLI/Chat and standalone Runner
service wiring. The operator must supply the ledger, fixed provider profile and
private credential; there is no fallback to the existing adapter.
The optional constructor `limit_usd` is operator-only and must match an existing
lower account limit. Omitting it selects the actual Contract ceiling; a mismatch
with a frozen account fails closed. Claim context and model metadata cannot
override that limit or private scope. Factory construction may happen while
LEASED; reservation and dispatch occur only when the model is invoked.

The factory accepts only manual/chat desktop roots, refuses resume and streaming,
and sets `FunctionCallingHarness(model_retries=0)`. The compatibility default
remains one transient retry. Harness generates
`harness:model:{iteration}:response` and `harness:model:{iteration}:summary`;
bounded retries keep the same identity. These are request metadata, not
authorization or a new checkpoint schema. The ledger rechecks the RUNNING lease
before dispatch. There is no planner, reflection or delegation. Runner's
aggregate ModelUsage remains telemetry and cannot settle the ledger.

Tools default to an empty tuple. Injected tools require an explicit operator
allowlist, complete capability mapping, matching WorkUnit requirements and unique
unscoped Contract grants. Scoped handlers and known model/delegation or remote
agent/MCP capabilities are refused. Accepted handlers must be trusted local
operator code performing no model/network calls. Names and capability strings
do not prove arbitrary handlers satisfy that restriction. This does not sandbox
Python or establish a general-purpose extension security boundary.

The profile reserves a reviewed maximum billing input window plus its enforced
completion limit per HTTP request. Its assumptions and unknown-usage behavior
are documented in [the provider guide](../strict_provider/README.md) and
[ADR-0124](../../../docs/architecture/decisions/0124-strict-in-process-provider-budget-profile.md).
`tests/integration/test_strict_budget_runner_sqlite.py` uses a real loopback HTTP
peer, SQLite ledger, Runner claim/reporting, CAS and independent byte verifier.
Its successful terminal result proves output integrity, not code correctness or
live supplier billing. Insufficient funds prevent the next HTTP request, and
trusted late settlement cannot revive cancelled work.
Focused Harness tests are `tests/services/test_harness_model_operations.py` and
`tests/services/test_strict_budget_harness.py`.
