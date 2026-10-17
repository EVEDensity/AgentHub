# ADR-0131: Display registered log Artifact snapshots through xterm

> Status: accepted\
> Owner: frontend, Mission Control and Desktop maintainers\
> Last reviewed: 2026-10-18\
> Scope: UI-3a bounded read-only registered log presentation and frontend activity labels

## Context

The [component plan](../../roadmaps/composable-agent-platform-plan.md) requires
a real source before adopting xterm. The admin Terminal has no execution
service. The sidebar console also labeled local navigation records as execution,
state-machine and tool logs and accepted local pseudo-commands. Those records
cannot establish Runner execution or service health.

The existing [Artifact preview contract](0128-registered-artifact-text-preview.md)
already supports `kind: log` and `text/plain`, human/workspace authorization,
exact Mission/WorkUnit/attempt metadata, bounded CAS reads, byte length, SHA-256,
UTF-8 and `no-store`. A new log socket, database or parallel authorization API
would duplicate that boundary.

The opt-in trusted-local test producer actually emits a registered log Artifact:
one diagnostic JSON line followed by captured pytest process output. It does
not provide separate live stdout/stderr channels. OpenSandbox combined output
is inside a report; browser reports are page observations and default model
outputs are `test-result` Artifacts. None is silently relabeled as a log here.
Failed execution paths may publish no Artifact at all. The viewer cannot promise
real-time, complete or universally available execution logs.

## Decision

Use the existing metadata list and authenticated text-preview GET. Only an
explicitly opened registered `kind: log` selects the new renderer; other text
Artifacts retain Monaco. The parent first rechecks the ORIGINAL text bytes,
identity, attempt, size and SHA-256. Unsupported, sensitive, oversized, missing,
tampered or invalid UTF-8 bytes remain unavailable through the existing contract.
Empty bytes remain empty. No command, Evidence, verdict, state transition or
new public contract is introduced by reading or displaying a log.

Adopt exact `@xterm/xterm` 6.0.0, upstream tag and npm `gitHead`
`f447274f430fd22513f6adbf9862d19524471c04`. Its pinned
[manifest](https://github.com/xtermjs/xterm.js/blob/f447274f430fd22513f6adbf9862d19524471c04/package.json)
and [license](https://github.com/xtermjs/xterm.js/blob/f447274f430fd22513f6adbf9862d19524471c04/LICENSE)
declare MIT. The package adds no runtime dependencies; existing locked versions
do not change. Preserve the actual distributed LICENSE, source commit and npm
archive integrity with local assets. This is source/archive checking, not an
independent provenance signature claim. No addons, external fonts, CDN, Worker,
model loop or terminal transport are installed for this renderer.

Before xterm, create an explicitly modified plain-text display projection.
Retain at most the last 2,000 LF-separated logical lines and 100,000 Unicode
code points, with at most 2,000 code points per line INCLUDING its truncation
marker. Bounds count displayed escape tokens and generated spaces. Show source,
omitted-line, truncated-line and visible-control counts. Tabs become four spaces;
C0/C1/DEL controls, carriage returns, soft hyphens and selected bidi/zero-width
format characters become visible `[U+XXXX]` text. This includes the CR in CRLF.
The UI does not interpret ANSI colors, erase/cursor commands, OSC titles/links/
clipboard directives or terminal queries. Do not hash this transformed view and
present it as the original Artifact digest.

An ephemeral same-origin frame owns the terminal and its DOM/listeners. Its
private parent/source/origin/channel protocol accepts only bounded printable
text plus LF, independently rejects controls and malformed Unicode, and never
receives credentials, URLs to fetch or the raw control stream. Only the fixed
script turns LF into CRLF for layout. Use 80 columns, 24 visible rows and bounded
6,000-row scrollback, sufficient for the display cap including double-width
characters and wrapping. Narrow viewports scroll inside the frame.

Set `disableStdin`, make the internal textarea read-only, leave the cursor inert
and return keyboard handling to the browser for focus traversal. Escape requests
the existing Dialog close. No data/binary callback is connected to execution.
There is no WebLinks/clipboard addon; a no-op link activation handler is an
additional guard. Core selection/manual copy is not an automatic clipboard
directive. CSP refuses network connections, images, workers and nested frames;
scripts/styles are local. This same-origin lifetime boundary does not claim
hostile-code isolation from its own parent application.

Dispose the previous terminal before replacement; remove owned listeners and
dispose on explicit cleanup/pagehide. Removing the iframe tears down its browsing
context, including pending work after a load failure. A failed load removes the
frame and requires explicit close/reopen; it does not invent log content. Dialog
focus returns to the actual preview trigger. Identity/workspace/Mission/detail
changes continue to unmount the entire prior Artifact scope.

Rename the sidebar console to frontend activity. It starts empty, records only
actual browser navigation clicks with browser timestamps, keeps at most 200
entries and offers a real clear action. Remove pseudo-command input, fabricated
system/version messages and execution/state/tool tabs. The actual admin Terminal
remains an unavailable notice. Neither surface becomes an interactive shell.

## Verification, limits and rollback

The [owned HTTP fixture](../../../tests/integration/artifact_log_http_fixture.py)
runs the production trusted-local producer with a frozen external pytest suite
and its real process backend. A deliberate test failure captures actual CJK,
stdout/stderr, HTML and ANSI/control output. Production CAS publication and
Mission service lease/start/register/complete commands establish the registered
log and VERIFYING WorkUnit; no Evidence or PASS is admitted. Separate artificial
boundary objects are labeled as fixtures. This tests log production/reading, not
the complete signed five-Artifact verification policy or untrusted-code isolation.

Pure projection, frame and component checks cover bounds, control neutralization,
UTF-8, scope/lifecycle, focus and failures. Real browser checks must use the actual
Next rewrite, API, SQLite and CAS, inspect real xterm content, reject terminal
input/control effects and prove no Mission writes or remote resources. Keep the
prior Artifact/chat/native-Session cases and require the new suite without skips.

Installation/build prepares the exact local JS/CSS/LICENSE and fails on missing
resources. Generated assets stay out of Git and Docker source context; build
preparation produces them in the standalone/Docker/Desktop public resources.
The prior npm audit baseline remains 20 findings with none added by xterm;
this narrow profile is not general dependency-security clearance. Record local
bundle/asset sizes without presenting them as latency or performance gains.

Packaged WebView2, offline assets, zoom, IME, installer and signing checks remain
separate release gates. Live incremental logs, command execution and complete
failed-attempt log retention require independent contracts. Rollback removes
the renderer/package/assets and returns logs to explicit availability or the
existing literal code preview; retain honest activity labels and server read
authorization/integrity. Stored Artifacts and Mission state require no migration.
