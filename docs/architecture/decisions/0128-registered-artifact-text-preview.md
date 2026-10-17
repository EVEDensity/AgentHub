# ADR-0128: Preview registered Artifact text through Mission Control

> Status: accepted\
> Owner: Mission API, frontend and Desktop maintainers\
> Last reviewed: 2026-10-18\
> Scope: UI-1 bounded Artifact reads, shared code presentation and local assets

## Context

The accepted [component plan](../../roadmaps/composable-agent-platform-plan.md)
requires a real read-only Artifact code view. The Mission panel previously listed
IDs; the legacy session-content viewer did not read registered execution bytes.
An installed editor dependency did not establish a transport or authorization
boundary. The default Go API routing profile also lacked Mission endpoints.

## Decision

Add `GET /api/v1/missions/{missionId}/artifacts/{artifactId}/preview`. Reuse the
existing human-role restriction and Mission workspace authorization, then require
the registered Artifact to belong to that exact Mission. The limited profile
accepts public/internal sensitivity and text media types, JSON/+json, JavaScript
and Python. It refuses other sensitivity, unsupported/binary text, invalid UTF-8,
NUL and registered or returned content above 262,144 bytes. It never truncates
content into an apparently complete preview.

Reuse the existing bounded CAS/MinIO byte exporter and independently recheck
returned type, length and SHA-256 before serializing the additive
`artifact-text-preview.schema.json` contract. Return only Artifact identity,
WorkUnit/attempt, kind, media type, size, digest and text. Do not expose storage
addresses. Success and controlled errors carry `no-store` and `nosniff` headers.
The ten-second asynchronous request deadline does not forcibly terminate an
existing storage thread; underlying I/O retains its storage transport timeout.

This read creates no Evidence, Decision, event, acceptance verdict or Mission
state transition. It can display an earlier registered attempt with its actual
attempt number; it cannot declare that attempt current or accepted. Existing
identity/workspace authorization remains authoritative; this adds no new IAM.

Route `/api/v1/missions/:path*` explicitly to `MISSION_CONTROL_URL`, falling back
to `API_BACKEND_URL` and its existing default. This rule precedes generic Go or
legacy routing. Desktop's build explicitly pins this endpoint to loopback,
overriding and then restoring any inherited Mission URL; other
gateway routes retain their profile. Deployments must configure these endpoints
at Next.js build time, as with the existing standalone bundle.

The shared Mission panel exposes an explicit preview action and independently
bounds/parses the response, matches every immutable metadata field and rechecks
UTF-8 length and SHA-256. Workspace, credential, user identity/role, Mission and
detail revision changes remove old projections. Abort signals plus generations
reject late reads. Malformed successful list/detail responses render errors,
not fabricated empty lists. Generic data rows no longer imply PASS with a green
check. Only Mission Control's existing Evidence/Outcome flow accepts work.

Adopt shadcn/ui's Dialog composition at commit
`c8c4027b6b192822ea2952b225ee4760cddedf0d`,
`apps/www/registry/default/ui/dialog.tsx`, with MIT attribution. Bind it to the
current exact `@radix-ui/react-dialog` 1.3.1 rather than installing an old CLI.
Use the already locked Monaco 0.55.1, with `@monaco-editor/react` 4.7.0 also
made exact in package metadata. This viewer loads core Monaco in its own local
frame instead of using a page-global React loader: removing the frame gives its
editor, models, workers and Blob URLs one lifecycle. It does not claim sandbox
security from `allow-scripts allow-same-origin`.

The fixed frame script accepts only same-origin parent messages on its private
channel and calls text/model APIs. It never executes Artifact code or inserts
HTML/Markdown. Disable rich hovers, links, suggestions, remote JSON schemas and
TypeScript diagnostics. A load failure/deadline removes the frame and requires
an explicit close/reopen. Closing returns keyboard focus to the actual trigger.

Prepare local Monaco code, styles, hash-named workers and complete notices from
the exact npm lock at install/build time. Missing assets fail the build.
Runtime assets are generated and ignored, and are copied with public resources
into Next standalone/Desktop bundles. Source notices also cover shadcn, Radix
and its runtime dependency licenses. The npm archive of react-remove-scroll-bar
2.3.8 omits its license, so preserve its upstream MIT text from pinned source
`8ca9ba5ea52de03308fe8ced94f7b159a44d28ff`; this is a notice source, not a claim
that npm's unavailable gitHead identifies that revision's code.

Replace the remote Google icon stylesheet with exact
`@fontsource/material-symbols-outlined` 5.3.9 and its local 400-weight CSS/font.
The WOFF2 asset is 323,332 bytes. Preserve the npm archive's actual OFL-1.1
license and source note in shipped notices; the Fontsource website's different
license label does not supersede the distributed font's license. No external
font connection is required by this flow.

The frontend Docker build copies preparation scripts and notices before locked `npm ci` and
uses Node 24, matching CI. Generated assets and local scratch files do not enter
the Docker build context as source. No persistent storage or database migration
is introduced.

## Verification and limits

Executable checks live in the [API tests](../../../tests/api/test_artifact_preview.py),
[SQLite/CAS integration](../../../tests/integration/test_artifact_preview_sqlite.py),
[contract tests](../../../tests/contracts/test_artifact_text_preview.py) and
[real browser flow](../../../frontend/e2e/artifact-preview.spec.ts).

API and real SQLite/CAS tests cover authorization, sensitivity, type/size limits,
bad bytes, unavailable storage, deadlines/cancellation and unchanged durable
state. Contract tests validate actual producer/HTTP output. Component tests cover
strict transport, literal text, empty content, retry, focus and stale responses
across StrictMode, workspace and credential changes.

The selected browser job uses the real Next rewrite, production Mission routes,
SQLite and CAS. Only fixture authentication and unrelated platform requests are
substituted. It requires actual Monaco content/read-only behavior, CJK, HTML
source without execution/resource loads, local assets, live Worker creation and
release, empty/oversize/sensitive behavior, real 403 and network retries without
writes. Enabled tests cannot skip. A fixture server never attaches to or stops
an existing listener; it verifies its own process identity.

The existing npm audit baseline contains 20 findings. The initial 24 added Radix
packages introduce none; unchanged Monaco includes an existing DOMPurify finding.
The narrow viewer disables rich-content entry points, but does not establish a
general hostile-content boundary or replace dependency-security maintenance.
Full framework/security upgrades remain separately scoped.

Web browser success does not establish packaged WebView2, installer, offline
Desktop, IME, zoom or signed release verification. Those release gates remain
required before claiming Desktop readiness. The Tauri shell receives no new
commands or Mission authority. Chat and real log-viewer integrations remain
later UI slices.

Rollback removes this UI/endpoint and corresponding dependency/asset changes;
historical Artifact bytes and Mission state require no migration. Failures must
return to an explicit unavailable state, not synthetic content.
