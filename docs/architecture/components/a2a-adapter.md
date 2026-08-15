# A2A Adapter

> Status: implemented  
> Owner: protocol and Mission Control maintainers  
> Last reviewed: 2026-08-15

## Responsibility

The Go Gateway adapts external A2A Agent Cards and JSON-RPC task operations to
the Mission Control boundary. It publishes the AgentHub card, maintains only
Agent Card routing metadata, and separates local outbound task commands from
the peer-facing inbox. It is not a scheduler, execution engine, or second task
database.

## Inputs and outputs

- Outbound input: an authenticated JSON-RPC 2.0 request at
  `/platform/a2a/tasks` with a non-empty method, workspace, objective message,
  and configured external `agentUrl`/`target` for `tasks/send`.
- Inbound input: an authenticated peer request at `/platform/a2a/inbox` with a
  task ID, workspace, objective message, and origin-only `sourceAgentUrl`.
  Outbound-only `agentUrl` and `target` fields are rejected.
- Inbound result read: `tasks/get` uses the same workspace, source origin, task
  ID, peer Card trust, and authorization boundary as submit and cancel.
- Capability input: optional `requiredCapabilities`, strictly decoded as a
  JSON string array. Values are trimmed, non-empty, and unique
  case-insensitively before Mission submission and remote forwarding.
- Discovery input: `GET /.well-known/agent-card.json` from the configured
  HTTP(S) origin. The response is bounded to 1 MiB, must declare protocol major
  version 1, and must keep its card URL and task API on the same scheme/host.
- Output: an A2A task projection returned from Mission Control. Non-terminal
  responses contain status only. A completed inbound task may include the
  bounded Artifact/Evidence result bundle defined by ADR-0052.
- Remote response contract: task responses are bounded to 1 MiB, must use
  JSON-RPC 2.0, must echo the Gateway-generated request ID, and must contain a
  `result` or `error` object.

## Dispatch flow

Outbound `tasks/send` submits the idempotent task to Mission Control first. The
control plane first selects an enabled credential-free workspace catalog
binding with the exact `a2a.outbound` adapter and `a2a.send`. New state is not
persisted when no matching local executor exists. The Agent ID and adapter are
stored on the root `a2a.delegate` WorkUnit as an execution snapshot, and
idempotent retries preserve it. Capabilities requested from the remote Agent
remain in the Contract/WorkUnit and are checked against its Agent Card; they are
not misrepresented as capabilities implemented by the local transport worker.

The Gateway then probes the Agent Card, verifies required skills/tags, and
verifies signed cards with Ed25519. Only after those checks does it call the
card's declared task endpoint. Before dispatch it removes `agentUrl`/`target`, binds
`sourceAgentUrl` to its own signed Card URL, and uses only the receiver-issued
credential configured for that peer origin. Probe, compatibility, signature,
capability, authentication, and remote
dispatch failures are written back as `FAILED` through the control plane. The
remote response is never treated as independent completion evidence.

Mission Control can now atomically claim a catalog-bound outbound root only
when the Mission source is `a2a`, the WorkUnit kind is `a2a.delegate`, and the
assigned adapter is `a2a.outbound`. Existing binding, dependency, ACL, tenant
concurrency, row-lock, lease, attempt, and event rules remain mandatory. The
current Gateway request-path dispatch is a compatibility path while the
Runner-supervised heartbeat, polling, cancellation, and result-import loop from
ADR-0053 is implemented. Claim eligibility alone does not authorize a second
dispatch path.

