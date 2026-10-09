# Chat-to-Mission adapter

The adapter parses chat intent, authorizes the session workspace, resolves
credential-free catalog `AgentBinding` values, and calls Mission Control.
It does not execute work or own a separate queue.

One `function-calling` catalog Agent is supported per Mission. Ambiguous or
unresolved mentions, multiple executors, and unsupported adapters are rejected
before Mission creation. With no executor mention, the adapter prefers the
enabled `local-desktop-agent` binding, then another supported catalog Agent.
`@archivist` remains a read-only context preprocessor.

The `dispatch` response describes the persisted WorkUnit, including its ID,
assigned Agent, adapter, and status. `PENDING` means it has not been claimed;
successful dispatch is not proof that a Runner exists or that execution passed.
The Mission source stores the actual session ID so later lifecycle events can
refer back to that session.

Mission Control creates only the exact requested Mission's WorkUnit. The manual
desktop derivation loop does not supply a fallback executor for chat Missions.
Rule target Agents use the same catalog admission and cannot create a separate
execution path. See ADR-0112 for the current dispatch boundary.

Rule confirmation/cancellation uses the pending repository's transactional
companions. A compare-and-set consumes PENDING once; Mission/Contract creation,
start, dispatch and confirmation receipts commit together or roll back together.
Expired requests persist EXPIRED and return 410. A failed storage write never
falls through to execution. SQLite serializes transactions across tasks; the
same task may nest without opening an independent transaction. See ADR-0113.
