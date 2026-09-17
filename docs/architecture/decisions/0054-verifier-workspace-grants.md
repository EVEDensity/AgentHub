# ADR-0054: Revocable Verifier Workspace Grants

> Status: accepted  
> Owner: IAM, Mission Control, and verification maintainers  
> Date: 2026-08-16  
> Scope: independent verifier authorization and Evidence admission

## Context

Mission Control already restricts Evidence recording to verifier identities,
but its workspace check required a non-admin verifier subject to equal the
workspace ID. A separately identifiable verifier service therefore could not
verify ordinary user workspaces without administrator credentials. Reusing the
workspace owner identity would collapse the actor boundary that makes Evidence
independent.

## Decision

`platform_workspace_members` remains the only workspace authorization truth.
Register `mission:verify` as an explicit permission and do not include it in a
built-in role's default scope set. Before Artifact I/O or a Mission transaction,
the WorkUnit verification endpoint queries this permission for a non-admin
verifier whose subject differs from the Mission workspace.

A missing grant returns `403`. An unavailable ACL store returns `503` and never
falls back to workspace access or Evidence admission. Administrator access
remains break-glass, and the existing subject-equals-workspace behavior remains
temporarily compatible while verifier deployments migrate to explicit service
principals.

The grant authorizes only the existing verification command. The authenticated
subject must still equal `verifierId`, the WorkUnit must be `VERIFYING`, the
criterion and ArtifactRefs must match Mission truth, and Mission Control must
reread and verify every referenced Artifact before recording Evidence. The
grant does not provide Mission listing, ordinary workspace reads, Runner claim
authority, or the ability to bypass state transitions.

## Consequences

Independent verifier processes can use distinct, revocable identities without
administrator credentials or a second ACL. Removing `mission:verify` blocks the
next verification decision immediately. Mission Control stores neither tokens
nor grant snapshots in Mission state.

This decision establishes authorization only. It does not define verifier work
discovery, evaluation policies, automatic PASS decisions, or a verifier runtime.
Those remain separate fail-closed slices.

## Verification

Service tests cover exact workspace, principal, and `mission:verify` lookup,
missing grants, invalid identifiers, the existing ACL query, and unavailable
storage. API tests cover explicit cross-workspace verifier access, denial before
Artifact reads or state mutation, unavailable IAM, identity matching, and the
unchanged verifier-gated state transitions.
