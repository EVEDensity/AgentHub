# Desktop preview harness tests

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
