# ADR-0136: Observe the shared admin surface after product login

> Status: accepted  
> Owner: Desktop, frontend and delivery maintainers  
> Date: 2026-10-20  
> Scope: UI-4c nested frames, local resources, Chinese input, zoom, and shutdown

## Context

ADR-0134 identifies the packaged executable's WebView2 process. ADR-0135
observes one page-initiated admin login and leaves the shared UI unchecked.
The shell embeds that admin page at loopback `/admin`. The admin page then
embeds `/artifact-code-frame.html` on its own origin as a read-only preview.
UI-4c still has to show that those frames, their script and worker resources,
Chinese keyboard entry, zoom, and process shutdown were observed together.

A browser preview or a resource-byte report does not establish this. Neither
does a login report whose shared-UI flags are already true.

## Decision

Add a `desktop-webview2-shared-surface` report that accepts only a passed
`desktop-webview2-product-login` report for the same executable digest. The
observation must include all of the following:

- The admin frame is `http://127.0.0.1/<port>/admin`. Its child artifact frame
  is `/artifact-code-frame.html` on that same origin, with the sandbox
  `allow-scripts allow-same-origin` and a read-only title.
- Every observed script, style, font, and worker URL stays on that origin.
  Script and worker are both required. Another host fails the check.
- Chinese text is entered with the keyboard API and the control reads the same
  value back. Assigning the value from the test does not pass.
- Zoom is a keyboard change whose finite scale increases and stays between
  0.5 and 3.
- After shutdown, the launched process id is absent and no `msedgewebview2.exe`
  parent chain still reaches it.

The report sets shared UI, Chinese input, zoom, and shutdown true. It leaves
`realServicesVerified` false. It does not execute a Mission, open a credential
bridge, or treat a fixture observation as a packaged WebView2 run.

## Consequences

The classifiers and control readers can be tested without the packaged
executable. Producing this report from a digest-matched `agenthub-desktop.exe`
remains a separate Windows run. Live services and clean-machine installation
remain later gates.

## Alternatives considered

Treating Monaco's presence in the extracted package as proof of the nested
frame would repeat ADR-0133's byte check. Driving zoom through a debugging
emulation command would change scale without using the product keyboard path.

## Verification

`node --test desktop/tests/webview2-surface.test.mjs` covers loopback frame
and resource rejection, keyboard Chinese entry, leftover WebView2 shutdown
failure, and the login-report prerequisite. It does not launch the desktop
executable.

## Supersedes

None. ADR-0134 and ADR-0135 remain the earlier gates.
