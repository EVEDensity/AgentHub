# ADR-0130: Recover native Session Missions as bounded observations

> Status: accepted\
> Owner: frontend and Mission Control maintainers\
> Last reviewed: 2026-10-18\
> Scope: UI-2b native main conversation, read-only Session directory and Mission association

## Context

The main page admitted real Missions but listed legacy `/api/chat/sessions`,
loaded legacy messages and Tasks, and merged Session events and Mission events
into a browser store keyed by the legacy view ID. It discarded ledger IDs and
event types and generated message timestamps. A cached view-to-native Session
link did not restore a Mission after refresh or establish workspace authority.

Replacing only its renderer would preserve these incompatible histories.
Session `message.created` events contain at most an initial message excerpt
and their historical writes are best effort. Mission objectives can contain
rule or archivist preprocessing. Neither is a complete original transcript.

This follows [ADR-0129](0129-mission-assistant-ui-projection.md) for the shared
dialog and the [component plan](../../roadmaps/composable-agent-platform-plan.md).

## Decision

Make the main page an explicit native Mission profile. Retain the existing
Next.js and pinned assistant-ui presentation. Render literal, bounded ledger
observations with stable event IDs through the shared external-store adapter.
Keep controlled submission, confirmation, cancellation and execution status on
the existing authenticated Mission commands. The UI does not execute model or
tool calls or decide acceptance.

Read the authenticated personal workspace scope from Mission Control's existing
`GET /api/v1/access/whoami` and require explicit selection. Its principal must
match the logged-in identity. Read failures remain visible and retryable; they
do not manufacture a default workspace or infer one from a stored user ID.
The separate Go platform directory does not establish Mission Control scope;
team workspace discovery remains another contract. Identity/role/token, workspace,
Session and Mission selections replace the disposable view with a monotonically
changing scope. Late responses cannot restore previous bytes, even after
A→B→A selection. Logging out clears credentials and unmounts all observers.

Expose three human/workspace-authorized, read-only versioned projections:

- `GET /api/v1/sessions?workspaceId&limit&offset`: native Session metadata,
  at most 50 entries, newest creation first. `hasMore` and `nextOffset` are
  explicit. Offset paging is a live directory rather than a snapshot; insertion
  can cause overlap, which the client deduplicates. Refresh starts over.
- `GET /api/v1/sessions/{id}?workspaceId`: the same minimal Session metadata
  after checking its durable workspace assignment.
- `GET /api/v1/sessions/{id}/missions?workspaceId&limit&beforeId`: at most 20
  actual chat Missions whose source Session and workspace both match. Creation
  time and ID give a stable descending keyset. The cursor must belong to that
  same relation; foreign, missing and non-chat cursors are rejected. SQLite
  comparisons retain UTC and microsecond chronology; PostgreSQL uses native
  timestamps. A new read repository owns this query, without new storage.

The [read schema](../../../platform/contracts/v1/session-history.schema.json)
contains metadata and at most 1,000 Unicode code points of each Mission
objective, with an explicit truncation flag. It exposes no Contract grants,
credentials, CAS addresses, Event payloads or legacy message bodies. Responses
are `no-store`. Authorization runs before cursor or target queries. Legacy
rows without a durable workspace remain unassigned and are not silently
migrated, listed, inferred or deleted.

The main page offers a new local draft, server Session directory, Mission
selection, status/observation and cancellation. Selecting a saved Mission
starts only GET/status/SSE reads; it never repeats admission, starts execution,
manufactures an acknowledgement or restores an acceptance result from cache.
Objectives are labeled as server task targets, rather than user messages.
Historical Sessions are an observation profile: further submission requires a
new view. This avoids treating an incompletely loaded history as idle work.
Within a newly mounted view, a real terminal status permits the next request.
This UI rule does not create a server-wide concurrency lock.

A new local draft issues no server command. The existing admission process
creates Sessions; a later admission failure or pending rule can leave a real
empty Session, because Session creation is not atomic with Mission creation.
Only an admission acknowledgement adds the local request receipt and clears unchanged
submitted fields. New text or attachments entered during admission survive.
Unsupported attachments are retained and rejected visibly before any command;
they are not silently dropped. IME composition does not submit. Errors remain
visible and POSTs are never automatically retried. A transport failure, timeout,
malformed acknowledgement or HTTP 5xx leaves the main view's admission locked
as uncertain, retaining its draft. The operator can refresh/read the directory
to check committed work; a different task requires explicitly creating a new
view. A definite HTTP 4xx rejection other than 408 permits correction. This
client latch does not provide server idempotency. Existing pending-rule and cancel acknowledgement
semantics remain authoritative.

Native Session IDs and restored Mission IDs are explicit transport inputs,
separate from a legacy view/cache key. They are not persisted as inferred alias
links in this profile. Command success, errors and cleanup use a scope epoch so
an old response cannot unlock or overwrite another A request after A→B→A.
Next routes v1 Sessions and access to the same Mission Control owner as chat and Missions
under both API profiles and the existing Desktop build configuration.

Directory caches hold at most 500 Sessions or 200 Missions with an omission
notice. Reads bound response bytes, validate identity and pagination, reject
redirects, time out, and cancel stale requests. The existing observation limit
of 200 events/1 MiB and 4,000-code-point row truncation remains visible. The
server WorkUnit event feed is itself a recent bounded window. Refresh restores
selectable Mission associations and observations, not complete disconnected
text, original messages, pending-rule controls after refresh or an execution checkpoint.

## Verification, limits and rollback

API, contract and real database checks cover scope, actual source association,
legacy exclusion, stable paging/cursors, time zones and microseconds, objective
bounds and unchanged storage. Frontend checks cover validation, read retry,
scope/ABA isolation, native-session admission, zero-POST observation and existing
draft/attachment/cancellation rules. Real browser checks use the production
Next proxy, API, SQLite and existing test Harness; Mission reads are not replaced
with successful browser fixtures. A fixed ModelPort proves transport, not model
quality or an independent PASS.

The old page's legacy share/rename/delete, Task/DAG, deployment, file editing
and message action controls are absent from this native profile. Their helper
modules remain available to other callers; their old records are unchanged.
They require their own supported contracts before reintroduction here. The
public bot route and admin dialog retain their separate scopes. No new package,
database schema, execution grant or state transition is added. Full original
transcript persistence, real logs and packaged WebView2/IME/offline/installer
acceptance remain separate work.

Rollback may disable this native presentation with an explicit unavailable
notice. Keep the read contracts and prior cursor/stream correctness fixes.
Do not restore inferred workspace ownership, synthetic messages or a mixed
legacy/Mission execution view as a fallback.
