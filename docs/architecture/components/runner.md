# Runner Component

> Status: implemented  
> Owner: execution maintainers  
> Last reviewed: 2026-08-16

## Responsibility

Runner owns one isolated execution attempt: lease-fenced start and heartbeat,
request-scoped Harness supervision, Artifact byte publication, metadata
registration, and completion to VERIFYING. It reports every durable change
through Mission Control and cannot create Evidence or declare success. Its
inbound Harness adapter submits content-minimized execution checkpoints through
the same lease fence; Runner never persists the request-scoped Harness journal.

`RunnerWorker` is the process-local polling supervisor. The current minimum
worker passes one explicitly configured workspace to `claim_ready_and_run`.
Mission Control atomically discovers and leases eligible work across Missions;
Runner neither lists Missions nor retains a ready queue. The Mission-scoped
`claim_and_run` entry remains a compatibility API, not the process polling path.

## Inputs and outputs

- Input: a bound `WorkUnitRunner`, explicit workspace ID, lease duration, and
  bounded idle/error delay configuration.
- Output: lease-fenced Runner commands and a content-minimized in-process status
  snapshot for liveness/readiness adapters.
- Snapshot fields contain counters, timestamps, delay, and exception type only;
  they never contain objective, prompt, tool arguments, provider response, or
  credential content.

## Polling and shutdown

A successful `idle` or `capacity_saturated` claim marks the control path ready
and exponentially backs off to the configured maximum. The snapshot counts the
two outcomes separately and exposes only the last low-cardinality status. A
`claimed` WorkUnit resets delay to the minimum. Any non-cancellation failure
marks the worker unready, increments failure counters, stores only the
exception type, and continues with bounded backoff.

A requested stop prevents another poll but waits for an active claim to finish.
Task cancellation propagates into `WorkUnitRunner`; Runner's existing
cancellation supervision records the leased attempt as failed before re-raising
when Mission Control is reachable. The worker never swallows cancellation.

## Process boundary

`services/python/runner_service/` is the strict minimum process adapter. It
requires explicit identity and URL configuration, loads separate Mission
Control, AI Gateway, and MCP credentials from mounted files, rejects mock model
routing, forwards the exact resolved tool schemas, builds Stateless MCP
bindings per attempt, and exposes sanitized `/healthz` and `/readyz` probes.
Shutdown drains the active claim until a bounded deadline and then cancels it
through the existing Runner supervision path.

This is a workspace-scoped deployment candidate, not a production fleet
scheduler. Its readiness proves a successful Mission Control claim request; it
does not execute model or tool probes outside a WorkUnit. Replicas may share a
workspace and binding because Mission Control owns claim locking and fairness.

## Ready-work discovery

Mission Control now exposes a workspace-scoped atomic discovery contract. It
filters by immutable Agent/adapter binding, allows only delegated or eligible
inbound/outbound A2A root work, checks dependency readiness, orders by least
in-flight Mission load, and locks the owning Mission plus candidate WorkUnit
with `SKIP LOCKED`. Authentication supplies the lease owner; callers cannot
provide one in the request.

Outbound eligibility requires the exact `a2a` Mission source,
`a2a.delegate` root kind, and `a2a.outbound` adapter combination. The current
Python process composition intentionally still rejects that adapter until the
Gateway-to-Runner dispatch cutover is atomic; claim support is not a fallback
to the inbound model Harness. The standalone Agent Card route resolver is now
implemented and tested. A separate versioned peer-manifest loader now creates a
strict signed-and-pinned policy and exact-origin receiver credential provider
from mounted files without accepting plaintext tokens. Neither component is
production wiring authority by itself. An isolated outbound attempt factory now
requires those strict trust inputs and an injected process-owned HTTP client,
then composes the resolver, trusted route, stateless transport, result importer,
CAS publisher, and supervisor without Harness or `WorkUnitRunner`.

The claimed outbound lease owner can retrieve the versioned execution context
after Mission Control revalidates the complete source/kind/binding/capability
and lease identity chain. Context retrieval is read-only; it does not start the
WorkUnit or call a remote peer.

