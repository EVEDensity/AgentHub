# ADR-0058: Server-Owned Evidence Integrity Hashes

> Status: accepted  
> Owner: Mission Control and verification maintainers  
> Date: 2026-08-16  
> Scope: Evidence admission, canonical integrity material, and API compatibility

## Context

Evidence already persists and exposes an `integrityHash`, but the verification
request supplied its value. Mission Control validated only the SHA-256 string
shape and stored it unchanged. A caller could therefore attach any digest to
otherwise valid Evidence, so the field proved neither completeness nor
tampering.

ADR-0057 now provides canonical byte-derived Artifact observations and a
controlled PASS evaluation result. Mission Control can bind those facts to the
Evidence it creates without changing the existing database column or response
shape.

## Decision

Mission Control generates every new Evidence integrity hash. The canonical
domain is `agenthub.evidence-integrity.v1`, encoded as sorted compact UTF-8 JSON
and hashed with SHA-256. Its material binds:

- Evidence ID, Mission ID, Contract ID and version;
- WorkUnit ID and attempt, including legacy attempt zero;
- criterion, verifier identity/version/configuration digest, and verdict;
- sorted ArtifactRefs plus byte-derived Artifact ID, kind, digest, and size;
- the controlled evaluator identity, digest, and verdict for PASS;
- the exact summary and UTC-microsecond generation timestamp.

ArtifactRefs, registered Artifacts, and byte observations must form the same
unique ID/digest set. PASS additionally requires its controlled evaluation to
match the criterion, configuration, verdict, and canonical observations.
FAIL and INCONCLUSIVE exclude a PASS evaluation but still bind verified
Artifact observations.

The hash is computed inside the Evidence-admission transaction after current
Contract, WorkUnit, attempt, Artifact metadata, and PASS evaluation are
revalidated. The stored Evidence and its event payload receive only this
server-generated value.

For request compatibility, `integrityHash` becomes optional and deprecated.
Mission Control does not pass it into the service or compare it; supplied values
are ignored and never persisted. Existing clients may omit it immediately, and
a later request-contract version can remove it. The Evidence response schema
and database column remain unchanged.

`Sha256EvidenceIntegrityHasher.matches` recomputes the canonical envelope using
constant-time digest comparison. Historical caller-supplied hashes are not
promoted or backfilled; they are verifiable only if they happen to reproduce
the v1 envelope.

## Consequences

New Evidence has a deterministic tamper-evident checksum over its durable
identity, execution attempt, Artifact bytes, and evaluation provenance. Input
order and equivalent timezone offsets do not change the result; meaningful
field changes do.

This is an unkeyed integrity hash, not an authenticity signature. A principal
that can rewrite all database fields can also recompute it. Strong provenance
requires a verifier-held signing key or remote attestation in a later slice.
Content-aware semantic evaluation and isolated verifier supervision also remain
separate work.

## Alternatives considered

- Validate or preserve the caller's hash: rejected because the server-generated
  Evidence ID and timestamp are not known to the caller, and caller ownership
  is the original trust flaw.
- Hash only the public Evidence JSON: rejected because Contract version,
  WorkUnit attempt, Artifact kind/size, and controlled evaluation provenance
  would remain unbound.
- Add a new database column immediately: rejected because the existing digest
  column can store canonical v1 hashes, while validity is established by
  recomputation rather than an unverifiable migration flag.
- Sign Evidence in this slice: deferred until isolated verifier identity and
  key custody are defined.

## Verification

Integrity service tests cover stable canonical ordering and timezone handling,
field-change sensitivity, exact Artifact closure, PASS/evaluator consistency,
non-PASS behavior, invalid versions/attempts/timestamps, and recomputation.
Mission API tests prove `integrityHash` can be omitted and a supplied forged
value is replaced by a server-generated digest. Existing persistence and public
Evidence contract tests verify the unchanged storage and response shape.

## Supersedes

This decision replaces caller ownership of the existing Evidence
`integrityHash` and implements the integrity-envelope follow-up from
[ADR-0057](0057-controlled-byte-evaluation.md).
