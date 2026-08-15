# ADR-0040: Support Non-Exportable Agent Card Signing

> Status: accepted  
> Owner: protocol, security, and deployment maintainers  
> Date: 2026-08-15  
> Scope: Gateway startup, remote signing contract, KMS/HSM boundary, and Agent
> Card signing identity metadata

## Context

ADR-0039 introduced a controlled file-backed Ed25519 signer for AgentHub's own
Agent Card. That enables strict peer trust but still places exportable private
key material in the Gateway process. Production installations may require a
non-exportable KMS/HSM key and auditable signing policy without binding the
Gateway to a single cloud provider SDK.

A remote signer must not become a general secret-reveal service, task service,
or source of Mission/WorkUnit state. It must also fail closed when transport,
identity, version, or signature integrity is uncertain.

## Decision

Gateway supports a second `A2ACardSigner` backend selected by
`A2A_CARD_SIGNER_URL`. It is mutually exclusive with
`A2A_CARD_SIGNING_KEY_FILE` and requires `A2A_CARD_SIGNER_KEY_ID` plus a bearer
token loaded from `A2A_CARD_SIGNER_TOKEN_FILE`.

- The endpoint accepts only `public_key` and `sign` operations with the fixed
  `a2a_agent_card_v1` purpose and configured key ID.
- HTTPS is required. Explicitly enabled insecure HTTP is limited to loopback
  development endpoints.
- Requests have a fixed five-second client timeout. Redirects are never
  followed, including when a custom transport is injected.
- Response bodies are limited to 32 KiB, must be `application/json`, contain
  exactly one object, and reject unknown fields.
- `public_key` returns Ed25519 algorithm, exact key ID, non-empty key version,
  and the public key. Gateway caches that public identity only for startup.
- `sign` includes the pinned key version and base64 Card payload. A changed
  algorithm, key ID, or version fails the operation.
- Gateway verifies the returned Ed25519 signature against the public key before
  mutating or publishing the Card.
- The Card may publish non-secret `key_id` and `key_version` metadata. Private
  keys and bearer tokens never enter the Card, logs, trust status, Registry,
  Mission, WorkUnit, Artifact, or Evidence.

The remote endpoint is an adapter contract. A deployment-specific sidecar or
service owns the cloud KMS/HSM API integration and must authorize the caller,
purpose, and key ID. It does not own any AgentHub business lifecycle state.

## Consequences

Production installations can keep the AgentHub identity key non-exportable
without adding cloud-specific dependencies to Gateway. The public key and key
version create an explicit, reviewable rotation identity while origin-bound
public-key pins remain the trust decision.

The signing endpoint and token become startup dependencies. An unavailable or
malformed signer prevents signed A2A initialization; Gateway never silently
downgrades to an unsigned Card. Runtime hot reload remains deferred. Rotation
uses a restart and the existing peer-pin overlap sequence.

## Alternatives considered

- Adding vendor KMS SDKs directly to Gateway was rejected because it couples
  protocol code to deployment providers and expands credential scope.
- Reusing the IAM plaintext secret API was rejected because it exports private
  material and has no purpose-bound signing operation.
- Allowing unauthenticated or redirecting signer endpoints was rejected because
  either would make the signing identity vulnerable to misuse or credential
  forwarding.
- Trusting the remote signature without local verification was rejected because
  signer routing or key-version mistakes could publish an unverifiable Card.

## Verification

Gateway tests cover backend mutual exclusion, HTTPS and loopback policy, token
file loading, purpose and key-version propagation, successful remote signing,
private material redaction, mismatched signatures, and redirect rejection.
Gateway tests, vet, Compose parsing, and patch hygiene remain release gates.

## Supersedes

None. This extends ADR-0039 without removing its file-backed deployment option.
