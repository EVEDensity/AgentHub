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
`capacity_saturated` without failing the active WorkUnit.

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
limits. Shell acceptance commands keep their existing declared-command channel.
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
