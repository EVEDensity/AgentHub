# Chat presentation

> Status: implemented, bounded UI-2a and UI-2b profiles\
> Owner: frontend and Mission Control maintainers\
> Last reviewed: 2026-10-18\
> Scope: shared admin/Desktop assistant and native main Session/Mission observations

`AIChatDialog` submits text through `useMissionChat` and displays the same
workspace-authorized execution status as the main conversation. It replaces the
old public-gateway request, invented model picker and simulated typing. An
acknowledgement adds a request receipt; a failed or uncertain response preserves
the draft. Text entered while admission is pending is retained. One active or
unknown Mission blocks another submission while allowing the next draft to be
edited. Unsupported attachments, permission overrides and model choices have
no controls here.

`MissionObservationThread` lazily adapts original user receipts and ledger
observations to the pinned assistant-ui ExternalStoreRuntime. Only Thread and
Message presentation primitives are mounted. Literal React text does not
execute HTML, Markdown, tool calls or model commands. The runtime is disabled
and `isRunning` stays false; actual execution and acceptance remain in the
separate Mission projection. No cloud/history, tools, voice, attachment or MCP
App adapter is configured. Transitive cloud/frame packages are installed but
are not instantiated by this path.

Closing pauses GET/SSE observers and disposes the view runtime. It does not
cancel server work. Reopening in the same mounted identity preserves its draft,
receipts and current Mission and resumes observation. Workspace, credential,
user or role changes synchronously remount the projection and clear old bytes.
This profile keeps native session links in memory only; refreshing is not full
chat-history recovery. Mission/Decision records remain on the server.

Only server-acknowledged cancellation closes the observation. Errors and GET
retries remain visible. Lease expiry removes the executing label even when a
status request stalls. `WAITING_DECISION` takes its meaning from the Mission;
the existing Decision inbox owns resolution. Stream connection, model text and
WorkUnit completion do not produce PASS.

Transport bounds response/frame sizes, refuses foreign Mission stream URLs,
disables redirects, checks workspace/status shapes and rejects late results.
The opt-in observer retains at most 200 events and 1 MiB of text; the display
keeps 200 rows and explicitly truncates a long observation at 4,000 code points.
It identifies omissions and is not a complete historical transcript. Stable IDs
deduplicate replay. The existing server feed itself exposes a bounded recent
WorkUnit window; disconnected older observations are not guaranteed here.

The standalone Next app, Web and bundled Desktop share these sources and local
assets. Packaged WebView2, IME, offline and release checks remain separate.
The public bot route retains its separate scope. Its runtime/state cannot
establish this dialog's Mission acceptance.

## Native main conversation

`app/page.tsx` and `NativeMissionWorkspace` use explicit personal scope selection
from Mission Control's authenticated `access/whoami` projection and the v1 native
Session/Mission reads. Selecting a historical Mission
only reads its current status and bounded ledger stream. Its saved objective is
labeled as a server task target and may contain rule/context preprocessing; it
is not represented as an original user message. Historical Sessions are read-only
in this profile. A new view admits plain text, retains unsupported attachments
and failed/concurrently edited drafts, and permits another request only after
the current Mission is observably terminal. Changing selection stops local
observation without cancelling server work.

The directory has explicit loading/error/retry/more states. Its local read cache
holds at most 500 Sessions and 200 Missions with a visible limit notice. No local
Session ID alias supplies ownership. Native restoration is GET-only; it creates
no Mission or resumed execution. The old legacy message/Task/DAG store and
share/edit/deploy actions do not run on this page. Existing records are unchanged.
Full original transcript persistence and unbounded history remain separate.
See [ADR-0130](../../../docs/architecture/decisions/0130-native-session-mission-observation.md).

See [ADR-0129](../../../docs/architecture/decisions/0129-mission-assistant-ui-projection.md),
[component checks](../../__tests__/components/chat/AIChatDialog.test.tsx) and
[real browser checks](../../e2e/mission-chat.spec.ts).
