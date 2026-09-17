# ADR-0039: Sign The AgentHub Agent Card With A Controlled Identity Key

> Status: accepted  
> Owner: protocol, security, and deployment maintainers  
> Date: 2026-08-15  
> Scope: Gateway startup, AgentHub Agent Card publication, secret mounting, and
> future KMS/HSM integration

## Context

ADR-0038 makes external Agent Card verification fail closed, but AgentHub's own
published card was unsigned. Two strictly configured AgentHub installations
therefore could not establish pinned mutual interoperability without weakening
one side's unsigned policy.

The signing private key is an installation identity secret. It must not be
stored in Mission state, the Agent registry, Compose source, logs, status
responses, or a general API that returns plaintext secret material.

## Decision

Gateway supports a startup-loaded `A2ACardSigner` and signs its self Card once
before registering or serving it.

- `A2A_CARD_SIGNING_KEY_FILE` points to a read-only Secret file containing a
  hex-encoded 32-byte Ed25519 seed or consistent 64-byte Ed25519 private key.
- Key files are read with a 4 KiB limit. Invalid hex, length, or an inconsistent
  private-key public suffix prevents Gateway startup. Temporary byte buffers
  are cleared after key construction.
- The Card is copied, populated with the derived public key and `ed25519`
  algorithm, serialized with an empty signature field, signed, and only then
  published. Signing errors prevent A2A handler initialization.
- `A2A_REQUIRE_SIGNED_SELF_CARD=true` makes a missing key file a startup error.
  Without that release policy, Gateway can start unsigned but emits an explicit
  warning for local compatibility.
- `/platform/a2a/trust-status` exposes only `self_card_signed`; it never exposes
  public or private signing material.

The signer is an interface rather than direct file access inside the handler.
A future KMS/HSM adapter can implement public-key retrieval and signing without
changing Agent Card construction. The current IAM secrets API is not used
because it reveals plaintext and does not expose a non-exportable signing
operation.

## Consequences

Strict peers can pin AgentHub's published public key and cryptographically
verify the Card. Installation key rotation uses the same overlap sequence as
external pins: add the future public key to peers, replace the mounted signing
key and restart Gateway, then remove the old pin.

File-backed keys require deployment-level Secret mounting, backup, access
control, and rotation. The repository and default Compose file pass only a file
path and never generate or embed key material. Signing currently occurs at
startup; runtime key reload is deferred.

## Alternatives considered

- Generating an ephemeral key at startup was rejected because identity would
  change after every restart and invalidate peer pins.
- Accepting a raw private-key environment variable was rejected because it is
  more likely to leak through process and deployment inspection.
- Calling the existing IAM secret reveal endpoint was rejected because the
  Gateway would still receive plaintext and the API is not a signing service.
- Requiring a key unconditionally was rejected for local compatibility; release
  deployments can enforce it with `A2A_REQUIRE_SIGNED_SELF_CARD=true`.

## Verification

- Tests cover absent configuration, strict signing requirement parsing, seed
  file loading, invalid key rejection, signature verification, private-seed
  redaction, strict peer pin validation, and redacted signed status.
- Gateway tests, vet, Compose parsing, and patch hygiene remain release gates.

## Supersedes

None. This completes the local identity side of ADR-0038's external trust
policy.
