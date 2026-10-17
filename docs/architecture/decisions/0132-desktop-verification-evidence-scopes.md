# ADR-0132: Separate desktop browser preview from packaged and native acceptance

> Status: accepted\
> Owner: Desktop, frontend and delivery maintainers\
> Last reviewed: 2026-10-18\
> Scope: UI-4a smoke tooling and desktop acceptance evidence classification

## Context

The script named `webview2-gui-smoke.ps1` served `desktop/ui/` through Python
HTTP and opened a normal browser using a dynamically downloaded Playwright CLI.
It neither launched a packaged executable nor drove its native WebView2. The
shell deliberately provides native-command fixtures in browser preview; its
displayed readiness is not an observation of real services. The old script
did not write the screenshots implied by its workflow artifact name.

The ordinary Windows package job does not request `-LocalServices`. The
tag-only stack publishing job builds those resources separately. Neither a
shell package nor its browser preview establishes that UI-1 through UI-3's
Next.js resources are included or usable in the installed application.

## Decision

Name the canonical check `shell-browser-smoke.ps1` / `.mjs`. Preserve the old
PowerShell entry point as a parameter/exit-code compatibility wrapper, without
retaining the inaccurate success message. Use only Playwright 1.61.1 already
installed through the existing frontend lockfile and its matching Chromium.
Missing dependencies or browser fail; the smoke does not download tools.

The runner owns an exclusive loopback static server and its browser/context.
Only a fixed set of shell assets may be served, after realpath and regular-file
checks. Reject traversal, private/API paths and unexpected methods. Do not
reuse or stop an existing listener. Before navigation, restrict browser requests
to those exact static GET/HEAD assets, block service workers/WebSockets and use
CSP to refuse connections and frames. This includes the shell's automatic
loopback login attempt; it must not contact a developer's running services.
This is a test transport boundary, not a production sandbox claim.

Preserve actual home, settings and monitoring DOM assertions and write actual
screenshots with a visible browser-preview classification. Existing native
fixtures remain fixtures; do not exercise a simulated bootstrap and call it
installation, authentication or Mission execution.

Each invocation creates a unique run directory, initially writes a `running`
report and atomically replaces it with `failed` or `passed`. Mark success only
after all assertions, screenshots and owned-resource cleanup succeed. A failed
run cannot overwrite an older run's report or silently inherit its screenshots.
Include tool version, checked screens and explicit scope, with
`nativeWebView2`, `packagedApplication`, and `realServicesVerified` all false.
The JSON is internal smoke metadata; it is not Mission Evidence or a new public
business contract. Do not change Mission state, authentication or native commands.

Run the real browser preview in deterministic PR CI and the separate Windows
packaging workflow. Upload its classified report/screenshots using accurate
artifact names. CI independently rejects absent/failed/unclassified reports or
missing screenshots. Node tests exercise real static HTTP/port boundaries,
failure cleanup and compatibility-wrapper behavior on supported platforms.
The existing shared frontend browser suites remain mandatory.

## Follow-up gates

UI-4b must inspect actual Portable/installer contents from the selected source
commit and full-service build mode: Next static assets, baked rewrites, Monaco
workers, xterm, fonts and license notices. Source copies or a separate stack
build are insufficient. Missing, stale or tampered resources must fail.

UI-4c must launch the actual packaged executable on Windows and identify its
owned WebView2 runtime before inspecting real login and nested Mission/Artifact
frames, local assets, Chinese input, zoom and shutdown. Testing infrastructure
may enable a bounded debugging endpoint only for the test process. The shell's
in-memory login token currently does not authenticate the admin origin; use the
real login flow or design an explicitly authorized bridge separately. Fixture
localStorage injection cannot establish product authentication.

Signing, clean-machine installation, upgrade/rollback and live-provider checks
remain separate gates. Historical green workflows retain their historical
meaning and do not prove these follow-up gates. See the
[component plan](../../roadmaps/composable-agent-platform-plan.md#511-web-与-desktop-ui-组件target).

## Rollback

The change affects test tooling, documentation and CI selection only. It adds
no product runtime dependency, persistence or native command. Revert the new
check if needed while retaining the truthful evidence classification; do not
restore a browser preview's claim to native WebView2 acceptance.