The outbound claimed-work resolver now validates that projection again against
the claimed Mission, WorkUnit, attempt, Agent, adapter, capability set, lease,
Contract, and target-scoped grants. It emits a bounded credential-free command
and a local supervision identity; `a2a.send` is removed from peer requirements
because it is local transport authority. A separate stateless transport port
defines finite `send/get/cancel` inputs and content-free lifecycle responses.
The HTTP adapter now enforces bounded JSON-RPC envelopes and bodies, exact
request/task identity, finite states, safe same-origin redirects, and
receiver-issued bearer credentials scoped to the verified peer origin. It
accepts only a route produced by an injected Agent Card trust/capability
resolver, so Runner does not duplicate Gateway's trust policy. The outbound
supervisor now starts the claimed attempt through Mission Control, dispatches
once, heartbeats its lease while polling, and propagates timeout, remote
failure, unsupported input, transport/heartbeat failure, and caller
cancellation through fenced local failure handling. A remote `COMPLETED`
snapshot triggers a separate trusted result fetch. Runner rejects schema,
Base64, count, byte, digest, and Evidence-reference drift before publishing any
bytes. It then publishes every remote Artifact to local CAS, registers the
local metadata behind the active attempt lease, and stores remote Evidence only
as an attestation `report` Artifact. Completion uses all local references and
moves the WorkUnit to `VERIFYING`; only an independent local verifier may move
it to `SUCCEEDED`. This path does not invoke Harness or `WorkUnitRunner`.
The attempt factory also records context-resolution failure or cancellation
behind the validated claim fence and rejects inconsistent Mission Control
failure responses. It accepts only already claimed work and owns neither ready-
work polling nor HTTP-client lifetime. A separate outbound workspace coordinator
now performs one exact Agent/`a2a.outbound` claim, applies the same claim envelope,
binding, lease-owner, and status checks as inbound execution, and returns the
native supervision result. The process-local `RunnerWorker` consumes only the
validated low-cardinality claim status, so it can supervise either result type
without making them interchangeable. Production process wiring remains
disabled, so Gateway and Runner cannot dispatch the same attempt.

An ASGI integration gate now drives the isolated outbound coordinator through
real Mission Control HTTP serialization and a mocked remote HTTP peer. It
verifies the full claim-to-`VERIFYING` chain, actual local CAS bytes and digests,
two lease-fenced Artifact registrations, attestation-only remote Evidence, and
an idle second claim that cannot resend the task. This is a composition gate;
it does not enable the outbound adapter in the production process.

The Runner service now also exposes an explicit low-level outbound runtime
candidate for lifecycle testing. It composes the outbound coordinator with a
caller-supplied Mission Control port, CAS publisher, loaded strict peer policy,
peer HTTP client, and resources whose ownership is transferred to the runtime.
Shutdown first drains the worker, then cancels an over-deadline claim while the
peer client is still open for cancellation handling, and only then closes peer
and control resources. Closed or duplicate resources fail before startup. The
candidate is not referenced by `create_app`; production settings continue to
reject `a2a.outbound`.

The process worker consumes this endpoint with explicit workspace scope. It
derives the Mission ID only from the claimed WorkUnit, validates lease owner,
binding, state, and Mission identity, then reuses the existing context, start,
heartbeat, cancellation, Artifact, and completion path. Priority and
Agent-specific capacity routing remain future Mission Control policies beyond
the tenant concurrency ceiling described below.

Each independently identifiable Runner principal receives the explicit
`mission:claim` permission through `platform_workspace_members`; no
non-break-glass built-in role receives it by default. Mission Control reads
that ACL on every new claim and fails closed when the authorization store is
unavailable. Removing the permission blocks the next claim. Commands for an
already claimed attempt are authorized by the active lease owner and lease ID,
then rechecked inside the Mission Control transaction. The grant does not allow
Mission listing or ordinary workspace access.

## Concurrency admission

Workspace claims resolve the tenant's effective `max_concurrent` from IAM plan
quota truth and tenant overrides. A positive limit is enforced transactionally
against non-expired `LEASED` and `RUNNING` WorkUnits across all tenant
workspaces. Mission Control briefly serializes bounded claims per tenant to
prevent concurrent over-admission; it does not serialize execution or persist a
capacity counter. `0` means unlimited and retains the existing `SKIP LOCKED`
claim path.

Quota resolution and active-state reads fail closed. A successful claim returns
`claimed`, `idle`, or `capacity_saturated` under the versioned response
contract. Runner validates the status/payload pair before execution and applies
the same bounded backoff to both empty outcomes without becoming unready.
Operational counters are process-local and must not be represented as Mission
state, quota usage truth, or a scheduling cursor.
