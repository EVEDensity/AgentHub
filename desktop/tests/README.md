# Desktop preview harness tests

`webview2-acceptance.test.mjs` covers the identity-before-login launch order,
loopback debugging refusal, and the requirement that acceptance leaves live
services unverified. It does not start the packaged executable.

`webview2-surface.test.mjs` covers the nested admin and read-only artifact
frames, loopback script/worker resources, keyboard Chinese input, zoom, and
shutdown after the login report. It does not launch the packaged executable
or claim a live service.

`webview2-login.test.mjs` covers the loopback debugging argument, the admin
form login interaction, and rejection of a pre-seeded token. It does not
launch the packaged executable. Shared UI acceptance is still separate.

`webview2-native-smoke.test.mjs` covers packaged-executable digest matching,
WebView2 parent-chain ownership, cleanup failure, and the rule that an
identity pass cannot claim login or shared UI. It does not launch the product
or attach to a native WebView2. A live Windows run is
`node desktop/webview2-native-smoke.mjs --executable <agenthub-desktop.exe>
--expected-sha256 <package digest>`.

`shell-browser-smoke.test.mjs` uses Node's built-in test runner to exercise the
smoke harness's real static HTTP boundary, exclusive port ownership, cleanup
failures and failed subprocess reports. On Windows it also invokes the legacy
PowerShell entry point with actual arguments and verifies nonzero exit behavior.
It does not manufacture a successful browser runtime or native WebView2 result.

Run `node --test desktop/tests/shell-browser-smoke.test.mjs` from the repository
root. The separate real Chromium smoke requires the existing locked frontend
dependencies and matching installed browser. See [Desktop verification](../README.md).

`package-resources.test.mjs` exercises resource inventories, actual fixture ZIP
extraction and rejection of missing, modified, stale and unsafe resources.
On Windows it also verifies Tauri's fixed-width MSI/NSIS executable marker
digest calculation, including missing and duplicate marker rejection.
Its package skeletons are explicit fixtures; selected component notices and
fonts come from the locked frontend installation. Install `frontend/` with
`npm ci` first. `local-services-build.test.mjs` runs Windows PowerShell against
disposable Git checkouts and failing build tools to verify cleanup boundaries,
fresh-freeze failures and restoration of inherited environment values. These
tests cannot establish that a genuine installer was built.

On Windows run `node --test desktop/tests/*.test.mjs`. The selected CI package
job also builds the real full stack and independently requires actual Portable
and MSI extraction reports. Those reports, rather than these fixtures, establish
the selected commit's package resource integrity. Python tests in
`tests/desktop/test_package_report_gate.py` execute the report consumer against
classified metadata fixtures and reject incomplete or misleading reports.
