# ADR-0043: Lease-Fenced Inbound Execution Context

> Status: accepted  
> Owner: Mission Control, Runner, and Harness maintainers  
> Date: 2026-08-15  
> Scope: inbound A2A context projection and Harness input trust boundary

## Context

ADR-0042 permits a catalog-bound `a2a.inbound` root to enter the normal Runner
lease lifecycle. The claim contains durable references but intentionally does
not contain executable code. Treating the peer objective as code would cross a
protocol trust boundary, while letting Runner assemble context from unrelated
reads could mix leases, attempts, or Contract versions.

## Decision

Mission Control exposes a read-only execution-context command for an inbound
A2A root. The command requires a RUNNING inbound Mission, a LEASED or RUNNING
root `a2a.inbound` WorkUnit, the exact unexpired lease ID and runner identity,
and the Mission's immutable Contract. It returns versioned public Mission,
Contract, and WorkUnit snapshots in one transaction. The projection adds no
business state, event, or alternate source of truth.

Runner's `A2AInboundClaimedWorkResolver` rechecks the Mission, Contract,
WorkUnit, lease, runner, status, attempt, and capability relationships. It
requires `a2a.receive` and ensures WorkUnit requirements are a subset of
Contract grants. It then compiles deterministic, size-bounded JSON with
`language=text` and a timeout capped by both the Contract and local Runner.

The compiled document marks the peer objective and source metadata as untrusted
intent. It contains only safe Contract fields, capability names, acceptance
criterion descriptions, expected outputs, and Artifact IDs/digests. It excludes
capability scope, criterion configuration, repository scope, credentials,
arbitrary provider config, and Artifact bytes. Capability metadata is
descriptive: an independent capability resolver supplies actual Harness tools.
Any malformed, oversized, or inconsistent context fails closed.

## Consequences

An inbound claim can now become bounded model input without turning A2A into an
execution authority or duplicating Mission truth. Deployments must explicitly
use a model-capable Harness for `text`; the default SandboxHarness must not
execute it as code. Resolver success is not WorkUnit success and does not bypass
Artifact publication, independent Evidence, or terminal Mission decisions.

The endpoint currently returns Artifact references only. Artifact byte
materialization, remote Artifact/Evidence exchange, replayable context
manifests, and third-party A2A conformance remain later gates.

## Alternatives considered

- Executing the peer objective directly was rejected because protocol text is
  untrusted data.
- Copying the full Contract into the model prompt was rejected because scopes
  and configuration can contain authority-bearing or sensitive values.
- Letting Runner read Mission objects independently was rejected because it
  cannot prove a single lease-fenced snapshot.
- Persisting a second Runner context record was rejected because Mission
  Control already owns the required durable truth.

## Verification

API tests cover valid read-only projection, lease owner and expiry fencing,
source/kind restrictions, and missing Contracts without events or mutations.
Runner tests cover deterministic safe-field compilation, timeout and size
limits, prompt-injection treatment, sensitive-field exclusion, HTTP transport,
and fail-closed identity, lease, attempt, Contract, and capability drift.
