# ADR-0129: Bind the shared assistant dialog to Mission projections

> Status: accepted\
> Owner: frontend and Mission Control maintainers\
> Last reviewed: 2026-10-18\
> Scope: UI-2a admin/Desktop conversation, bounded observation and scoped SSE resume

## Context

The shared admin assistant used a public gateway request, an invented model
selector and timer-based typing. It did not use the Mission admission,
authorization, execution status, governance or cancellation contracts. Replacing
its renderer alone would preserve those separate execution semantics.

The accepted [component plan](../../roadmaps/composable-agent-platform-plan.md)
selects assistant-ui as presentation over an external store. The main
conversation already combines native Mission admission with legacy history,
session events and other views; migrating that entire page remains another
slice. This decision first replaces the shared admin/Desktop dialog.

The real scope-switch flow also exposed that Tailwind's content discovery
omitted `app/`: the header's `hidden md:block` never generated its responsive
rule and hid workspace selection at every width. Include App Router sources
in discovery and verify selection through the visible desktop control.

## Decision

Keep existing `useMissionChat`, execution status and human Decision commands as
the transport/authority boundary. Submit text through `/api/v1/chat/mission`,
confirm/cancel pending rules through the existing commands, and cancel a
Mission only through its authenticated command. Next explicitly routes v1 chat
and Mission paths to the same build-time Mission Control endpoint under both
Go and legacy profiles, including Desktop's baked loopback address.

