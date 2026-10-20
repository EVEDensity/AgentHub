# ADR-0135: Observe product login through the admin form

> Status: accepted  
> Owner: Desktop, frontend and delivery maintainers  
> Date: 2026-10-20  
> Scope: UI-4c login observation, after packaged WebView2 process identity

## Context

ADR-0134 proves that a digest-matched `agenthub-desktop.exe` owns its WebView2
process. That report intentionally leaves login false and opens no debugging
port. The shell's in-memory Mission Control token is not presented to the
embedded admin origin. The admin page authenticates by posting the form to
`/api/auth/login` and only then stores the returned token.

A later UI-4c run needs a debugging connection to drive that form. The
connection has to be limited to the test process and to loopback, and it must
not be accepted as login when the token was already present.

## Decision

Product-login acceptance requires a passed process-identity report for the
same executable digest. The test process may set
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` to only
`--remote-debugging-port=<port> --remote-debugging-address=127.0.0.1`, where
the port is an integer from 1024 through 65535. Inherited debugging arguments
are removed first. The recorded endpoint is `http://127.0.0.1:<port>`.

The interaction reads whether `agenthub_token` is already present, then fills
the admin Username and Password fields and activates the Login button. Login
is verified only by one page-initiated HTTP 200 `POST /api/auth/login`. A
pre-existing token, a test-originated write, or any other initiator fails the
check. The page may store the token after that response; that write is the
product path.

This scope sets `realLoginVerified` true and leaves shared UI, live services,
Chinese input, zoom, and shutdown false. It does not add a credential bridge,
change the admin login API, or treat the identity report as login.

## Consequences

The helper can be tested without launching the packaged executable. Wiring it
to a live WebView2 still requires a digest-matched package and a Windows run.
Shared Mission and Artifact frames remain a separate acceptance scope.

## Alternatives considered

Copying the shell token into the admin origin would skip the login form and
would not show that the packaged WebView2 can authenticate. Accepting any
debugging port on a non-loopback address would expose the test browser beyond
the test process.

## Verification

`node --test desktop/tests/webview2-login.test.mjs` covers the loopback
argument, rejection of a pre-seeded token, the form interaction order, and
binding to a passed identity report. It does not launch WebView2.

## Supersedes

None. ADR-0134 remains the process-identity gate.
