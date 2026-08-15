# Python Control Plane

`app/` contains the Python application and the Mission domain migration
boundary. It is not the permanent home of every Agent feature.

## Ownership

- `domain/`: immutable Mission, Contract, WorkUnit, Artifact, Evidence, and
  transition models.
- `repositories/`: persistence and transaction boundaries.
- `services/`: application use cases, compatibility adapters, and storage ports.
  Artifact byte verification reads Runner-owned content through this boundary;
  Mission Control retains only immutable Artifact metadata.
  Peer-facing A2A result export is a separate all-or-nothing service. It emits
  only completed PASS Evidence and referenced current-attempt Artifacts, rereads
  and verifies every byte, applies sensitivity/count/size limits, and never
  exposes content addresses.
  Mission delegation accepts only same-Mission registered ArtifactRefs and
  creates causally linked child WorkUnits behind the parent lease fence.
  Delegated Agent selection goes through a scope-aware binding port; Mission
  state stores only the Agent ID, adapter type, and capability snapshot.
  New outbound A2A roots use the same credential-free catalog before Mission
  creation, require the exact `a2a.outbound` adapter, and snapshot the selected
  local executor. Historical unbound roots are never silently rebound.
  The default binding resolver reads a workspace-scoped, credential-free
  catalog projection; catalog failures fail closed and never fall back to the
  legacy user-scoped registry. Inbound A2A admission uses the same catalog to
  select one enabled binding that has `a2a.receive` plus every requested
  capability. The Agent ID and adapter are stored on the WorkUnit as an
  execution snapshot; retries do not rebind existing work.
  Catalog mutations use workspace authorization and an atomic expected-version
  compare-and-set; request schemas reject provider credentials and raw config.
  Legacy Registry synchronization reads only Agent ID, adapter type,
  capability tags, and availability status; actor-owned rows take precedence
  over global templates and every synchronized binding uses the same CAS path.
  The Runner uses a replaceable Harness boundary for execution, renews its
  fenced lease, and cancels local work when supervision can no longer prove
  ownership. A bound Runner can atomically claim one ready delegated WorkUnit
  or a catalog-bound inbound/outbound A2A root through Mission Control. Root
  claims require the exact Mission source, WorkUnit kind, and adapter tuple;
  other root kinds remain ineligible. Claim responses are validated against
  Mission, Agent, adapter, lease owner, and attempt before execution. A replaceable
  resolver must turn durable Mission/WorkUnit references into bounded execution
  input, and a missing resolver fails the claimed unit without synthetic
  success. Harness
  owns bounded model/tool loops and explicit per-run tool
  grants resolved from Mission Contract and WorkUnit capabilities;
  ModelAdapterPort normalizes provider responses and reports provider usage;
  Harness enforces per-run token and model-cost budgets and emits request-scoped
  checkpoints; Runner must not write repository state directly. Claiming an
  inbound root only creates a fenced lease. The inbound resolver reads a
  lease-fenced Mission/Contract/WorkUnit projection and compiles a bounded JSON
  prompt with the peer objective marked as untrusted intent. Capability scope,
  criterion configuration, credentials, and Artifact bytes are excluded;
  actual tool grants remain an independent Harness input.
  Outbound A2A roots can read the same versioned projection only through the
  active lease after Mission Control rechecks their exact source, root kind,
  Agent, adapter, `a2a.send` marker, attempt, owner, and expiry. The read does
  not start execution or dispatch remotely. `a2a_outbound_runner.py` revalidates
  that projection against the claimed attempt and compiles one bounded,
  credential-free remote command. It separates local `a2a.send` authority from
  peer capability requirements and defines content-free `send/get/cancel`
  transport contracts without invoking the inbound model Harness. Artifact
  inputs fail closed until the result/input exchange contract is implemented;
  `a2a_outbound_transport.py` implements those contracts with strict JSON-RPC,
  bounded request/response bodies, same-origin 307/308 redirects, exact remote
  task identity, and origin-bound receiver credentials. Agent Card trust and
  capability verification remain an injected route-resolver responsibility.
  `a2a_peer_route_service.py` provides the standalone implementation with a
  bounded Card probe, strict schema, protocol/origin checks, Gateway-compatible
  Ed25519 verification, origin pins with key rotation, and skill/tag matching.
  Its trust policy is injected and it never loads peer credentials.
  `a2a_peer_credentials.py` supplies exact-origin Bearer lookup without exposing
  an enumerable credential map. Runner's standalone peer-manifest loader binds
  strict pins to optional mounted token files, rejects plaintext credentials
  and shared token files. `a2a_outbound_composition.py` accepts only that strict
  signed-and-pinned policy, an exact-origin credential provider, and an
  externally owned HTTP client, then composes the claimed-work resolver, trusted
  Card resolver, stateless transport, result importer, CAS publisher, and
  supervisor for one already claimed attempt. Resolution failure or caller
  cancellation is recorded behind the original lease fence when the claim
  identity is valid. The dedicated outbound supervisor performs the fenced
  `LEASED -> RUNNING` start, sends once per invocation, renews the lease while
  polling, and converts timeout, remote terminal failure, unsupported input,
  transport failure, heartbeat loss, and caller cancellation into explicit
  local failure handling. After remote
  completion it performs a separate trusted `tasks/get`, strictly validates
  the bounded bundle schema, Base64, sizes, SHA-256 digests, and Evidence-to-
  Artifact reference closure, then publishes bytes to local CAS. Remote
  Evidence is preserved only in a local attestation `report` Artifact. All
  imported Artifacts are registered behind the original lease before the
  WorkUnit moves to `VERIFYING`; they do not prove local success. This isolated
  attempt composition does not claim or poll workspace work, invoke Harness, or
  own the HTTP client. `a2a_outbound_worker.py` adds one isolated workspace poll:
  it requests only the configured Agent with the exact `a2a.outbound` adapter,
  validates the shared claim envelope and lease ownership contract, and invokes
  the native outbound attempt result without converting it to a Harness result.
  Idle and capacity-saturated polls perform no execution. The generic worker
  supervisor now depends only on the low-cardinality claim status and validates
  that status before updating readiness; inbound result behavior is unchanged.
  A serialized ASGI integration gate now exercises outbound submission through
  workspace claim, execution-context read, controlled start, signed-and-pinned
  peer routing, stateless send/result fetch, real local CAS publication,
  Artifact registration, completion to `VERIFYING`, and an idle second claim.
  It also proves that peer Evidence remains attestation-only.
  Production process wiring remains disabled until Gateway direct dispatch is
  removed in the same cutover.
  Inbound claimed execution uses a request-scoped execution plan, so its
  Harness is built from the exact Contract, WorkUnit, and attempt rather than
  falling back to Runner's fixed Sandbox Harness. `a2a.receive` remains an
  admission marker; other required capabilities must resolve to concrete
  per-attempt bindings or execution fails.
  `runner_worker.py` supervises polling for one explicitly configured workspace.
  It owns only process-local readiness, bounded idle/error backoff, and graceful
  stop. Mission Control authorizes scope, balances ready work by active and
  unverified Mission load, and locks the selected Mission and WorkUnit
  atomically. The worker consumes that claim without persisting a queue,
  scanning Missions, or replacing lease state. Independent Runner principals
  receive the explicit workspace ACL permission `mission:claim`; Mission
  Control reads that grant on every new claim, while an active lease remains
  the sole authority for subsequent execution commands. IAM tenant
  `max_concurrent` quota is enforced against live, non-expired Runner leases in
  the same claim transaction without a durable capacity counter. Successful
  claim responses distinguish `claimed`, `idle`, and `capacity_saturated`;
  Runner consumes that transient result for sanitized process-local readiness
  counters only.
  Independent verifier principals use the separate explicit workspace ACL
  permission `mission:verify`. Mission Control checks it before Artifact I/O or
  mutation, fails closed when IAM is unavailable, and still requires the token
  subject to match `verifierId`. Outside the narrow discovery projection, this
  grant permits only Evidence admission; it does not grant Mission listing,
  Runner claims, or ordinary workspace access. The verifier discovery command
  extends that grant only to one minimal,
  current-attempt `VERIFYING` context. Its short database lock creates no
  durable claim, and its projection excludes Mission source, Contract
  repository/capability grants, execution leases, credentials, and Artifact
  bytes. The projection also carries a fail-closed evaluation-policy decision.
  Mission Control currently recognizes only the deterministic
  `artifact-set.v1` evaluator, whose canonical configuration digest binds one
  acceptance criterion to WorkUnit kind and Artifact requirements. PASS
  Evidence must reproduce that criterion and digest before Artifact I/O and
  again inside the state-transition transaction; unsupported, ambiguous, or
  unsatisfied policies cannot be bypassed through the direct verification API.
  This evaluator proves Artifact-set availability and byte integrity only, not
  semantic correctness or test execution. PASS also runs the controlled
  `artifact-set.v1` evaluator over the exact byte-verification results before
  and during transactional admission. Missing, extra, duplicate, wrong-digest,
  or wrong-size results fail without Evidence or state mutation; successful
  observations are canonicalized by Artifact ID for the future Evidence
  integrity envelope. Mission Control now owns that versioned envelope: it
  hashes Evidence/Mission/Contract identity, WorkUnit attempt, verifier and
  verdict, canonical Artifact byte observations, controlled PASS evaluation,
  summary, and generation time. The request's deprecated `integrityHash` is
  optional and ignored; only the server-generated digest is persisted. This is
  tamper-evident hashing, not verifier authentication or a signature.
  `verifier_service.py` adds the independent application-layer coordinator and
  its narrow Mission Control HTTP port. It strictly validates discovery,
  verifies the projected Artifact bytes, replays only the registered
  `artifact-set.v1` evaluator, and submits PASS only from that controlled
  result. It rejects inconclusive policies before submission because the
  current inconclusive projection has no unambiguous criterion attribution.
  `verifier_worker.py` supervises one explicit workspace with bounded backoff,
  graceful drain, cancellation propagation, and content-free snapshots. It
  owns no queue or verifier lease; Mission Control's verification transaction
  remains the duplicate-admission and lifecycle authority. These modules are
  pure runtime boundaries and are not yet composed into a deployable verifier
  process.
  `mcp_tool_adapter.py` is a stateless MCP client/tool adapter; it forwards
  Mission/WorkUnit/capability context and emits content-free call audit events.
  `build_mcp_capability_binding` composes it with Contract/WorkUnit capability
  resolution so scope is applied at call time. Evidence admission rechecks
  Artifact ownership against the verified WorkUnit and attempt before and
  after byte verification; expired delegated attempts cannot satisfy a retry.
- `api/`: HTTP/WebSocket transport; handlers must delegate to services.
- `schemas/`: versioned request and response validation.
- `compat/`: one-way adapters from legacy Task/DAG data into Mission objects.
- `core/`, `db/`, `utils/`: shared application infrastructure.

New execution behavior must enter through Mission/WorkUnit services. Do not add
new business state to the legacy LangGraph task state machine.

## Change checklist

1. Update domain transitions and persistence together.
2. Append an event for every durable state transition.
3. Add domain, persistence, and API tests as applicable.
4. Update the nearest contract or ADR when ownership changes.
