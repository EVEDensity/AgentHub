# Artifact presentation

> Status: implemented, bounded UI-1 profile\
> Owner: frontend and Mission API maintainers\
> Last reviewed: 2026-10-18\
> Scope: registered Artifact text preview and its local presentation assets

`MissionArtifactList` reads only registered v1 Mission Artifact metadata. An
explicit preview click calls the authenticated Mission Control preview endpoint;
the browser never follows a `contentAddress`, reads local paths or starts a tool.
The original `ArtifactViewer` remains the legacy session-content view and does
not establish authority for these registered execution Artifacts.

`missionArtifactPreview.ts` accepts the exact preview contract, bounds serialized
responses to 2 MiB and text to 256 KiB, and independently checks immutable
identity, attempt, UTF-8 bytes and SHA-256. Failed reads stay visible with a GET-only
retry. Empty bytes are shown as empty. Workspace, credential and Mission changes
unmount the old projection; cancellation and request generations reject late
responses. Byte integrity does not create Evidence or accept a Mission.

`ArtifactDialog` adapts the reviewed shadcn Dialog composition over Radix, with
focus return and Escape handling. The lazy `ReadOnlyArtifactCode` owns an
ephemeral same-origin frame. A fixed local script uses Monaco text APIs; Artifact
text is never inserted as HTML or executed. The frame owns editor/model/Worker
and Blob URL cleanup. It is a lifecycle boundary, not hostile-content isolation.
Rich hovers, links, suggestions, remote schema requests and TypeScript diagnostics
are disabled for this viewer.

`npm ci` and `npm run build` prepare pinned Monaco assets and licenses under
ignored `public/monaco/0.55.1/`. Missing assets fail preparation. The standalone
Next.js build, Docker image and Desktop public-asset copy carry the same files.
No CDN, user browser session, new model runtime or separate Mission store is used.

The real browser gate starts an explicitly named loopback test fixture using
SQLite/CAS and production Mission routes, with fixture-only authentication. It
checks CJK/read-only content, HTML source, local resources, actual Worker cleanup,
empty/limited content, authorization and transport failures. Packaged WebView2
startup, offline behavior and release signing remain separate Desktop gates.

See [ADR-0128](../../../docs/architecture/decisions/0128-registered-artifact-text-preview.md).
