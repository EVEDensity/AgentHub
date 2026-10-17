# ADR-0127: Evaluate one anonymous browser read behind a dedicated Native profile

> Status: accepted\
> Owner: Runner and browser-tool maintainers\
> Last reviewed: 2026-10-18\
> Scope: M2b explicit Playwright adapter, attempt authorization and diagnostic output

## Context

The accepted [composition plan](../../roadmaps/composable-agent-platform-plan.md)
selects Playwright as a browser capability. Existing browser helpers share a
process-global context, and the Desktop MCP bridge imports tools into a shared
session. Neither establishes the lifecycle and capability boundary required for
a controlled WorkUnit. Adding another Agent framework would not fix that scope.

## Decision

Add an explicitly selected `BudgetedBrowserReadHarnessFactory`, reusing actual
Mission/Contract revision/WorkUnit/lease validation and the strict direct model
port from [ADR-0124](0124-strict-in-process-provider-budget-profile.md). It accepts
exactly one required `browser.read` capability, with a unique Contract scope
equal to the operator profile digest and canonical origin list. Host repository
scopes, resume and streaming are refused. Build performs no model request or
browser launch; execution remains under the existing Runner supervision.

A single Native Harness receives only `browser_read(url)`. The profile fixes the
Node executable, worker source/lock digests, SDK/upstream version, origins and
limits. Runtime changes after selection fail. Model arguments cannot choose
origins, headers, credentials, launch parameters or scripts. Model retries remain
disabled, and every direct model request uses the existing budget ledger.

Place the independent Node worker in `services/node/browser-read`. This runtime
boundary allows a pinned upstream JavaScript browser library without adding its
Python SDK to Mission Control or sharing the user's everyday browser. The private
JSON-file/stdout protocol is not labeled MCP. A future official MCP transport
requires its own initialization, capability and lifecycle conformance.

Use Playwright 1.61.1 at upstream commit
`39e3553a4f283a41134d75d7e404484bd9e6865a` and its matching Chromium 1228,
149.0.7827.55. npm lock integrity pins the packages. Each read launches a new
browser and nonpersistent context; there is no cross-read cookie/session reuse.
The first Python process adapter supports Linux only and needs pidfd and a
readable same-UID process inventory. An optional worker/cache is never installed
automatically or selected by default clients or production service configuration.

Only one anonymous main-frame document GET is supported. Disable page scripts,
downloads, Service Workers and WebSockets, abort subresources and additional
navigations, and accept HTML/plain text with successful HTTP status. Every
redirect fails. Playwright routing has redirect caveats, so the handler uses
`route.fetch(maxRedirects=0, maxRetries=0)` before fulfillment; checking the final
URL alone is insufficient. Return bounded visible text/title and actual HTTP,
version, digest, truncation and cleanup observations.

One total monotonic deadline covers model and tool work. The process adapter
uses a private random environment marker, sanitized child environment and bounded
output/filesystem observation. Chromium launches into a separate POSIX process
group; killing Node alone is insufficient. Cleanup rechecks process identity,
start time and exact ownership marker and signals through pidfds. It does not
use worker-supplied PIDs or kill foreign process groups. Unknown inventory or
unconfirmed cleanup prevents success. Cancellation permits bounded owned cleanup
and fences late results from Artifact publication.

The inventory excludes processes born strictly before the Python parent: a new
UUID passed only to subsequently spawned workers cannot be inherited by an older
process. Processes in the same clock tick remain candidates. An unreadable
candidate keeps cleanup unknown, while independently confirmed owned processes
are still signalled. Browser-owned profile symlinks count toward filesystem
entry/metadata limits without following their targets; this does not change the
stricter trusted-test snapshot policy.

After at least one successful final read and confirmed cleanup, the Harness
returns two Artifacts: model text as the existing `test-result` kind and a
`browser-execution-report.v1` diagnostic. Runner registers them under its active
lease, then the independent byte Verifier and Mission Control retain acceptance.
The dedicated Contract requires both kinds through existing `artifact-set.v1`.
The diagnostic is not a TestRunReport or factual verification verdict. Failed
Runner results retain the existing no-publication behavior.

## Verification and limits

The selected Linux job installs the pinned optional runtime and runs real
Chromium against local HTTP fixtures. It requires successful static reading,
empty content, Unicode truncation, cookie separation, blocked scripts/resources,
redirect/HTTP/download failures, process-observed cancellation, post-request
deadline cleanup, concurrent ownership separation and real Native/model-fixture
HTTP/budget/SQLite/Runner/CAS/Verifier admission. An enabled empty/skipped gate
fails. Cross-platform unit tests do not substitute for that Linux evidence.

This remains an operator-controlled-origin experiment. Origin matching does not
pin DNS or provide OS network isolation. A GET can have side effects, and process
polling limits are not hard memory/CPU or network-byte quotas. SPA interaction,
credentials, arbitrary internet or hostile-page execution, native Windows
execution and production isolation remain outside scope. A deterministic model
fixture proves protocol/byte-integrity behavior, not research accuracy or live
supplier billing. Existing M1 production gates remain in force.

There is no database migration or new Mission state. The report schema is additive.
Disabling this factory stops new selection; historical Artifacts/Contract/Evidence
keep their meaning. Removing the optional worker/dependency does not enable a
fallback browser or ordinary shell path.

## Primary references

- [Pinned Playwright source and license](https://github.com/microsoft/playwright/tree/39e3553a4f283a41134d75d7e404484bd9e6865a).
- [Browser contexts](https://playwright.dev/docs/api/class-browsercontext).
- [Explicit redirect and retry controls](https://playwright.dev/docs/api/class-route#route-fetch).
- [Browser process launcher](https://github.com/microsoft/playwright/blob/39e3553a4f283a41134d75d7e404484bd9e6865a/packages/utils/processLauncher.ts).
- [Linux initial environment and access checks](https://man7.org/linux/man-pages/man5/proc_pid_environ.5.html).
- [Linux PID descriptor identity](https://man7.org/linux/man-pages/man2/pidfd_open.2.html).
