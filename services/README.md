# Services

`services/` contains independently runnable services grouped by runtime. A
service may own transport, compute, or infrastructure behavior, but it must
not create a competing Mission or WorkUnit state machine.

## Runtime groups

- `go/`: gateway, protocol adapters, realtime delivery, IAM, permissions,
  runtime control, audit, session, and shared event/database libraries.
- `python/`: model adapters, document and knowledge pipelines, summarization,
  and batch evaluation.
- `rust/`: isolated performance cores. Rust services communicate through stable
  contracts or events and do not own orchestration policy.

## Ownership rules

- Gateway authenticates and routes; it does not invent task completion.
- Mission Control owns durable execution state.
- Harness and Runner own execution attempts, isolation, and evidence capture.
- MCP and A2A services are protocol boundaries, not business databases.
- The MCP Gateway's `/mcp/rpc` endpoint is stateless and requires per-call
  Mission/WorkUnit/attempt/capability context; its SSE session endpoints remain
  compatibility transport only.
- NATS subjects and payloads require versioned contracts before production use.

Before adding a service, document its independent scaling, security, failure, or
runtime boundary in an ADR. Prefer a module in the control plane until data
proves a service boundary is needed.
