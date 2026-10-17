# Disposable OpenSandbox execution PoC

> Status: implemented
> Owner: Runner maintainers
> Last reviewed: 2026-10-17
> Scope: explicit M1 integration experiment, outside normal Runner startup

This profile runs a small real execution experiment behind the optional
AgentHub OpenSandbox HTTP adapter. It does not change Mission ownership,
default execution, production deployment or the model loop. It needs no model
key, cloud account, external service account or repository secret.
Real Linux Docker execution remains unverified until the opt-in job records
passing evidence.

## Prerequisites and command

Use a disposable Linux amd64 host with Python 3.11 or newer, the repository
dependencies, rootful Docker Engine, a reachable local
`unix:///var/run/docker.sock`, and access to public Docker Hub images. Ubuntu
24.04 GitHub-hosted runners are the intended CI target. Remote Docker contexts,
Windows provisioning and a daemon with existing OpenSandbox main/sidecar
containers or managed runtime volumes are rejected. This establishes an empty
baseline and prevents upstream recovery logic from adopting unrelated
containers. The daemon must not be shared with another concurrent PoC.

From the repository root:

```bash
python scripts/opensandbox_poc_ci.py --enable --report data/opensandbox-poc/run-1/report.json
```

`--enable` is mandatory. Every run needs a fresh report path. The wrapper pulls
the four digest-pinned images, creates a random job ID and authentication key,
starts one temporary server, checks health and authentication, and calls
`scripts/opensandbox_poc.py` using the current Python interpreter. No OpenSandbox
SDK or server package is installed into AgentHub. The workload image is a small
Ubuntu shell environment; Code Interpreter, Kubernetes, Redis, Vault and model
providers are not required.

## Authentication, ports and network

The server container uses host networking so its loopback can reach Docker's
loopback-published ports. It binds its lifecycle API to `127.0.0.1:18080`; sandbox
ports use `127.0.0.1:45000-45120`. The range spans at least 100 ports to satisfy
the upstream 1.1.1 configuration validator. Only the server container receives
the Docker socket. Sandbox workloads have no host bind mount or Docker socket.
The server's socket grants control of the disposable Docker daemon; this layout
is not a public or multi-tenant server deployment.

The generated key enters the server through `OPENSANDBOX_SERVER_API_KEY` and
the PoC process through `AGENTHUB_OPENSANDBOX_API_KEY`. Docker receives the
environment variable name without its value in argv. GitHub Actions masks the
random key before provisioning. It is absent from config files, reports and
artifacts. Neither missing nor wrong authentication may access `/v1/sandboxes`.
The unauthenticated `/health` endpoint alone cannot establish readiness.

The config enables bridge networking with the pinned `dns+nft` egress image.
The HTTP adapter explicitly requests a deny policy per sandbox; configuring an
egress image alone does not establish deny-by-default behavior. The wrapper
passes the Docker bridge gateway as `AGENTHUB_OPENSANDBOX_CANARY_HOST` for the
PoC's temporary TCP listener. A positive host probe establishes listener health;
the sandbox's denied TCP connection is checked without depending on Internet
availability. This listener binds a random port on the disposable host while
the check runs and holds no secrets.

Docker's runtime does not support upstream `secureAccess`. Loopback endpoints
are not tenant-specific authenticated endpoints. Real malicious-workload
isolation, kernel escapes, DNS rebinding and adversarial network-policy bypasses
require separate evaluation.

## Pinning and provenance

[`images.lock.json`](images.lock.json) records all OCI digests, `linux/amd64`,
the release tag, its commit and the distinct upstream BOM `gitCommit`. Docker
pulls by digest; mutable release tags are never used at runtime. The example
config is rendered from [`server.config.toml.in`](server.config.toml.in) with
these exact execd and egress values. The release is fixed at 1.1.1 rather than
the broken upstream 1.1.0 server wheel.

These pins were checked against the upstream BOM and public registry manifest
metadata. Digest pinning does not establish publisher identity. This PoC does
not yet perform keyless signature/provenance validation or generate a complete
image SBOM; those remain explicit gates before broader adoption.

