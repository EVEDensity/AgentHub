# ADR-0033: Credential-Free Registry Catalog Synchronization

> Status: accepted
> Owner: Mission Control maintainers
> Date: 2026-08-14
> Scope: legacy Registry projection into workspace Agent catalog

## Context

The legacy `agent_registry` remains the configuration UI source and contains
provider credentials, base URLs, raw config, model names, avatars, and other
fields that must not enter Mission Control. Catalog bindings need an explicit
migration path without treating user identity as workspace identity or
silently overwriting concurrent catalog changes.

## Decision

Synchronize one requested Agent at a time. The source query is scoped to the
authenticated actor and selects only Agent ID, adapter type, capability tags,
and status. An actor-owned row takes precedence over a global template with the
same Agent ID. No request can select another source owner.

The target workspace is authorized independently. Synchronization uses the
catalog writer from ADR-0032 and therefore requires `expectedVersion`.
`online` and `sleeping` Registry Agents become enabled bindings; `offline`
Agents become disabled. Missing Agents return 404. Empty/mock adapters,
malformed capabilities, and unknown status return 409. Storage failures return
503 without a fallback projection.

## Consequences

Existing Registry configuration can enter the execution catalog without
copying provider access material. Sync remains explicit rather than coupling
legacy Registry writes to Mission Control. A single-Agent operation avoids
partial batch success and gives each binding an independent conflict boundary.

## Verification

- Source tests inspect the executed SQL projection and prove sensitive columns
  are absent.
- Synchronizer tests cover user/global resolution, status mapping, invalid
  source records, version forwarding, and catalog conflicts.
- API tests cover source-owner derivation, target workspace authorization, and
  error mapping.
