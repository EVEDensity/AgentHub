# ADR-0053: Runner-Supervised Outbound A2A Execution

> Status: accepted  
> Owner: protocol, Mission Control, and Runner maintainers  
> Date: 2026-08-15  
> Scope: outbound A2A WorkUnit binding, claim, and execution ownership

## Context

Outbound `tasks/send` creates a durable `a2a` Mission and an
`a2a.delegate` root WorkUnit, but the Gateway currently performs the remote
request inside the caller's HTTP request. The WorkUnit previously had the
`a2a.outbound` adapter without an assigned local execution Agent and therefore
could not enter the fenced Runner lifecycle. A successful remote dispatch left
the local WorkUnit `PENDING`; later reads did not supervise the remote task.

Creating a long lease in the Gateway request would make the Gateway an implicit
Runner without stable heartbeat, crash recovery, cancellation supervision, or
attempt fencing. Treating the remote acknowledgement or peer Evidence as local
completion would also bypass Artifact publication and independent verification.

## Decision

Every newly persisted outbound A2A WorkUnit is bound before Mission creation to
one enabled, credential-free workspace catalog entry. Selection requires the
exact `a2a.outbound` adapter plus `a2a.send`. The selected Agent ID and adapter
type are stored as an immutable local execution snapshot. Missing, unavailable,
wrong-adapter, or capability-incomplete catalog state fails before creating a
new Mission.

Capabilities requested from the remote Agent remain in the Contract and
WorkUnit and are checked against the remote Agent Card. They are not required
on the local transport worker's catalog entry: transport authority and remote
task capability are separate trust decisions.

Mission Control admits an outbound root to the existing atomic claim path only
when all of these facts agree:

- Mission source is `a2a`;
- WorkUnit kind is `a2a.delegate` and it has no parent;
- assigned adapter is `a2a.outbound`;
- requested Agent and adapter equal the stored binding;
- dependencies, status, workspace ACL, tenant concurrency, and lease policy
  satisfy the existing claim contract.

Claiming emits the normal fenced lease event with claim mode `a2a.outbound`.
It does not dispatch remotely or prove completion.

The active outbound lease owner can read the same versioned
Mission/Contract/WorkUnit execution projection used by controlled inbound
roots. Mission Control rechecks source, root kind, assigned executor, adapter,
`a2a.send`, status, attempt, lease ID, owner, and expiry before returning it.
The read creates no event and exposes no provider or peer credential.

The outbound worker will own the remaining lifecycle: start the WorkUnit, call
the protocol transport, renew the lease while polling, propagate local
cancellation, import a bounded result into local content-addressed storage,
register local Artifact metadata, and complete to `VERIFYING`. Remote Evidence
is stored only as an attestation or report Artifact. A separate local verifier
remains required for `SUCCEEDED`. Gateway will then submit commands and project
Mission state; it will no longer supervise remote execution in the request path.

This cutover is phased. The current slices implement catalog binding,
controlled claim eligibility, lease-fenced execution context, and a dedicated
outbound claimed-work resolver. The resolver produces a bounded,
credential-free command and never invokes the inbound model Harness. A
stateless transport port defines `send/get/cancel` and a finite content-free
remote state projection. Its HTTP implementation now enforces strict JSON-RPC,
bounded bodies, exact identities, safe same-origin redirects, and
origin-specific receiver credentials. Agent Card trust/capability verification
is injected through a trusted-route boundary rather than copied into Runner. A
dedicated supervisor now performs the fenced start, one send per invocation,
lease heartbeat, bounded polling, best-effort remote cancellation, and local
failure write-back. A remote `COMPLETED` snapshot now triggers a second trusted
`tasks/get` for the bounded result bundle. The sender validates its strict
schema, canonical Base64, counts, aggregate bytes, SHA-256 digests, and complete
Evidence-to-Artifact reference closure before any CAS write. It publishes and
lease-registers local Artifacts, preserves peer Evidence only inside an
attestation `report` Artifact, and completes the local WorkUnit to `VERIFYING`.
A standalone trusted Agent Card route resolver now implements bounded discovery,
strict Card parsing, protocol/origin validation, Gateway-compatible Ed25519
verification, exact-origin public-key pins with rotation, capability matching,
and Bearer-requirement projection without reading credentials. Production
credential preparation is a separate startup-only adapter: a versioned peer
manifest contains exact origins, rotating public pins, and optional absolute
token-file references, while the loaded provider exposes only exact-origin
lookup. Plaintext token fields, unsigned compatibility, duplicate origins, and
cross-peer token-file reuse are rejected. This loader is not yet a
`RunnerServiceSettings` or worker dependency. An isolated outbound attempt
factory now requires the strict signed-and-pinned policy, exact-origin
credential provider, CAS publisher, and a process-owned HTTP client. It composes
the resolver, Card verifier, stateless transport, result importer, and
supervisor for one already claimed WorkUnit. Valid claim fences are reused to
record context-resolution failure or cancellation, and Mission Control failure
responses are validated before the attempt is considered recovered. The
factory does not claim workspace work, invoke Harness, own the HTTP client, or
enable runtime dispatch. Production wiring remains disabled because Gateway
direct dispatch is still a compatibility path. The two paths must not both
dispatch the same attempt after cutover.