Sources: [release and limitations](https://github.com/opensandbox-group/OpenSandbox/releases/tag/release-1.1.1),
[BOM](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.1.1/docs/releases/1.1.1.yaml),
[Docker labels](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.1.1/server/opensandbox_server/services/docker/runtime.py),
[sidecar lifecycle](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.1.1/server/opensandbox_server/services/docker/networking.py),
[publisher verification guide](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.1.1/docs/community/release-verification.md).
OpenSandbox's repository is Apache-2.0; image OS packages and bundled components
have their own notices and licenses. The lock does not replace a distribution
license inventory. Docker Desktop has separate terms and is not needed here.

## Evidence, cleanup and rollback

The PoC report records checks and exact sandbox IDs with the job identity. A
zero exit without a matching successful report fails the wrapper. The wrapper
writes a separate `report.infrastructure.json` with image pins, Docker version,
job ID, overall status and cleanup errors. Tests using mocked HTTP or Docker
only establish adapter contracts; a passing manual Linux run is required for
real environment evidence. No real Linux run or production claim is recorded
by merely checking in this profile.

The server's SQLite state is an operating-system temporary directory mounted
at `/poc-state`. Config and state are removed on wrapper exit.
The private host temporary parent remains mode `0700`. Its credential-free
config is `0644` and the exact state mount target is `0777` so the upstream
UID-0 server with all capabilities dropped can read/write across the host
runner UID boundary. No credentials are stored in either mount target.
Workload data must be transferred by file API; it is not mounted from the checkout. The
adapter's serial path checks reject traversal and symlinks for this controlled
experiment; these checks do not provide race-free `openat2` isolation against
concurrent hostile workloads and cannot become the production storage boundary.

The PoC destroys its own resources in `finally`. The wrapper then uses the
exact random `agenthub.job` metadata label, IDs recorded by that job's report,
and `opensandbox.io/egress-sidecar-for=<sandbox-id>` to remove only its own
remaining containers. A runtime volume must have the exact
`opensandbox-runtime-<sandbox-id>` name and upstream managed-volume label.
Server cleanup uses its recorded full container ID and verifies the job label.
Deletion is followed by absence checks. After stopping the server, a final
read-only scan counts all remaining OpenSandbox main containers, sidecars and
managed `opensandbox-runtime-*` volumes. `unknown_resource_counts` conservatively
includes every unresolved residue, even known resources whose removal failed.
Any nonzero count fails cleanup. If scanning fails, counts remain `null` and
cleanup fails; unavailable evidence is never represented as zero. Only counts
are reported, without resource names or secrets. This detects a sidecar created
before a lost create response, where no client-recorded ID or job label exists.
Such unknown resources are never deleted or assigned to this job based on an
inventory difference. Cleanup errors fail an otherwise successful run. No
global `docker prune`, blanket OpenSandbox deletion or host directory deletion
is used.

Rollback is stopping this disposable run; no Mission migrations or default
backend changes exist. Termination signals trigger bounded best-effort cleanup.
Forced host loss or `SIGKILL` cannot run Python cleanup; use disposable runners
and inspect the failed run's exact IDs before any manual cleanup. Public image
cache contents remain in the daemon until the runner is discarded; they are
not per-job containers or credential stores.

Local contract verification:

```bash
python -m pytest -q -p no:cacheprovider tests/services/test_opensandbox_backend.py tests/services/test_opensandbox_poc.py tests/services/test_opensandbox_poc_ci.py
python -m ruff check app/services/opensandbox_backend.py app/services/opensandbox_protocol.py scripts/opensandbox_poc.py scripts/opensandbox_poc_ci.py
```

## Future Runner injection boundary

The pure HTTP port lives in `app/services/opensandbox_backend.py`; its bounded
wire helpers live in `opensandbox_protocol.py`. It does not import the upstream
SDK, and it is outside `runner/__init__.py` so the standalone experiment does
not load the default model/controller composition. Constructing the port with
`OpenSandboxConfig(..., enabled=False)` is refused before any network call.
Normal Runner and desktop startup never construct it.

A later, separately reviewed Runner composition may construct this port only
after resolving the immutable Contract and attempt-scoped execution policy.
That composition must translate approved operations to explicit argv, bind
job/sandbox identity to the active attempt, stop work on lease loss, publish
digest-verified bytes through the existing CAS publisher, and preserve
independent verification. It must own cleanup on all paths, including an
uncertain create response. Backend failure must fail the attempt without
dispatching a native subprocess. This PoC is not that production integration,
does not prove checkpoint compatibility, and does not change Mission states.
