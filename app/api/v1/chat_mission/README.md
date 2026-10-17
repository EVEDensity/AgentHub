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

Direct admission also groups Mission/Contract creation, start, WorkUnit dispatch
and the Mission-created receipt in one transaction. Catalog changes or storage
failures cannot leave an orphan RUNNING Mission behind a failed HTTP request.

Default admission uses `app/services/default_mission_contract.py`, the same
output-integrity policy as CLI and Desktop. Its `artifact-set.v1` criterion
allows the independent verifier to reach a terminal outcome after checking the
current attempt's registered `test-result` bytes. That historical output kind
also contains model text; it does not prove test execution or code correctness.
The policy verifies availability, size and SHA-256, and has no minimum byte size.
It does not accept an executor's self-issued PASS or add Contract tool grants.
Only newly admitted Missions use this default; existing immutable Contracts and
pending Decisions retain their original acceptance requirements.

This endpoint currently accepts text only. `attachments`, `references`, and
`execPermission` are unsupported fields and return HTTP 422 during request
validation, before a Mission, session or pending confirmation can be created.
Clients must show this limitation instead of dropping these inputs or implying
that a local permission selector changes the server's empty tool grants.

Rule confirmation/cancellation uses the pending repository's transactional
companions. A compare-and-set consumes PENDING once; Mission/Contract creation,
start, dispatch and confirmation receipts commit together or roll back together.
Expired requests persist EXPIRED and return 410. A failed storage write never
falls through to execution. SQLite serializes transactions across tasks; the
same task may nest without opening an independent transaction. See ADR-0113.

Default-policy closure is covered by
`tests/integration/test_default_task_acceptance_sqlite.py`: durable HTTP
admission, fenced execution, real CAS bytes, independent authenticated HTTP
verification, terminal success, tampered/missing bytes, and cancellation.
