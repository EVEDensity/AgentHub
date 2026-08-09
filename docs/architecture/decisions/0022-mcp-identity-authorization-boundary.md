# ADR-0022: MCP Identity and Authorization Boundary

> Status: accepted
> Owner: protocol and security maintainers
> Date: 2026-08-09
> Scope: Stateless MCP HTTP authentication and authorization

## Context

ADR-0021 added validated Mission, WorkUnit, attempt, capability, and scope
headers to `POST /mcp/rpc`, but correctly did not treat them as identity or
authorization proof. Without a separate authenticated principal, any caller
able to reach the endpoint could declare an arbitrary capability context.
Implementing a second JWT or capability registry inside MCP would duplicate
platform IAM and the Contract resolver.

## Decision

The MCP Gateway reuses `shared/iam.AuthMiddleware` to verify a Bearer token and
place the tenant, actor, roles, and scopes in Go `context.Context`. Stateless
RPC requires the Authorization header and does not accept the shared
middleware's query-token compatibility path.

The transport exposes an injectable `StatelessAuthorizer` and remains
independent of IAM. The MCP-specific IAM adapter authorizes the intersection
of two independent inputs:

- verified identity must have a tenant and `tool:execute`;
- Contract capability scope may require an additional IAM `required_scope` or
  constrain `tenant_id`, but it can never add permissions to the identity.

Authorization runs after execution-header validation and before JSON-RPC
dispatch. Authentication failures return 401; authenticated denials return
403. Neither path invokes the tool handler.

HTTP mode fails closed when `JWT_SECRET` is absent. Unsigned local IAM dev
mode requires the explicit `MCP_ALLOW_INSECURE_DEV_AUTH=true` setting. STDIO
mode remains a parent-process trust boundary and does not use HTTP auth.

## Consequences

MCP requests now carry verified tenant and actor identity alongside the
Mission/WorkUnit correlation context without conflating the two. Tool handlers
can consume both from the same Go context, while Mission Control remains the
only owner of durable WorkUnit authorization and state. Capability headers
remain tamperable request declarations; a future Mission Control grant lookup
may further attest them without changing the transport port.

Deployments must provide the same `JWT_SECRET` as other ingress
services. The current shared IAM verifier uses the platform's existing HMAC
JWT contract; asymmetric service identity or workload identity is a separate
future decision.

## Alternatives considered

- Trust `X-AgentHub-Capability` as authorization: rejected because clients can
  forge headers.
- Implement MCP-specific API keys or JWT parsing: rejected because it creates
  a second identity source and policy lifecycle.
- Put IAM imports directly in transport: rejected because protocol transport
  should be testable and reusable independently from the platform IAM module.
- Allow empty JWT secrets by default: rejected because configuration mistakes
  would silently disable authentication.

## Verification

- HTTP middleware tests cover missing Authorization and verified tenant/actor
  propagation.
- MCP authorization tests cover missing identity, missing `tool:execute`,
  additional required scope, cross-tenant denial, and a valid intersection.
- Transport tests prove authorization runs before dispatch and preserves the
  validated execution context.
- An authenticated stateless RPC contract test observes identity and execution
  context together at the dispatcher boundary.

## Supersedes

None. This completes the IAM follow-up left outside ADR-0021.