Historical unbound outbound WorkUnits are not silently rebound. They require
an explicit migration or cancellation and resubmission under a new task ID,
because choosing a new executor changes execution authority.

## Consequences

Outbound work now has an explicit local execution identity and can enter the
same Mission Control lease and admission system as other Runner work. Catalog
configuration becomes a prerequisite for creating new outbound tasks. Adapter
names may use bounded dot-separated identifiers such as `a2a.outbound`; the
catalog continues to reject URLs, credentials, and arbitrary configuration.

The current compatibility dispatch still lacks durable supervision. It must not
be removed until the worker result path and local verifier are verified. MCP
`call_agent` must remain behind that same end-to-end gate.

## Verification

Service and API tests cover exact-adapter catalog selection, namespaced adapter
validation, fail-closed submission without persistence side effects, immutable
idempotent binding snapshots, recursive inbound rejection, source/kind/adapter
root guards, atomic lease creation, claim mode, and PostgreSQL query fencing.
Execution-context tests cover the positive outbound projection and reject a
wrong source, parent shape, adapter, capability marker, owner, or expired lease
without mutating Mission state.

Resolver contract tests revalidate the claim/context identity chain, exact
target-scoped capability grants, local `a2a.send` separation, bounded wire
params, unsupported Artifact inputs, and finite content-free remote states.
They assert that resolution performs no start, network dispatch, Artifact
publication, or lifecycle completion.

Transport tests cover exact trusted-route origin matching, receiver-only bearer
use, strict and bounded JSON-RPC responses, duplicate/mismatched identity
rejection, finite remote states, and safe same-origin redirect handling. The
route-resolver tests cover signed and pinned Cards, rotation, strict schema,
protocol and origin isolation, redirect/response bounds, skill/tag completeness,
Bearer projection, and redacted trust failures. They do not enable the second
dispatch path. Peer configuration tests cover strict pinning, key rotation,
exact-origin token lookup, manifest version/field validation, duplicate origin
and token-file rejection, token bounds, and error redaction.

Supervisor tests cover exact start fencing, single dispatch per invocation,
heartbeat during polling, terminal remote failure mapping, unsupported input,
timeout, caller cancellation, transport failure redaction, invalid remote task
identity, result import failure, completion failure, and the final transition
to `VERIFYING`. Import tests cover strict bundle schema, canonical Base64,
declared and aggregate size, SHA-256, Evidence reference closure, deterministic
local IDs, CAS metadata, fenced registration responses, and remote Evidence as
attestation-only report content. No path creates local Evidence from the peer
bundle or declares the Mission successful.

Composition tests traverse the complete signed-and-pinned mocked object graph,
prove that Agent Card probes receive no bearer credential, bind the receiver
token only to the verified task origin, import and register result bytes, and
stop at `VERIFYING`. They also cover strict-policy enforcement, injected client
ownership, lease-fenced resolution failure, cancellation propagation, and
inconsistent failure-response rejection without enabling production dispatch.
