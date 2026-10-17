# Frontend

The Next.js frontend is a projection and command surface for the control plane.
It must not manufacture domain state when a backend is unavailable.

## Areas

- `app/`: admin and application routes.
- `components/`: reusable UI and workflow views.
- `stores/`: transport state and cache; no durable business truth.
- `types/`: API-facing types; prefer generated or contract-backed types for new
  Mission and WorkUnit endpoints.
- `e2e/`, `__tests__/`: browser and component verification.

Demo fixtures are permitted only in explicitly named development stories or
tests. Production failure must render an honest unavailable/error state.

## Mission chat execution

Chat admission returns after `POST /api/v1/chat/mission` persists the Mission
and its PENDING WorkUnit. The execution banner reads the workspace-authorized
`GET /api/v1/missions/{missionId}/execution-status` projection. It distinguishes
waiting for a matching Runner, waiting for claim/start, running behind a live
lease, independent verification, governance, and terminal outcomes. An enabled
catalog binding or connected SSE stream never becomes an execution signal.

Submission and read errors remain visible. Status retries reread the same
Mission; stream reconnects deduplicate durable event IDs. Cancellation issues
the server command and shows success only after acknowledgement. Pending rule
confirmations expose explicit confirmation and cancellation buttons. Expired
leases cannot display active execution.

The main conversation reads native Sessions and their actual chat Mission
associations from `/api/v1/sessions`. Explicit personal-workspace selection uses
Mission Control's authenticated `/api/v1/access/whoami`, with visible read errors;
it does not infer scope from local credentials or the Go platform catalog. Selecting a saved Mission
only observes it; it does not repeat admission or execution. Server task target
excerpts are labeled separately from original requests. Historical Sessions are
read-only here; editing a new draft creates no Session. The server admission
process can leave an empty Session on a later failure or pending rule. Legacy rows
remain unchanged and do not acquire inferred workspace ownership. Scope
changes clear local bytes and cursors, including A→B→A late responses.

See [ADR-0116](../docs/architecture/decisions/0116-runner-contact-and-chat-execution-status.md).

When adding a user workflow, document its command, loading, retry, cancellation,
and permission states and cover the primary path with an end-to-end test.

## Decision inbox

The admin Decision inbox is the human command surface for pending Mission
Control Decisions in the selected workspace. It reads
`GET /api/v1/missions/decisions` and resolves an item through the versioned
`POST /api/v1/missions/{missionId}/decisions/{decisionId}/resolve` command.

- Loading and workspace changes fetch server state and cancel stale reads.
- Read or command failures remain visible and can be retried; no local Decision
  fixture or synthetic success is used.
- Commands require a rationale and submit the Decision's `expectedVersion`.
- A version conflict refreshes the inbox so the operator must decide again
  against current state.
- Mission failure requires explicit confirmation. Controls remain disabled
  while a command is in flight.
- The API enforces human-only access and workspace authorization; the frontend
  does not infer or replace those permissions.

## Current command support

Mission chat currently admits plain text. Nonempty attachments, file/message
references and execution permission overrides are rejected visibly before
admission; selected input is kept so it can be corrected. The server also
rejects unknown request fields. The permission hint describes server Contract
ownership, and every send creates a Mission rather than a local-only message.
Failed admission preserves the draft. Acknowledgement clears only unchanged
submitted fields, so typing or adding context while a request is pending is safe.

The deployment dialog is an explicit unavailable notice until a real deployment
command is supported. Workflow definitions remain editable; test execution,
history and execution analytics are unavailable until backed by Mission data.
MCP, Agent version and billing reads show transport errors instead of fixtures.
Failed add/remove/rollback/tool commands never update the view as if they had
succeeded. MCP connection probing is unavailable until the gateway exposes a
supported probe; the browser cannot infer connection from a configured URL.

## Terminal availability and shared UI

