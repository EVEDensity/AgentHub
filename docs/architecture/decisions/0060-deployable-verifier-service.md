# ADR-0060: Deployable Workspace Verifier Service

> Status: accepted  
> Owner: verification and operations maintainers  
> Date: 2026-08-16  
> Scope: verifier process configuration, storage boundary, and lifecycle

## Context

ADR-0059 introduced the pure verifier coordinator and worker but intentionally
left process composition undefined. A deployable verifier needs a distinct
credential, bounded configuration, operational probes, resource ownership, and
shutdown behavior. Reusing the Runner process would violate independent
verification, while accepting ambient storage or credential defaults would
make Artifact byte provenance deployment-dependent.

## Decision

AgentHub provides a Python verifier service that hosts exactly one explicitly
configured workspace worker. The process requires verifier ID and version,
workspace ID, Mission Control origin, mounted bearer-token file, and mounted
Artifact CAS root. Identity, workspace, network origin, token, and storage have
no functional defaults.

The Mission Control token is read only during runtime composition from an
absolute, bounded, non-symlinked, single-line file. Plaintext token environment
settings are unsupported. HTTP redirects are disabled and the process owns one
bounded Mission Control client, closed after worker shutdown.

The initial deployment storage profile is deliberately local-only. It accepts
`local:sha256/...` addresses and reads beneath the configured content-addressed
root with the existing digest, size, path-containment, and byte-limit checks.
MinIO and every other address fail before provider I/O. Supporting remote
object storage later requires explicit endpoint, bucket, credential-file, and
trust configuration rather than activating library defaults.

The service exposes only `/healthz` and `/readyz`; API documentation surfaces
are disabled. Health means the worker task is alive. Readiness additionally
requires a running worker whose last poll completed successfully. Responses
contain only sanitized worker counters, timestamps, enum status, and exception
type.

Shutdown first requests graceful worker stop and waits for the current
evaluation. At the configured deadline the worker task is cancelled, allowing
cancellation to propagate through Artifact byte I/O and the Mission Control
request, then owned HTTP resources are closed. The process creates no durable
lease or recovery record. Mission Control remains authoritative if admission
races with cancellation.

The container runs as dedicated non-root UID `10002`. Operators mount the token
and Artifact root read-only. Independent replicas may repeat evaluation; they
must not add a process-local queue or assume discovery is a claim.

## Consequences

Verification can scale and deploy independently from execution without gaining
general Mission listing or Runner authority. Startup fails closed on missing or
unsafe configuration. The first storage profile is operationally narrower than
Mission Control's generic Artifact verifier, preventing accidental use of
default object-store credentials.

Readiness is based on real discovery rather than a synthetic startup success.
A persistent invalid policy or unavailable Artifact keeps the service alive but
not ready and is retried with bounded backoff. Fleet alerts must distinguish
health from readiness and investigate Mission Control or storage rather than
editing durable work state.

This decision does not solve criterion attribution for inconclusive policy,
remote Artifact storage, verifier authenticity signatures, or semantic
evaluation. Each requires its own contract and trust decision.

## Alternatives considered

- Host verifier and Runner in one process: rejected because credentials,
  failure domains, and output independence would collapse.
- Accept bearer tokens directly in environment settings: rejected because
  process metadata and deployment manifests would expose long-lived secrets.
- Enable MinIO using application defaults: rejected because endpoint, bucket,
  and credential provenance would not be explicit verifier configuration.
- Report ready immediately after task creation: rejected because configuration
  success does not prove authorized Mission Control discovery.

## Verification

Configuration tests cover required identities and paths, unsafe URLs, plaintext
token rejection, bounded secret files, and poll limits. Runtime tests cover
strict composition, local-only Artifact rejection, health/readiness redaction,
graceful drain, shutdown cancellation, and resource closure. The integration
gate exercises the verifier service against the real Mission API transport and
real local Artifact bytes.

## Supersedes

This decision implements the deployable-process follow-up from
[ADR-0059](0059-independent-verifier-coordinator.md). ADR-0055 through ADR-0058
remain authoritative for discovery, policy, controlled evaluation, and durable
Evidence integrity.