Use exact `@assistant-ui/react` 0.15.29. The official registry provenance
declaration associates its archive with commit
`a46d6634e7a75eddd85d51415b1a615886d4ad0e`; this source check is not a claim of
independent signature verification. Its [source manifest](https://github.com/assistant-ui/assistant-ui/blob/a46d6634e7a75eddd85d51415b1a615886d4ad0e/packages/react/package.json)
supports React 18/19. The lock records archive integrity and all transitive
versions. Its minimum Zustand version requires the compatible 5.0.14 → 5.0.15
patch in this repository. Other existing package versions remain unchanged.

Lazily mount ExternalStoreRuntime with a deliberate row converter and only
Thread/Message primitives. Set `isDisabled: true`, `isRunning: false` and
`unstable_enableToolInvocations: false`. The required `onNew` rejects library
commands; actual submission/cancellation stays in existing application controls.
No Cloud instance, remote history, tool, voice, attachment, MCP App or generative
UI adapter is configured. Transitive `assistant-cloud` and `safe-content-frame`
are present; this path does not instantiate them or require their remote assets.

Retain a controlled input because the library's default Composer clears it
before an asynchronous command is acknowledged, and its Cancel does not await
the application acknowledgement. Only a durable admission acknowledgement adds
a user receipt and clears an unchanged draft. Failure retains the entire draft;
new typing during a request is never cleared. There is no automatic POST retry.
A timeout or interrupted response may already have committed a Mission, so the
UI asks the operator to check Mission state before another action. This adds no
admission idempotency guarantee.

An active, pending or unreadable current Mission prevents another submission.
The input remains editable. Actual status distinguishes no Runner, live lease,
expired lease, independent verification, governance and terminal outcomes. A
clock removes an executing label when its observed lease expires even if the
next GET stalls. A Mission waiting for a Decision is displayed independently of
a WorkUnit still in VERIFYING. The existing Decision inbox owns resolution.
Model text and execution/stream completion never establish acceptance.

Closing pauses observers and unmounts the library runtime; it does not cancel
accepted work. Reopen within the same mounted scope retains local draft,
receipts, current Mission and bounded observations. Identity/workspace changes
synchronously remount the whole projection. Native session IDs are retained
only in this mounted hook, without persisting an alias for this profile.
This does not promise full transcript recovery after refresh. Existing main
conversation consumers keep their transport-link behavior.

Render source as escaped React text. Observations preserve their ledger ID,
Mission ID and original event type; they do not become library tool executions.
Keep at most 200 observer events/1 MiB of text and 200 visible rows. Long rows
are explicitly truncated after 4,000 Unicode code points. The server's existing
feed includes only a bounded recent WorkUnit window; this surface is a live
observation view, not a lossless historical transcript. UI omissions are visible.

Bound JSON and individual SSE frames at 256 KiB and coalesced chunks at 2 MiB.
Reject foreign/absolute stream URLs, wrong-Mission frames, missing IDs in this
profile, malformed status/workspace shapes and late responses. Disable redirects
and response caching. Reconnect uses the last observed durable ID plus bounded
deduplication. These controls do not create a new business state source.

The Mission SSE response carries `Cache-Control: no-cache, no-transform`.
Real TCP checks found that Next's gzip middleware otherwise buffered small
indefinite streams even though the Python producer sent them promptly; a bounded
stream released all frames only on close. `no-transform` disables that response
transformation while retaining normal compression for the rest of the site.
The real browser gate must observe text before closing the live stream.

## Scoped cursor correction

The existing HTTP resume lookup treated an event's sequence as global. WorkUnit
and Mission sequences are independent, so a WorkUnit sequence 20 could skip
future Mission events 6–20. A foreign Mission's event ID could do the same.
After authorization, the HTTP route now resolves a cursor only from the current
Mission's Mission aggregate. WorkUnit, foreign and unknown IDs safely replay
from zero; stable event IDs deduplicate observations. Internal callers that
explicitly omit scope retain the old lookup behavior. No event order, storage
schema, domain transition or historical data is rewritten.

## Dependencies and notices

The initial install adds 65 package locations: 64 MIT and
`secure-json-parse` 4.1.1 under BSD-3-Clause. The existing audit baseline remains
20 findings; this is not a general dependency-security clearance. Preparation
walks actual parent-relative dependency directories, rather than assuming all
versions are hoisted, and ships the corresponding notices with local assets.

`use-composed-ref` 1.4.0 declares MIT in its pinned
[manifest](https://github.com/Andarist/use-composed-ref/blob/ea2c9d969f2f90a349f4c99338b806423824cea2/package.json)
and registry metadata but omits an upstream LICENSE and author/copyright data.
Preserve the actual package manifest and a clearly labeled declaration notice
with the [SPDX reference template](https://spdx.org/licenses/MIT.html). Do not
invent a copyright holder/year or describe that template as an obtained upstream
notice. This known source-notice gap remains documented for distribution review.

## Verification and limits

The [browser fixture](../../../tests/integration/mission_chat_http_fixture.py)
uses production routes, real SQLite, catalog bindings, transactions, CAS and
the real Harness with a fixed test ModelPort. It creates its first Mission
through browser admission, not seeded success. A test-only driver uses real
commands to establish leases, publish text, complete execution and trigger a
pending Decision. It never emits PASS Evidence or executes a paid provider.
Only fixture authentication and unrelated platform reads are substituted.

[Browser checks](../../../frontend/e2e/mission-chat.spec.ts) cover admission,
waiting, actual text, VERIFYING, lease expiry, Decision navigation, pending
confirm/cancel, real 403 and network failures, pause/resume, replay deduplication
and workspace clearing. API fixture and [mixed aggregate cursor checks](../../../tests/integration/test_mission_sse_cursor_sqlite.py)
use real SQLite. Frontend checks exercise the actual assistant-ui package and
independent transport/status hooks. Both real browser suites must execute with
zero skips; files run serially because each owns the same baked loopback port.

Web browser results do not establish packaged WebView2/IME/offline/installer or
signed Desktop release readiness. No Tauri commands, backend model selection,
Contract grants, tool capabilities, acceptance policy or database migration are
introduced. Main-conversation migration, real log rendering and external
executor comparison remain later slices.

Rollback disables this dialog with an explicit unavailable notice and removes
the new library/asset changes while keeping the existing main conversation.
Do not restore the old simulated typing or public-proxy execution path. Keep
the scoped cursor and streaming-header correctness fixes independently.
Historical Mission records need no migration and must not be relabeled or deleted.
