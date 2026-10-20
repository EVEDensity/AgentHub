# ADR-0134: Identify the packaged executable's own WebView2 process

> Status: accepted  
> Owner: Desktop and delivery maintainers  
> Date: 2026-10-20  
> Scope: UI-4c process-identity gate, before product login and shared UI acceptance

## Context

ADR-0132 classifies the Playwright shell preview as neither a packaged
application nor native WebView2. ADR-0133 verifies extracted Portable and MSI
bytes, then explicitly leaves native WebView2 and product login unchecked.
The desktop shell keeps its Mission Control token in memory. That token is not
delivered to the admin origin embedded by the shell, so writing
`localStorage.agenthub_token` from a test does not authenticate the product.

UI-4c still has to launch the actual packaged executable, prove which WebView2
process it owns, and only then exercise real login, nested frames, local
assets, Chinese input, zoom, and shutdown. Those later checks need a debugging
endpoint. Opening that endpoint before ownership is proven would let a test
attach to some other WebView2 on the machine.

## Decision

Add a separate `desktop-webview2-process-identity` check. It launches only a
regular file named `agenthub-desktop.exe` whose SHA-256 equals the supplied
package application digest. It removes any inherited
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` from that process, so this gate does
not open a debugging port.

A WebView2 process counts as owned only when its image is `msedgewebview2.exe`
and its parent chain reaches the launched process id within eight steps.
Another desktop process, an unrelated WebView2, a cycle, or a chain longer
than that bound is not ownership. The check records the launched id and the
owned WebView2 ids, then stops the launched process tree.

A passing identity report sets `nativeWebView2` and `packagedApplication` to
true and keeps `realLoginVerified`, `sharedUiVerified`, `realServicesVerified`,
`chineseInputVerified`, `zoomVerified`, and `shutdownVerified` false, with
`debugEndpoint` null. Setting any of those later flags true fails this scope.
The report is test metadata, not Mission Evidence or a login proof.

Product login continues to require the admin origin's real login request.
This decision does not add a credential bridge, change native commands, or
treat resource extraction as GUI acceptance.

## Consequences

Windows can fail closed when the packaged executable or its WebView2 child is
absent. CI resource jobs stay byte checks until a later slice attaches this
identity result to a build digest and adds the login scope. A passed identity
report cannot be promoted to UI-4c completion.

## Alternatives considered

Attaching to any local WebView2, or seeding the admin token from the shell,
would produce a login-shaped page without proving the packaged executable
owned that runtime. Enabling the debugging port during identity discovery
would widen the test process before ownership is known.

## Verification

`node --test desktop/tests/webview2-native-smoke.test.mjs` covers digest
mismatch, parent-chain ownership, inherited debug-argument removal, and the
requirement that a passing identity report leaves login and shared UI false.
Launching the selected package and recording a live process tree remains a
Windows run of `desktop/webview2-native-smoke.mjs`; unit fixtures do not
substitute for that run.

## Supersedes

None. ADR-0132 and ADR-0133 remain in force for their narrower scopes.
