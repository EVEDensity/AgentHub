# ADR-0112: Catalog-bound single-executor chat dispatch

> Status: implemented  
> Owner: Mission Control maintainers  
> Last reviewed: 2026-10-09  
> Scope: chat admission, WorkUnit binding, controlled desktop claims

## Context

Chat mention helpers read dictionary fields from a catalog that returns typed
`AgentBinding` values. Participant metadata did not bind actual execution:
inline derivation ignored the Mission ID and queried a fictitious workspace,
while background derivation substituted a fixed desktop Agent. Workspace
claims and the desktop model-context compiler admitted only manual Missions.
An accepted chat request therefore did not establish a usable dispatch path.

## Decision

Chat initially admits exactly one enabled workspace catalog Agent using the
existing `function-calling` adapter. Unresolved or ambiguous identifiers,
multiple executors, and other adapter types fail before Mission creation.
The adapter never maps an outbound Agent onto an inbound or desktop executor.

Mission Control owns `create_chat_work_unit`. It locks the exact started chat
Mission, checks workspace ownership, revalidates its admitted participant
snapshot against the current enabled catalog, and atomically persists one
PENDING `desktop.task` WorkUnit plus its creation event. Its deterministic
identity makes repeat admission idempotent. Existing plans do not rebind.
Catalog capabilities remain availability metadata; only Contract grants
authorize Harness tools.

The existing workspace claim query and service defense admit the exact
`chat` / `desktop.task` / `function-calling` root tuple. Workspace, Agent,
adapter, supported kind, dependencies, quota, attempt, and active lease checks
remain in force. Lease-fenced execution projection and the desktop compiler
admit this same source while preserving its actual `chat` source type.
Manual desktop derivation is restricted to manual Missions and cannot silently
create a fixed Agent fallback for chat.

## Consequences and verification

The built-in desktop Runner still claims only `local-desktop-agent`. A catalog
Agent with no matching running consumer remains visibly PENDING. This change
does not start another Runner, implement multi-agent orchestration, dispatch
outbound A2A, or create verification Evidence. Chat acceptance remains manual;
dispatch cannot attest semantic correctness.

`tests/api/test_chat_mission.py` exercises the production catalog dataclass,
API rejection before Mission creation, real SQLite persistence and claim SQL,
idempotent admission, exact Agent/kind/adapter/workspace filtering, event-write
rollback, same-owner lease restoration, foreign-owner rejection, and bounded
Runner compilation. Production model and remote-Agent execution remain outside
this dispatch slice.
