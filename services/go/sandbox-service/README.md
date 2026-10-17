# Sandbox service

> Status: implemented\
> Owner: execution maintainers\
> Last reviewed: 2026-10-17\
> Scope: Docker execution transport and failure behavior

This standalone Go service manages Docker containers for Runner code execution.
It owns container lifecycle and output capture, not Mission or WorkUnit state.
It does not authenticate callers: run it only on a private, trusted service
network. Docker socket access is an administrative privilege. This service is
not a tenant authorization boundary or a complete hostile-code platform.

## Supported execution boundary

Both `POST /v1/execute` and the managed `/containers` API create containers with
Docker `network=none`, dropped Linux capabilities, a read-only root filesystem,
`no-new-privileges`, CPU/memory limits and a bounded `/tmp` tmpfs. `disk_mb` is
metadata, not an enforced disk quota. The image must already be available to the
Docker daemon and keep its main process alive for exec calls.

Nonempty `network_allow` requests are rejected with HTTP 422 before container
creation. Setting `SANDBOX_NETWORK_ALLOW` to anything other than `none` makes
one-shot execution and readiness fail with HTTP 503. The former in-container
iptables path could not reliably enforce allowlists before startup or for IPv6;
it has been removed. Network access needs an independently enforced egress
boundary before it can become supported.

`DOCKER_SOCKET` defaults to `/var/run/docker.sock`; this process requires a Linux
Docker execution node. There is no production simulation or fallback execution.
If Docker cannot be reached, execution returns an error and never a fabricated
container, zero exit code or sample stdout. The process retains its real Docker
client, so readiness can recover after the daemon returns.

An unset `SANDBOX_SECCOMP_PROFILE` uses Docker's built-in default. If configured,
the file must be readable JSON; its contents are sent to the Engine API. An
invalid or missing configured profile fails readiness and creation rather than
silently selecting a weaker policy. Docker performs the final profile check.

## HTTP behavior

- `GET /healthz` reports process liveness (`status: alive`); it does not claim
  that code execution is available.
- `GET /readyz` probes the real Docker daemon with a three-second deadline and
  checks the configured security profile. An unsupported network configuration,
  invalid profile, missing socket or failed ping returns HTTP 503,
  `ready: false`, `mode: unavailable`.
- `POST /v1/execute` accepts `code`, `language` (`python` or `bash`), `timeout`
  and optional `image`. A successful HTTP response includes the actual
  `exit_code`, stdout/stderr and `mode: docker`; `success` is true only for a
  confirmed zero exit code after the temporary container has been removed.
- `POST /containers/{id}/exec` accepts `command` and optional `timeout` and
  returns a recorded execution result. Both execution paths default to 30
  seconds and cap deadlines at 300 seconds. The request cancellation/deadline
  reaches Docker create/start/exec HTTP calls and stream reads.
- Inspection errors, missing exit codes and still-running executions are
  failures. Timeout/cancellation returns HTTP 504, `success: false` and
  `exit_code: -1`. Unconfirmed executions are never recorded as successful.
- Interrupting an exec stream does not kill its process. Both paths therefore
  force-remove the affected container using an independent ten-second cleanup
  deadline; a failed managed container cannot admit further exec calls. Cleanup
  failures are logged, one-shot cleanup failures cannot return success, and
  failed managed deletes retain the record for retry. Operators must reconcile
  Docker containers after daemon outages or service crashes; cleanup is not a
  durable garbage collector.

Managed container listings and logs are process-local projections and are lost
on service restart. They are not a durable execution ledger or verification
evidence. Command success alone does not establish Mission acceptance.

## Verification

From this directory, run `go test ./...` and `go vet ./...`. The deterministic
suite uses HTTP daemon fixtures and backend fakes only in tests. It covers
unavailable Docker, rejection of unsupported networking, security payloads,
inspection failure, deadline propagation, cancellation cleanup and delete
failure. Real Linux Docker execution/isolation remains a separate environment
gate; passing unit tests does not establish production isolation.

### Real Linux Docker gate

`AGENTHUB_TEST_DOCKER=1 go test -count=1 -timeout=120s ./...` enables the real
Engine integration tests. Without that flag they explicitly skip. Once enabled,
an unavailable daemon, missing local image, unsupported host or execution error
fails the tests; the gate never pulls an image or falls back to a fake backend.
`DOCKER_SOCKET` defaults to `/var/run/docker.sock`.

Build the test image in CI before enabling the gate, and set
`AGENTHUB_TEST_DOCKER_IMAGE` to its tag (default:
`agenthub/sandbox-integration:test`). The image needs `/bin/sh`, `sleep` and a
long-running default command, for example `FROM alpine:3.21` with
`CMD ["tail", "-f", "/dev/null"]`. It must work with a read-only root and no
network; all test scratch is in `/tmp`.

The gate uses the production Docker client to execute real stdout/stderr and
nonzero exits, inspect network/read-only/resource/privilege settings, reject an
actual root write, and cancel an already running command through the managed
HTTP handler. It then asks the real Engine to confirm the container is gone.
Requests and observations have bounded deadlines, and `t.Cleanup` attempts
independent container removal even after a failed assertion or create response.
These checks establish the listed Docker behaviors only, not general hostile
code or tenant isolation.
