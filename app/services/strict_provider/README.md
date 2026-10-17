# Experimental strict direct provider

This operator-only in-process model port connects one frozen execution scope to
the Mission budget ledger. It is not selected by default Chat, CLI, Desktop or
the standalone Runner service. No Runner settlement API is added.

`StrictProviderProfile` fixes an exact credential-free `/chat/completions` URL,
provider/model, response/request byte limits, timeout and completion token cap.
HTTPS is required except literal loopback HTTP for protocol tests. The operator
must explicitly attest the exact model's maximum **billed input** ceiling and
record its source and ISO review date. The entire ceiling is reserved for every
call; character estimates are never treated as token bounds. That attestation is
not automatic verification: no universal hard-budget or production-provider
claim follows from configuring it. A valid profile also requires provider-enforced
`max_completion_tokens` across visible and reasoning output, and compatible
operator-owned prices covering all billed tokens. Unsupported pricing dimensions
and models without those guarantees must not be admitted.

`BudgetedModelPort(scope, service, profile, api_key)` receives its actual lease,
Runner and attempt from trusted composition. `ModelRequest.metadata` supplies
only `modelOperationId`, never authorization or price. Requests are text/function
calls, non-streaming, `n=1`, with no provider fallback or transport retry. Native
Harness tool results are labelled external-data user transcript text: the port
does not invent missing historical assistant tool calls. Final context and exact
wire bytes are frozen before reservation. The dispatch digest covers the endpoint
digest, profile digest and exact body digest. Credentials remain private and are
absent from those digests, recovery manifests and ledger receipts.

The optional server-only `limit_usd` keyword may lower the stored Contract cap.
It must match an already initialized account's frozen operator limit. Omitting it
does not increase a pre-existing lower account limit: that mismatch fails closed.

Only an atomic claimed dispatch sends HTTP. Re-entry, including SETTLED and
UNKNOWN operations, cannot resend. The response must contain the exact configured
model, provider response ID and explicit nonnegative integer prompt/completion/
total usage with matching totals. Valid usage is settled **before** interpreting
content or tool calls, so unusable output still costs money. Missing or malformed
usage, HTTP failures, cancellation, timeout and oversized responses retain the
hold as UNKNOWN (or DISPATCHED if best-effort cleanup cannot commit). No failure
response body or raw HTTP exception is surfaced. Actual over-bound usage is
settled fully and fails the port; over-reservation money also blocks the account.

Known complete literal bearer values are rejected from provider receipt IDs and
model output, including decoded function arguments. This is a narrow additional
guard, not general secret detection for fragments or transformed credentials.

The operation deadline includes ledger operations and HTTP. Cancellation/error
cleanup has at most 250 ms of waiting and may leave a retained durable hold. It
cannot retract a sent request. There is no response replay cache: recovery after
an indeterminate dispatch must stop rather than issue a second paid request.
Harness composition must explicitly disable its own retry and unsupported
streaming/delegation paths. `ModelUsage.cost` is only a float telemetry projection;
the integer ledger receipt remains financial truth.

Tests use a real loopback HTTP peer and real SQLite transactions. They establish
protocol/accounting integration, not evidence from a charging supplier.
