# Experimental anonymous browser reads

This operator-only Linux profile provides one `browser_read(url)` FunctionTool
to the existing Native Harness. Every read launches a fresh Node worker and
Chromium, with JavaScript, downloads and service workers disabled. Only the
initial main HTML/plain-text document GET is admitted; redirects, secondary navigation and all
subresources are rejected. It is not enabled by default CLI, Desktop, Chat or
Runner service wiring, and it adds no model loop or installation path.

`BrowserReadProfile` freezes explicit absolute Node/worker paths, canonical
origins, limits, Node bytes, worker/policy/lockfile bytes, Playwright 1.61.1 and
Chromium 149.0.7827.55 identities. Runtime hashes are checked before every read.
The supported URL subset is visible ASCII, at most 2048 characters, with no
credentials, fragments, backslashes or dot segments. Origins are exact canonical
HTTPS origins; literal loopback HTTP requires an explicit test-only option.
Origins do not restrict paths on an authorized site. The optional
`browser_cache_path` must point to an operator-installed browser cache for a
normal host install: the temporary HOME intentionally hides a user's default
cache, cookies and browser profile. Nothing downloads a missing browser.

`LocalBrowserReadBackend` refuses other operating systems. Its dedicated process
backend reuses the test-process result value, but uses its own directory budget
and does not use the legacy process-group termination path. Node admission is one
synchronous `Popen` call with a fixed argv and a minimal environment: temporary
HOME/TMP/XDG directories, a standard PATH/locale, an optional explicit browser
cache and this read's UUID ownership marker. Model credentials, Runner tokens,
proxy settings and NODE_OPTIONS are not inherited. The worker input is private
temporary JSON; no model-controlled command, executable, environment or path is
accepted. Process output is bounded to 1 MiB and the owned directory to 128 MiB.
Filesystem polling detects excess; it is not a kernel disk or memory quota.
Chromium profile links, including dangling Singleton links, count as entries
and contribute their own `lstat` byte size; their targets are never followed.
An already disappeared child directory during scanning is an expected cleanup
race; missing roots, unreadable paths and other I/O failures still fail the budget.

The parent scans Linux `/proc` for the exact ownership marker and current UID,
ignoring zombies and processes born strictly before this Python parent. The
marker is generated only by this parent and injected into subsequently spawned
children, so earlier processes cannot have inherited it. Equal birth ticks
remain eligible. It opens pidfds and rechecks process start identity and marker
before signalling each matching process, including detached Chromium descendants
created before `started.json` exists. It never authorizes signals from a worker-
reported PID, never runs broad `pkill`, and never signals an entire group.
Unreadable eligible same-UID process state yields unknown cleanup, not
confirmation. It does not prevent signalling other positively identified owned
processes. Diagnostic logs contain static categories, counts, errno and limit
flags, never process environments, page text, URLs or raw exception messages.
This requires a host where the parent's same-UID processes are visible through
`/proc` and Linux pidfds are available. It is cooperative cleanup of trusted
runtime code, not a security boundary against an actor hiding or altering its
process identity. Repeated cancellation cannot cancel the tracked cleanup task.

The read deadline includes runtime verification and execution. Each process
cleanup can wait up to five seconds, followed by a bounded parent verification
using the profile cleanup limit. Harness cancellation drains its Native worker
for 250 ms; cleanup may finish afterward, and no late result can become a new
read or Artifact. These are cooperative async bounds; synchronous process
creation and filesystem calls cannot be preempted by asyncio.

`BudgetedBrowserReadHarnessFactory` binds the actual claimed Mission, Contract
revision, WorkUnit, attempt, Runner and lease. It requires exactly `browser.read`
with the operator profile digest and origins, rejects host repository scopes,
resume and streaming, and reserves model funds only when model calls occur.
`BrowserNativeHarness` records the actual worker result and refuses success with
no successful read, a final failed read, unknown cleanup, or an output error.
Earlier observed failures may be followed by a successful read. Successful
execution returns model text (`test-result`) and an execution observation report
(`report`); the report is registered last through the existing Runner lease.
Failure results are not published by the current Runner reporting protocol.

Report bytes and `artifact-set.v1` verification prove output integrity only.
They do not prove page truth, task correctness, malicious-site safety, general
read-only semantics of GET, DNS/network isolation, or hostile-process protection.
No user browser, cookies, authentication state or files are mounted. External
write/credential actions require a separate future capability and approval
design. Real Chromium coverage is an explicitly enabled Linux conformance gate;
the ordinary Python adapter tests use controlled process/port fixtures.
