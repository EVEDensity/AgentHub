# Desktop preview harness tests

`shell-browser-smoke.test.mjs` uses Node's built-in test runner to exercise the
smoke harness's real static HTTP boundary, exclusive port ownership, cleanup
failures and failed subprocess reports. On Windows it also invokes the legacy
PowerShell entry point with actual arguments and verifies nonzero exit behavior.
It does not manufacture a successful browser runtime or native WebView2 result.

Run `node --test desktop/tests/shell-browser-smoke.test.mjs` from the repository
root. The separate real Chromium smoke requires the existing locked frontend
dependencies and matching installed browser. See [Desktop verification](../README.md).
