# Services

`services/` contains independently runnable services grouped by runtime. A
service may own transport, compute, or infrastructure behavior, but it must
not create a competing Mission or WorkUnit state machine.

## Runtime groups

- `go/`: gateway, protocol adapters, realtime delivery, IAM, permissions,
  runtime control, audit, session, and shared event/database libraries.
- `python/`: model adapters, document and knowledge pipelines, summarization,
  and batch evaluation.
- `rust/`: isolated performance cores. Rust services communicate through stable
  contracts or events and do not own orchestration policy.

## Ownership rules

- Gateway authenticates and routes; it does not invent task completion. Its A2A
  inbox can project a completed Mission Control result bundle through
  `tasks/get`, but it neither stores that bundle nor treats peer Evidence as
  local completion authority. Outbound root WorkUnits are bound to an exact
  credential-free `a2a.outbound` catalog entry and are claimable through
  Mission Control. Gateway request-path dispatch remains compatibility behavior
  until ADR-0053's Runner-supervised remote lifecycle is complete.
- Mission Control owns durable execution state.
- Harness and Runner own execution attempts, isolation, and evidence capture.
- The Python Runner service is a strict workspace-scoped process adapter. It
  loads control, AI Gateway, and MCP credentials from separate mounted files,
  rejects mock model routing, binds Stateless MCP tools per attempt, and
  publishes only sanitized health state. Mission Control discovers and leases
  eligible work; the process is not a global queue or fleet scheduler.
- The Python Verifier service is an independently authenticated,
  workspace-scoped process adapter. It reads a mounted verifier token and a
  read-only shared local Artifact CAS, reproduces only registered deterministic
  evaluation, and publishes sanitized health state. It owns no verifier lease,
  queue, Evidence store, or Mission lifecycle state; Mission Control serializes
  verification admission.
- The Python Decision expiry service is a Mission Control maintenance process.
  It drains persisted expiry eligibility directly through the existing
  transactional command, owns no queue or cursor, and publishes only sanitized
  operational state. Its process configuration cannot revise the durable
  `expiresAt` selected when a Decision was created.
- MCP and A2A services are protocol boundaries, not business databases.
- Gateway evaluates external A2A Agent Cards against an immutable startup
  trust policy. Unsigned cards fail closed by default; optional origin-bound
  Ed25519 pins and redacted trust status remain protocol/security metadata and
  never enter Mission or WorkUnit state. AgentHub's own Card can be signed from
  a read-only installation Secret or a purpose-bound remote signer backed by a
  non-exportable KMS/HSM key. Remote signing publishes only key ID/version and
  public key metadata; signer credentials and private keys remain outside the
  Card and all Mission state. Receiver-issued peer bearer tokens are loaded
  from origin-bound read-only files and are used only for outbound A2A inbox
  calls; caller credentials are never forwarded to peers.
- The MCP Gateway's `/mcp/rpc` endpoint is stateless and requires per-call
  Mission/WorkUnit/attempt/capability context plus a platform IAM Bearer token;
  tenant-scoped tools propagate verified tenant/actor identity without a
  synthetic default. Its SSE session endpoints remain compatibility transport
  only. Session listing enters through the authenticated Gateway
  `/platform/sessions` proxy and is persisted/read by `session-service`.
  Agent listing enters through Gateway `/platform/agent-registry`, which emits
  a credential-free, actor-scoped projection of the configured Agent catalog;
  AgentNet and Realtime role registries are not catalog truth.
  MCP `call_agent` is a controlled `agent.delegate` command: it forwards an
  explicit ArtifactRef-based child WorkUnit delegation to Mission Control and
  never runs a model loop or reports completion.
- NATS subjects and payloads require versioned contracts before production use.

Before adding a service, document its independent scaling, security, failure, or
runtime boundary in an ADR. Prefer a module in the control plane until data
proves a service boundary is needed.
