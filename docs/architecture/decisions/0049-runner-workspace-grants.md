# ADR-0049: Revocable Runner Workspace Grants

> Status: accepted  
> Owner: IAM, Mission Control, and Runner maintainers  
> Date: 2026-08-15  
> Scope: workspace authorization and Runner execution

## Context

Workspace ready-work discovery initially required a non-admin token subject to
equal the workspace ID. Replicas could share that service identity because
lease IDs fence attempts, but independently identifiable Runner principals
could not claim from the same workspace without administrator credentials.
Mission Control must not solve this by creating a second ACL or copying IAM
credentials into Mission state.

## Decision

`platform_workspace_members` remains the only workspace authorization truth.
Register `mission:claim` as an explicit workspace permission and do not include
it in any built-in role's default scope set. Mission Control uses a read-only
authorization port to query that permission for the authenticated principal on
every workspace claim. A missing row or permission returns `403`; an unavailable
ACL store returns `503` and never falls back to allowing the claim.

Only authenticated Runner principals may use this grant. Administrator access
remains a break-glass path, and the legacy `subject == workspace_id` behavior is
temporarily retained for deployment compatibility. Neither path changes the
lease owner: it is always derived from the authenticated token.

The grant controls acquisition of new work. After a claim, execution-context,
start, heartbeat, Artifact registration, completion, failure, and retry commands
are authorized by the active lease ID and lease owner. These commands recheck
the fence transactionally in Mission Control. Revoking `mission:claim` therefore
blocks the next claim immediately without preventing an already leased attempt
from reporting its outcome.

## Consequences

Different Runner instances can use distinct service-principal identities in one
workspace without administrator privileges. Grant and revocation use the
existing workspace ACL administration path; Mission Control stores no token,
credential, grant copy, or new authorization table.

Runner grants do not permit Mission listing or ordinary workspace reads and
writes. A principal cannot operate another Runner's WorkUnit because all
post-claim commands require the exact active lease owner and lease ID. Per-claim
database authorization adds one read to the claim path in exchange for immediate
revocation and a single authorization source of truth.

## Verification

Service tests verify exact workspace, principal, and `mission:claim` lookup
arguments, missing grants, invalid identities, and fail-closed database errors.
API tests cover explicit grant, denial, unavailable IAM, non-Runner identities,
pre-transaction rejection, lease-owner isolation, and the legacy compatibility
path. ASGI integration runs two distinct Runner identities through the full
claim-to-VERIFYING flow. PostgreSQL integration grants both principals through
`platform_workspace_members`, forces concurrent `SKIP LOCKED` claims, and
verifies that permission removal causes the next claim to return `403`.