The active outbound lease owner may read a versioned Mission/Contract/WorkUnit
execution projection. Mission Control requires the same `a2a` source,
`a2a.delegate` root, assigned Agent, `a2a.outbound` adapter, and `a2a.send`
marker, then fences the read by WorkUnit state, attempt, lease ID, owner, and
expiry. This read is side-effect free and contains no peer credentials. It is
input to the outbound protocol resolver, not permission for Gateway and Runner
to dispatch the same attempt. The resolver now rechecks the complete identity
chain and creates a size-bounded, credential-free command containing only task
routing, the objective message, and peer capability requirements. Local
`a2a.send`, lease/Runner identity, Contract budgets, capability scopes, and
credentials are not sent. A stateless `send/get/cancel` port and finite remote
state projection are defined. Its HTTP adapter rejects malformed or duplicate
JSON fields, mismatched request/task IDs, unsupported states, oversized bodies,
unsafe redirect statuses, cross-origin redirects, and missing receiver-issued
credentials. Agent Card verification is supplied through a trusted-route port;
the transport cannot choose or alter the requested peer origin. The standalone
Python resolver performs a bounded `/.well-known/agent-card.json` probe,
rejects duplicate or unknown fields, enforces protocol major version 1 and
same-origin Card/task routes, verifies Gateway-compatible Ed25519 signatures,
applies exact-origin pins with rotation, and checks required skill IDs/tags.
It projects only route and Bearer-required metadata and never reads credentials.
The credential adapter retains receiver-issued tokens behind exact-origin lookup
only. Runner's versioned peer loader accepts strict public pins and optional
absolute token-file references, rejects plaintext token fields, duplicate
canonical origins, and cross-peer token-file reuse, and suppresses underlying
validation/file errors that could disclose configuration or secret content.
An isolated Runner attempt factory now composes the claimed-work resolver,
trusted Card resolver, exact-origin credential provider, stateless transport,
result importer, CAS publisher, and supervisor. It requires a signed-and-pinned
policy and an injected HTTP client whose lifecycle remains process-owned. It
operates only on an already claimed WorkUnit; it neither polls workspace work
nor creates a second production dispatch path. Resolution failure and caller
cancellation are written back through the validated claim lease when possible.
An isolated outbound workspace coordinator now performs one exact Agent plus
`a2a.outbound` claim, reuses the shared claim-status/binding/lease validation,
and passes claimed work to that attempt factory. It preserves the native A2A
supervision result rather than fabricating a Harness result. Idle or tenant-
capacity outcomes do not invoke the attempt. The coordinator can satisfy the
generic worker polling protocol but is not composed by the live process.
Production worker wiring remains disabled while Gateway direct dispatch remains
active. A dedicated Runner supervisor owns the bounded execution loop after
resolution: it performs the fenced local start, sends once per
invocation, renews the lease during polling, attempts remote cancellation on
timeout or supervision loss, and records terminal failures through Mission
Control. Remote `COMPLETED` triggers a separate bounded `tasks/get`; the sender
validates the complete bundle before side effects, publishes verified bytes to
its own CAS, registers attempt-local Artifact metadata through Mission Control,
and completes only to `VERIFYING`. Peer Evidence is retained in a report
Artifact as a remote attestation and never becomes local Evidence authority.

`tasks/cancel` first cancels the durable Mission task and then best-effort
forwards cancellation with the same route-field cleanup, Card/origin checks,
and receiver-issued authentication. A remote cancel failure is logged because
the durable cancellation already owns the lifecycle.

Inbound `tasks/send` verifies the caller's source Card against the receiver's
trust policy and checks requested capabilities against the receiver Card. It
then creates an idempotent `a2a.inbound` Mission source and `a2a.inbound`
WorkUnit through Mission Control. Before creating new durable state, Mission
Control selects one enabled binding from the credential-free workspace catalog.
The binding must contain the internal `a2a.receive` capability and every
capability requested by the peer. Selection is workspace-isolated and
deterministic; an unavailable catalog or no complete match returns 503 without
creating a new Mission. The Agent ID and non-secret adapter type are persisted
on the WorkUnit as an execution snapshot. Idempotent retries preserve that
snapshot instead of rebinding against later catalog changes.

The selected inbound adapter is a local execution adapter, never
`a2a.outbound`, so a
received task cannot recursively delegate itself. Inbound identity is
`(workspace, source agent origin, external task ID)`, so two peers may use the
same task ID without collision. Inbound `tasks/cancel` must provide the same
source origin and can only cancel that directionally isolated mapping. The
Mission Control claim path admits a bound root only when both its Mission source
and WorkUnit kind are `a2a.inbound`. It applies the same exact Agent/adapter
binding, dependency readiness, row lock, lease, attempt, and event rules used
for delegated children. Other root kinds remain ineligible.

Claim only transfers fenced execution ownership to Runner. It does not turn the
peer objective into executable code, produce an Artifact, or prove completion.
The inbound `ClaimedWorkResolver` obtains a read-only execution projection only
for the active lease owner, rechecks Mission, Contract, WorkUnit, attempt, and
lease identity, and compiles bounded deterministic JSON with `language=text`.
It includes Artifact IDs and digests, not bytes, and strips capability scope,
criterion configuration, repository scope, and arbitrary provider config.
Tool authority is resolved independently by Harness. The Artifact/Evidence path
remains a separate gate and executing agents still cannot verify themselves.

Inbound `tasks/get` is also source-origin isolated and reads no Gateway-local
task state. Mission Control emits results only after the mapped Mission and
WorkUnit succeed. It selects PASS Evidence for that WorkUnit and only the
current-attempt Artifacts referenced by that Evidence. Artifact ID/digest,
ownership, attempt, and sensitivity are checked before every selected byte
stream is reread and verified for registered size and SHA-256. The response is
all-or-nothing and bounded to 20 Artifacts, 20 Evidence records, 512 KiB raw
bytes, and 900 KiB encoded bundle JSON. Content addresses and storage metadata
remain private. Receiver-side export does not make remote Evidence authoritative
for an outbound Mission; sender-side import and local verification remain a
separate gate.

The inbound Runner composition root builds one Harness per claimed attempt.
`a2a.receive` proves admission eligibility but is not a callable model tool.
Every other WorkUnit requirement must resolve through a per-attempt capability
binding factory; the exact tools are also passed to the model factory. Missing
bindings, invalid model factories, or recursive `a2a.outbound` adapter
configuration fail closed. The independently runnable Python Runner process
hosts this inbound composition. It still rejects `a2a.outbound` until the
dedicated protocol resolver and supervised remote lifecycle are complete.

