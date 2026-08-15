# ADR-0038: Enforce An Origin-Bound A2A Agent Card Trust Policy

> Status: accepted  
> Owner: protocol and security maintainers  
> Date: 2026-08-15  
> Scope: Gateway startup configuration, A2A registration, discovery, dispatch,
> and operational trust reporting

## Context

Ed25519 verification proves that an Agent Card was signed by the private key
corresponding to the public key embedded in that same card. It does not prove
that the key belongs to the configured external Agent. Accepting unsigned cards
or arbitrary self-declared keys by default leaves dispatch vulnerable to Agent
identity substitution even when the signature bytes are valid.

The trust boundary also needs a practical key-rotation mechanism. Replacing one
pin atomically creates either downtime or a window where the old or new Agent
cannot be verified.

## Decision

Gateway constructs one immutable `A2ATrustPolicy` at startup and injects it into
the A2A handler. The same policy is used for registry writes, live Agent Card
probes, remote task forwarding, cancellation probes, and bulk verification.

- Unsigned Agent Cards are rejected by default. Compatibility requires the
  explicit `A2A_ALLOW_UNSIGNED_CARDS=true` setting.
- `A2A_TRUSTED_PUBLIC_KEYS_JSON` is a JSON object mapping an HTTP(S) origin to
  one or more hex-encoded Ed25519 public keys. When an origin has configured
  pins, every signed card for that origin must match one of them.
- Multiple keys for one origin are valid simultaneously so an old and new key
  can overlap during rotation.
- `A2A_REQUIRE_PINNED_KEYS=true` requires every signed external origin to have
  at least one configured pin. Enabling it without a pin map is a startup error.
- Malformed booleans, JSON, origins, empty key lists, and invalid Ed25519 keys
  fail Gateway startup instead of degrading trust silently.
- Trust decisions emit the low-cardinality
  `a2a_agent_trust_decisions_total{decision}` metric. Unsigned compatibility is
  also logged. `GET /platform/a2a/trust-status` reports only policy flags and
  pinned-origin count; it never exposes key material.

Cryptographic verification remains separate from trust evaluation so tests and
diagnostics can distinguish malformed signatures from valid but untrusted
keys. Mission Control remains the lifecycle source of truth; a trust rejection
after durable submission is written back as a dispatch failure.

## Consequences

Production can fail closed against unsigned or unpinned Agent identity while
development retains an explicit compatibility mode. Operators can rotate keys
without stopping dispatch by adding the new pin, rotating the Agent, and then
removing the old pin. Configuration changes currently require Gateway restart;
there is no mutable trust database or credential copy in Mission state.

Existing unsigned deployments must opt in deliberately. This is a breaking
security default, documented in deployment configuration and observable through
startup logs, metrics, and the redacted trust-status endpoint.

## Alternatives considered

- Trusting the public key embedded in each signed card was rejected because it
  verifies self-consistency, not Agent identity.
- A single key per origin was rejected because it prevents overlap during safe
  key rotation.
- Storing trust pins in the Agent registry was deferred because the current
  registry lacks a governed, tenant-scoped trust-admin command and audit model.
- Silently falling back to unsigned compatibility on configuration errors was
  rejected because it turns operator mistakes into security downgrades.

## Verification

- Unit tests cover fail-closed defaults, strict environment parsing, multiple
  rotation keys, pin mismatch, and unsigned compatibility.
- Handler tests cover Mission failure write-back before remote task dispatch,
  strict registry rejection, and trust-status key redaction.
- Gateway Go tests and vet remain required before release.

## Supersedes

The unsigned compatibility default described by ADR-0037. Agent Card probing,
capability, origin, signature, and response checks from ADR-0037 remain in
force.
