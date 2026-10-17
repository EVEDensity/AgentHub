# Experimental anonymous document reader

> Status: implemented, operator-only experiment\
> Owner: Runner and browser-tool maintainers\
> Last reviewed: 2026-10-18\
> Scope: fixed Node/Playwright worker, one anonymous document per process

This worker is a private AgentHub JSON-file/stdout transport. It is not an MCP
server or an Agent runtime. Python owns the actual claim, budget, deadline,
process lifecycle and Artifact registration. Defaults do not select it.

The package locks Playwright 1.61.1 and its npm integrity records. The reviewed
upstream tag points to commit `39e3553a4f283a41134d75d7e404484bd9e6865a`;
its Chromium revision is 1228, version 149.0.7827.55. The worker checks both
package and actual browser version. Upstream Playwright is Apache-2.0; retain
the dependency licenses/notices and browser notices in any future distribution.
This directory contains original AgentHub adapter code, not copied MCP code.

Install the optional developer runtime explicitly, using Node 24:

```sh
npm ci --ignore-scripts
PLAYWRIGHT_BROWSERS_PATH=/absolute/operator/browser-cache npx playwright install chromium
npm test
```

Pass the same absolute cache path to `BrowserReadProfile`. The Python adapter
clears inherited credentials, proxy and Node hook variables, and supplies private
HOME/temp directories. It does not download a missing dependency. The first
supported process adapter is Linux with pidfd and readable same-UID `/proc`;
Windows clients can use a supported Linux Runner. This does not package a
Windows-native browser backend or modify the Tauri application's browser.

The input file has exactly schemaVersion, requestId, url, origins, timeoutMs and
maxTextChars. The owner UUID is private parent-generated process metadata, never
a model argument. Output is one bounded `agenthub.browser-read.v1` JSON result.
`started.json` in the owned temporary directory supplies observable process IDs
to conformance tests; Python does not treat its PIDs as signaling authority.

Each call creates a fresh BrowserServer and nonpersistent context. Page JS,
Service Workers, downloads and WebSockets are disabled. Only one main-frame GET
is fetched; all other page requests are aborted. `route.fetch` explicitly uses
zero redirects and retries. Any 3xx, non-2xx, unsupported document type, download,
version mismatch or unconfirmed cleanup fails. The result contains actual title,
bounded visible body text, canonical UTF-8 digest, pre-truncation bytes, truncation
and blocked-resource count. It does not include credentials, cookies or raw HTML.

The origin filter is a request policy for operator-reviewed controlled origins.
It is not DNS pinning, an OS egress firewall or hostile-page isolation. A GET may
have server-side effects. SPA interaction, login, arbitrary scripts, response-body
memory hard quotas and production internet browsing are outside this experiment.

Python independently terminates and checks the exact owner-marked processes,
including Chromium's detached POSIX process group, using verified pidfds. Other
processes are not terminated. Inaccessible process inventory is unknown cleanup,
not an empty inventory. Read failures cannot be replaced by a model completion
claim, and a failed Runner result does not publish its diagnostic Artifacts.

The selected Linux CI job runs actual Chromium against local HTTP fixtures,
including cookies, script/subresource refusal, errors/redirects, cancellation,
deadlines and the complete budgeted Native/SQLite/CAS/Verifier flow. It rejects
empty or skipped enabled conformance. See
[ADR-0127](../../../docs/architecture/decisions/0127-experimental-anonymous-browser-read.md).
