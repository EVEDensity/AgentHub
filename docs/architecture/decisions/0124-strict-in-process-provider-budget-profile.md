# ADR-0124: Bind an explicit provider profile to the Mission budget ledger

> Status: accepted
> Owner: Mission Control and Runner maintainers
> Last reviewed: 2026-10-18
> Scope: experimental in-process direct model calls; default and remote Runners are excluded

## Context

[ADR-0123](0123-persistent-mission-budget-ledger.md) provides durable money,
reservations and settlement, but does not intercept model requests. Existing
adapter retries, shared usage fields and absent output bounds cannot establish
per-request accounting. A wrapper around the whole Harness would miss actual
HTTP requests and would allow concurrent calls to share the wrong usage.

## Decision

Introduce a separate, explicitly composed direct provider profile and model
port. It uses the existing canonical ModelRequest/ModelResponse and Function
Calling Harness. It does not modify the default Desktop/CLI/Chat factory or
introduce another Mission state machine. Mission Control remains the financial
owner. The trusted in-process dispatcher performs HTTP and reads provider usage;
Runner result usage is telemetry and is never accepted as a settlement command.

The operator freezes one provider, model, direct endpoint, pricing version,
maximum billable input window, output limit, timeout and byte limits. The input
window requires an operator-reviewed source for that exact model/profile; a
character heuristic is not a token upper bound. Reserve the complete declared
input window and output allowance, even for a short prompt. This conservative
profile depends on the provider honoring its documented limits and selected
pricing. A declaration alone does not verify an arbitrary endpoint or model.
Unsupported models, missing prices or missing credentials fail before HTTP.
An optional operator `limit_usd` is bound privately by the dispatcher/factory
and may only tighten the stored Contract ceiling. It must match an already
initialized account; model metadata cannot change that ceiling.

Use text and function calls only, one completion, no streaming, fallback,
automatic transport or Harness retry, planner, reflection or model-backed child
tools. The wire request fixes the selected model and output bound. For the
Chat Completions protocol, `max_completion_tokens` bounds reasoning and visible
output together, and `n` is one. See the
[official API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
and [token-counting guide](https://developers.openai.com/api/docs/guides/token-counting).
These sources establish the protocol, not availability, current prices or
conformance of an arbitrary compatible server.

The factory privately binds Mission, WorkUnit, attempt, lease and authenticated
Runner identity from the validated claim context. Model metadata only carries
a stable operation identifier for each iteration and final summary. It cannot
choose its authorization scope, endpoint or price. Construction may happen at
LEASED; reservation and dispatch recheck actual RUNNING state and live ownership
under the ledger transaction. Explicitly injected local tools require operator
admission and the corresponding Contract/WorkUnit capability. Handlers remain
trusted operator code; a name check cannot prove a handler has no model loop.

Prepare and bound the final wire bytes before reserving. Bind the exact request,
endpoint and profile with a digest. Reserve, claim dispatch once, then send a
single HTTP request outside the database transaction. A duplicate dispatched
operation never sends again, including after restart. Changed bytes/profile
conflict. Prices are the ledger's frozen operator prices; model-returned cost
does not determine a charge.

After dispatch, timeout, cancellation, broken transport, HTTP error, excessive
response or missing/malformed usage retain the hold. UNKNOWN is not zero cost.
If usage is valid but response content/function calls are malformed, settle the
actual usage before failing execution. Over-reservation charges remain visible
and block later ledger admission. A valid explicit zero usage can settle zero.
Receipt identity is taken from the trusted endpoint response, not the prompt.

HTTPS or loopback HTTP is supported. Environment proxies and redirects are off.
Credentials stay private to the dispatcher and are excluded from profile/request
digests, persisted receipts, model context and safe errors. This is an in-process
trust boundary, not hostile-code isolation. It does not grant an independent
Runner service database access or a public self-reported settlement API.

## Recovery and evidence

This first composition refuses resume and streaming. It does not add response
caching or a checkpoint schema. A crash after dispatch can leave DISPATCHED or
UNKNOWN funds held; it cannot justify resending with a fresh operation ID.
Even a settled ledger receipt is not a replayable model response. Late trusted
settlement changes financial rows only and cannot revive a cancelled WorkUnit.

Loopback HTTP integration tests establish real transport, dispatch ordering,
usage parsing, cancellation, duplicate suppression and the Runner/Artifact/
independent Verifier flow with a deterministic test provider. They are not live
vendor conformance or billing evidence. Default `artifact-set.v1` verification
establishes output byte integrity; it does not prove code correctness. Trusted
repository tests remain the separately admitted policy from ADR-0121.

No budget migration is added here. Native asyncpg and the serialized SQLite
writer retain ADR-0123's scope; Neon HTTP remains refused. Cross-process broker
authentication, complete response recovery, cross-Mission fork inheritance,
all-provider/default-entry accounting and supported isolated execution
composition remain later slices. This decision does not complete M1 or enable
the team deployment profile.
