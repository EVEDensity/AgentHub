# ADR-0122: Evaluate a pinned OpenSandbox behind an opt-in HTTP port

> Status: accepted
> Owner: execution and deployment maintainers
> Date: 2026-10-17
> Last reviewed: 2026-10-17
> Scope: M1 Linux Docker conformance experiment

## Decision

Evaluate OpenSandbox 1.1.1 as an execution transport through a separate HTTP
adapter. Pin server, execd, egress and workload OCI digests in a reviewed lock
file; record source tag and build BOM separately. No SDK is added to the main
application dependency graph and no upstream task state becomes business truth.

The adapter is disabled unless explicitly enabled. Require a credential and a
loopback HTTP or explicitly configured HTTPS endpoint; never follow redirects
with credentials. Creation requests use bounded lifetime/resources, deny egress
and no host volumes. Unknown terminal status or missing exit code is not success.
Cancellation requires confirmed termination; uncertainty destroys the sandbox
and fails. File transfer is bounded and checked against SHA-256. There is no
fallback to ordinary local execution.

The Linux CI experiment starts the pinned runtime with an ephemeral credential,
checks unauthorized access, real stdout/stderr/nonzero exit, file transfer,
cross-sandbox separation, actual network denial and process cancellation. Cleanup
is scoped to the current job and its exact resource identities; setup, execution
or cleanup failure fails the selected merge check. CI must not report fake or
skipped conformance as success.

## Limits and rollback

This is a Docker conformance experiment, not a default Runner backend or a
multi-tenant security certification. No KVM, Kubernetes, long-term recovery,
Credential Vault, remote volumes or Windows-native sandbox is implied. Required
isolation, producer identity and budget integration are separate admission gates.
The trusted server has administrative Docker access and runs only on a disposable
CI daemon or an operator-controlled execution node.

Disable new backend admission, cancel/destroy owned environments and retain
diagnostics/history to roll back. Never switch an isolation-required request to
the trusted-local producer when OpenSandbox is unavailable.
