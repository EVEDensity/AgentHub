# ADR-0037: Probe And Verify A2A Agent Cards Before Forwarding

> Status: accepted  
> Owner: protocol and Mission Control maintainers  
> Date: 2026-08-15  
> Scope: Go Gateway A2A task adapter and external Agent Card interoperability

## Context

The A2A Gateway persists an inbound task in Mission Control before dispatching
it to an external Agent. Forwarding directly to a configured URL is not enough:
the URL may advertise an incompatible protocol, omit a capability required by
the durable WorkUnit, or point task traffic at an unrelated origin. Agent Card
signatures also need real verification; accepting arbitrary signature strings
would make the registry security metadata meaningless.

## Decision

Before `tasks/send` forwards a request, the Gateway performs these checks:

1. It requires a JSON-RPC 2.0 request envelope with a non-empty method before
   any Mission operation.
2. It reads `/.well-known/agent-card.json` from the configured HTTP(S) origin
   and limits the response to 1 MiB.
3. It accepts only A2A protocol major version `1` and requires the card URL and
   declared task API to use the configured agent's same scheme and host.
4. It parses `requiredCapabilities` as a strict JSON string array, trims values,
   rejects empty or duplicate entries, persists the normalized list in Mission
   Control, and requires each capability to match a skill ID or tag in the
   Agent Card (case-insensitively).
5. Signed cards must declare `ed25519` (or omit the algorithm, which defaults to
   Ed25519), provide valid hex-encoded key/signature bytes, and pass Ed25519
   verification over the card with its signature field cleared. Unsigned cards
   remain accepted for compatibility and are logged as warnings.
6. Mission Control submission happens first. Any probe, compatibility,
   capability, signature, or remote dispatch failure is written back through
   the control plane as `FAILED`; the remote task endpoint is not called when a
   probe fails.

Remote task responses are bounded to 1 MiB and must be JSON-RPC 2.0 responses
whose ID exactly matches the Gateway request ID and which contain either a
result or an error. A malformed response is a dispatch failure, even when the
HTTP status is successful.

Cancellation reuses the same Agent Card and origin checks but has no required
capability list. The Agent Card is protocol metadata only; Mission, WorkUnit,
lease, Artifact, Evidence, and Outcome state remain owned by Mission Control,
Runner, and the execution boundary.

## Consequences

External agents must publish a usable Agent Card and keep its task endpoint on
the registered origin. Capability errors become durable, observable task
failures instead of ambiguous remote calls. Unsigned legacy agents continue to
interoperate, while signed deployments receive actual cryptographic
verification. The adapter still does not claim completion from a remote A2A
response; clients observe the persisted lifecycle separately.

## Alternatives considered

- Forwarding to the configured URL without probing was rejected because it
  cannot enforce capability or protocol compatibility and permits endpoint
  drift.
- Treating a signature as an opaque audit field was rejected because callers
  could not distinguish authentic metadata from tampered metadata.
- Failing before Mission submission was rejected because dispatch and probe
  failures must be visible as durable failures and recoverable by operators.

## Verification

- Gateway tests cover capability propagation, strict capability parsing,
  unsupported capability failure before `/tasks`, cross-origin task API
  rejection, protocol version rejection, and valid/invalid/unsupported
  Ed25519 signatures.
- Existing Mission Control and A2A lifecycle tests remain the source of truth
  for idempotent submit, cancellation, and failure write-back behavior.

## Supersedes

None.
