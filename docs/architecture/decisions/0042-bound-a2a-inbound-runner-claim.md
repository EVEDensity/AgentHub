# ADR-0042: Admit Bound A2A Inbound Roots to Runner Claim

> Status: accepted  
> Owner: Mission Control and Runner maintainers  
> Date: 2026-08-15  
> Scope: WorkUnit claim eligibility, locking, and lease event semantics

## Context

Inbound A2A admission creates a root `a2a.inbound` WorkUnit with a
workspace-catalog Agent and adapter snapshot. The existing atomic Runner claim
query admitted delegated children only, so the durable inbound unit could not
enter the normal leased execution lifecycle. Letting every bound root use that
query would bypass planning and make an arbitrary root kind executable.

## Decision

The existing `/work-unit-claims` command becomes a bound WorkUnit claim rather
than a delegated-only command. It may select exactly one candidate when:

- the candidate is a delegated child; or
- the candidate has no parent, its kind is `a2a.inbound`, and its locked Mission
  source is also `a2a.inbound`.

Both modes require an exact Agent ID and adapter match, `PENDING` or `RETRYING`
status, and completed same-Mission dependencies. Selection remains deterministic
and uses `FOR UPDATE SKIP LOCKED`. Mission Control creates the lease, increments
the attempt through the domain transition, and appends one
`work_unit.lifecycle.leased` event. The event records `claimMode=delegated` or
`claimMode=a2a.inbound`.

The repository receives an explicit `allow_inbound_root` decision derived from
the locked Mission. The service independently rechecks binding and root
eligibility so alternate repository implementations fail closed. No other root
kind is claimable through this command.

Claim does not compile the peer objective into code and does not imply execution
or success. Runner still requires a trusted `ClaimedWorkResolver`, fenced start
and heartbeat, Artifact publication, and independent Evidence.

## Consequences

Inbound A2A work now reaches the same durable lease lifecycle as delegated work
without creating an A2A-owned scheduler. Existing delegated claim behavior and
the HTTP request/response contract remain compatible. Runner instances must be
configured for the WorkUnit's exact Agent and adapter binding.

Trusted inbound context resolution is now the next blocking execution gate. A
Runner without that resolver fails the claimed attempt honestly rather than
inventing input or success.

## Alternatives considered

- A separate inbound queue or scheduler was rejected because Mission Control is
  the lifecycle authority and already owns atomic claim and lease semantics.
- Allowing every bound root was rejected because binding alone does not grant a
  root kind execution semantics.
- Treating the peer objective as executable code was rejected because protocol
  text is untrusted context, not a Runner program.

## Verification

Persistence tests assert the root whitelist, exact binding filters, stable
locking, and Mission-derived allow flag. API tests cover successful inbound root
claim, exclusion of other root kinds and non-inbound Missions, fail-closed
alternate repository behavior, lease event mode, and unchanged delegated claim.
Runner tests confirm bound root payloads use the existing response fencing and
trusted resolver path.
