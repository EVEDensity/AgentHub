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
- Capability input: optional `requiredCapabilities`, strictly decoded as a
  JSON string array. Values are trimmed, non-empty, and unique
  case-insensitively before Mission submission and remote forwarding.
- Discovery input: `GET /.well-known/agent-card.json` from the configured
  HTTP(S) origin. The response is bounded to 1 MiB, must declare protocol major
  version 1, and must keep its card URL and task API on the same scheme/host.
- Output: an A2A task projection returned from Mission Control. A successful
  response means the task was accepted or its current durable state was read;
  it does not prove remote execution, Artifact production, Evidence, or
  Outcome acceptance.
- Remote response contract: task responses are bounded to 1 MiB, must use
  JSON-RPC 2.0, must echo the Gateway-generated request ID, and must contain a
  `result` or `error` object.

## Dispatch flow

Outbound `tasks/send` submits the idempotent task to Mission Control first. The
Gateway then probes the Agent Card, verifies required skills/tags, and verifies
signed cards with Ed25519. Only after those checks does it call the card's
declared task endpoint. Before dispatch it removes `agentUrl`/`target`, binds
`sourceAgentUrl` to its own signed Card URL, and uses only the receiver-issued
credential configured for that peer origin. Probe, compatibility, signature,
capability, authentication, and remote
dispatch failures are written back as `FAILED` through the control plane. The
remote response is never treated as independent completion evidence.

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

The selected adapter is a local execution adapter, never `a2a.outbound`, so a
received task cannot recursively delegate itself. Inbound identity is
`(workspace, source agent origin, external task ID)`, so two peers may use the
same task ID without collision. Inbound `tasks/cancel` must provide the same
source origin and can only cancel that directionally isolated mapping. The
current Runner claim path still admits delegated child WorkUnits only; bound
root `a2a.inbound` work is not automatically claimed yet.

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
binding, fail-closed catalog behavior without new persistence side effects, and
idempotent preservation of the WorkUnit binding snapshot.