The shared admin header reports that runtime metrics are not connected. It has
no fabricated health indicator, throughput, latency, success rate or online-agent
count. Those require a separate real monitoring contract.
UI-3b also removes the configured default Agent ID/fallback model name and the
fixed token/window/percentage displays from this header. It explicitly says
execution model, token use and context occupancy are not connected. It does not
read checkpoints or reinterpret configured Agents as observed execution. The
workspace, user, menu and navigation remain actual UI context. Empty Missions,
switching actual Missions and configuration-read failures must retain this
truthful state without introducing telemetry requests or Mission commands.

The sidebar activity panel starts empty and records only actual browser navigation
clicks, with browser timestamps and at most 200 entries. It has no pseudo-command
input, invented system/version messages or execution/state/tool tabs. These local
activities are explicitly separate from server execution logs.

The admin Terminal is an unavailable notice with no command input or execution
action. No terminal service is connected: it does not execute browser or server
commands, inspect files or environment variables, or manufacture health,
process, agent, package-manager, Git or network results. Its component tests
cover the notice and absence of execution and transport side effects.

The bundled Desktop admin view uses this same Next.js UI. The Mission panel now
adopts a shadcn/Radix Dialog and locally loaded Monaco for explicitly requested,
read-only registered Artifact previews. The bounded human/workspace-authorized
API verifies bytes before returning text; the frontend rechecks identity,
attempt, size and SHA-256. Empty, denied, unavailable and excessive content stays
visible as such. Scope changes and cancellation discard old responses. Preview
integrity does not mean Mission acceptance. See
[the Artifact guide](components/artifacts/README.md) and
[ADR-0128](../docs/architecture/decisions/0128-registered-artifact-text-preview.md).

`npm ci` and `npm run build` prepare pinned local editor assets and notices;
missing resources fail the build. Material Symbols use a pinned local Fontsource
font with its distributed OFL-1.1 notice, without a Google Fonts request.
Mission API rewrites use build-time
`MISSION_CONTROL_URL`, falling back to `API_BACKEND_URL` even under the generic
Go API profile. Docker exposes the corresponding build argument. Desktop's build
explicitly pins that path to loopback and restores an inherited override afterward.
The real browser gate uses a disposable SQLite/CAS HTTP fixture, rather than
fulfilling Mission requests with browser fixtures. Packaged WebView2 validation
remains a separate release requirement.

The admin/Desktop Mission assistant now uses a lazily loaded assistant-ui
external-store view over the existing Mission commands and bounded ledger
observations. It preserves failed/edited drafts, pauses observers on close,
requires server cancellation acknowledgements and displays independent
verification/governance states. The library supplies no model loop, tool grant
or alternative acceptance. This UI-2a slice replaces the old admin public-chat
proxy and simulated typing. UI-2b also migrates the main conversation to native
Session/Mission observations, without the old messages/Task/DAG store. See
[the chat guide](components/chat/README.md),
[ADR-0130](../docs/architecture/decisions/0130-native-session-mission-observation.md) and
[ADR-0129](../docs/architecture/decisions/0129-mission-assistant-ui-projection.md).

UI-3a uses pinned local xterm 6.0.0 for explicitly opened, byte-verified `log`
Artifacts. Controls are made visible before a bounded plain-text snapshot enters
the read-only frame; no terminal transport, addon or execution authority is
connected. Real log availability depends on producers registering an Artifact.
See [ADR-0131](../docs/architecture/decisions/0131-registered-log-artifact-snapshots.md).
Complete original transcript recovery, live logs and execution remain targets in the
[UI component plan](../docs/roadmaps/composable-agent-platform-plan.md#511-web-与-desktop-ui-组件target).
Keep
Mission state, authorization and acceptance on the existing control-plane APIs;
shared components own presentation and interaction only.

Run `npm test` and `npx tsc --noEmit` for the frontend checks. The management
failure tests cover network and HTTP failures, while admission tests ensure
unsupported input does not create a Mission.
