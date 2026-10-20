# ADR-0137: Chain packaged WebView2 acceptance across two launches

> Status: accepted  
> Owner: Desktop and delivery maintainers  
> Date: 2026-10-20  
> Scope: UI-4c orchestration of process identity, product login, and shared surface

## Context

ADR-0134, ADR-0135, and ADR-0136 define three separate reports. Identity
removes any debugging argument. Login and the shared surface need a loopback
debugging endpoint, and both must refer to the same executable digest as the
identity report. Running those checks as unrelated commands can attach the
debugger to a different WebView2 or skip identity entirely.

No digest-matched `agenthub-desktop.exe` is present in this workspace, so this
decision does not record a live packaged run.

## Decision

`desktop/webview2-acceptance.mjs` runs process identity first. Only a passed
identity report whose `debugEndpoint` is null can continue. It then launches
the same executable again with only
`--remote-debugging-port=<port> --remote-debugging-address=127.0.0.1`. The
second process must own an `msedgewebview2.exe` child. The debugging request
goes to `http://127.0.0.1:<port>/json/version` and refuses any other host
before a network call.

The product session supplies the login exchange and the shared-surface
observation. Shutdown is taken from the process list after that observation
and before the final report. The acceptance report copies login and surface
success and keeps `realServicesVerified` false. If no product session is
connected, the run fails. A failed identity run does not launch the second
process.

This is test metadata. It does not add a credential bridge, change Mission
state, or claim that a fixture session launched the packaged executable.

## Consequences

The sequence can be tested by substituting the identity run, process list, and
session. A Windows run of the command still needs the packaged executable and
a connected product session. Live Mission services remain a separate gate.

## Alternatives considered

Opening the debugging port during the identity launch would contradict
ADR-0134. Treating the three reports as independently optional would allow a
login observation without proving which executable owned the WebView2.

## Verification

`node --test desktop/tests/webview2-acceptance.test.mjs` covers loopback
refusal, identity-before-launch ordering, the replaced debugging argument, and
the false live-service flag. It does not start `agenthub-desktop.exe`.

## Supersedes

None.