## Security and ownership

Signed cards require valid hex-encoded Ed25519 public key and signature bytes.
Unsigned cards are rejected by default; local compatibility requires the
explicit `A2A_ALLOW_UNSIGNED_CARDS=true` setting and emits a warning plus a
low-cardinality trust metric. `A2A_TRUSTED_PUBLIC_KEYS_JSON` binds an origin to
one or more allowed Ed25519 public keys, allowing overlap during key rotation.
Configured pins are always enforced for their origin;
`A2A_REQUIRE_PINNED_KEYS=true` additionally rejects signed cards from origins
without pins. Invalid trust configuration prevents Gateway startup.

URL parsing rejects non-HTTP(S), user-info, and fragment-bearing URLs; Agent
Card probes and authenticated task requests do not follow cross-origin
redirects. Inbound Gateway authorization is used for local Mission Control
calls and is not copied into an external Agent request.
`A2A_PEER_BEARER_TOKEN_FILES_JSON` maps an exact peer origin to a bounded,
single-line token file. If a peer Card advertises Bearer auth, missing
origin-bound credentials fail the durable outbound task; Gateway never falls
back to the caller token. Peer credentials are startup-only protocol config and
never enter Registry, Card, Mission, status, or logs. `GET /trust-status`
reports policy flags and the number of pinned
origins plus a `self_card_signed` boolean, but never returns key material.

AgentHub can sign its own published Card from a read-only Secret file selected
by `A2A_CARD_SIGNING_KEY_FILE`. The file contains a hex Ed25519 seed or private
key and is loaded only at startup through the `A2ACardSigner` boundary.
`A2A_REQUIRE_SIGNED_SELF_CARD=true` makes the signing identity mandatory for a
deployment.

Alternatively, `A2A_CARD_SIGNER_URL` selects a purpose-bound remote signing
service backed by a non-exportable KMS/HSM key. It is mutually exclusive with
the local key file and requires a key ID plus a bearer token read from a
bounded Secret file. The endpoint only receives `public_key` and `sign`
operations for the fixed `a2a_agent_card_v1` purpose. HTTPS is mandatory except
for an explicitly enabled loopback development endpoint; redirects, oversized
or non-JSON responses, unknown response fields, changed key versions, and
signatures that do not match the returned public key fail Gateway startup.
Only non-secret key ID/version and public key metadata enter the Card. The
current plaintext-reveal IAM secret API is not part of either path.

The signed public Card is mounted at the A2A-standard root path
`/.well-known/agent-card.json` without authentication. Its declared task API is
the authenticated `/platform/a2a/inbox`; only local authenticated callers use
`/platform/a2a/tasks` for outbound routing.

Mission Control owns Mission, WorkUnit, lease, Artifact, Evidence, and terminal
state. Runner and Harness own execution. The adapter owns only protocol
translation, bounded discovery, and routing metadata. Full external A2A
interoperability/conformance certification remains a separate release gate.
The catalog contains capability and adapter metadata only; provider credentials
remain outside Mission and WorkUnit state.

## Verification

Gateway tests cover durable submit-before-probe ordering, capability
propagation and mismatch failure, JSON-RPC envelope and remote response ID
validation, protocol and same-origin checks, and valid/invalid/unsupported
Ed25519 signatures. Trust tests cover fail-closed unsigned behavior, explicit
compatibility, public-key rotation, pin mismatch, configuration validation,
registration rejection, self-Card signing, remote non-exportable signing,
strict peer verification, signer redirect rejection, mismatched-signature
rejection, status redaction, origin-bound peer credential loading, caller-token
non-forwarding, route-field isolation, and a two-Gateway signed/strict-pinned
submit, cancel, and remote-failure loop without recursive delegation.
Python adapter tests cover workspace-scoped, capability-complete deterministic
binding, exact outbound adapter filtering, fail-closed catalog behavior without
new persistence side effects, recursive inbound rejection, and idempotent
preservation of WorkUnit binding snapshots. Mission Control tests cover root
inbound and outbound claim eligibility, source/kind/adapter guards, exact
binding, atomic lease events, and continued delegated-child behavior.
Result exchange tests cover source-bound status reads, PASS/current-attempt
selection, sensitivity and digest policy, local and MinIO byte integrity,
response limits, typed Gateway projection, and all-or-nothing failure.
Outbound Runner tests cover claim/context identity drift, target-scoped grants,
local-versus-peer capability separation, request bounds, unsupported Artifact
inputs, finite transport contracts, supervised lifecycle polling, lease
heartbeat, timeout, cancellation, failure write-back, strict result import,
fenced registration, and completion to `VERIFYING`. Stateless HTTP transport
tests cover
receiver-token isolation, route-origin pinning, content-free send/get/cancel,
completed-only result fetch, same-origin redirects, response limits, JSON-RPC
identity, remote errors, and unsupported remote states. Import tests cover
schema, canonical Base64, aggregate bytes, SHA-256, reference closure, CAS
metadata, attestation-only Evidence, and registration response fencing.
