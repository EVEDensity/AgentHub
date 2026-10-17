# Integration Tests

Integration tests protect boundaries that unit tests cannot establish alone.
Default tests may use in-process ASGI transport, but must exercise real HTTP
serialization, authentication dependencies, API routing, and service clients.
They must not claim to prove database locking when using an in-memory store.

The current minimum auth path uses a workspace-scoped Runner service identity,
so concurrent replicas in these tests share one token subject and are fenced by
distinct lease IDs. Per-Runner service-principal workspace grants are a separate
authorization milestone; tests must not substitute administrator credentials.

`mission_chat_http_fixture.py` is the executable browser-test server for the
admin Mission dialog. Run `python -m tests.integration.mission_chat_http_fixture`
from the repository root. It binds only `127.0.0.1:38100`, owns a disposable
SQLite database and CAS, and creates no initial Missions. Its fixed human and
foreign test identities apply only to this fixture. Production chat admission,
catalog resolution, transactions, status, event streams, cancellation and
Decision resolution use the real routers and repositories.

The authenticated `/fixture/missions/{missionId}/advance` driver accepts
`start`, `text`, `expire`, `complete`, or `decision`. It invokes real claim and
state commands; `text` runs the production Harness with an explicitly fixed
test ModelPort, `complete` publishes real CAS output and stops at `VERIFYING`,
and `decision` supplies only a `report` so the default Contract's missing
`test-result` requirement creates real pending governance. The driver never
submits PASS Evidence. `expire` waits for an actual one-second lease instead of
changing a clock or persisted status. The private workspace's `fixture-confirm`
keyword rule exercises transactional pending confirmation and cancellation.

`/fixture/health` exposes the process ID and request counters; the authenticated
Mission snapshot exposes actual durable state for browser assertions. Each
browser case must cancel its remaining nonterminal Missions before another
case advances work: production workspace claim and verification discovery
select work by their normal policy. Reconnection replays persisted WorkUnit
events with the same IDs; consumers must deduplicate those identities.
`test_mission_chat_http_fixture.py` verifies the HTTP/SQLite/Harness boundary,
real CAS, keyset cursors, failure/authorization and cleanup. It does not prove
real provider accuracy, tool execution, full conversation-history recovery or
native Desktop packaging.

`test_mission_sse_cursor_sqlite.py` protects Mission stream resume against
aggregate-local sequence confusion. Only a cursor belonging to the authorized
Mission's own Mission aggregate advances its sequence. A WorkUnit, foreign
Mission or unknown event ID safely replays from zero, preserving stable event
IDs for client deduplication instead of skipping unrelated sequence ranges.

PostgreSQL-backed tests require `AGENTHUB_TEST_POSTGRES_DSN` pointing to a
dedicated disposable database. Each test creates and drops its own unique
schema. Never point this variable at production or a shared developer database.

Run the default integration layer from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration -q
```

Run the PostgreSQL layer after setting the dedicated DSN:

```powershell
$env:AGENTHUB_TEST_POSTGRES_DSN = "postgresql://user:pass@localhost/test_db"
.\.venv\Scripts\python.exe -m pytest tests\integration -q
```
