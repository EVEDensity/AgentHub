# Integration Tests

Integration tests protect boundaries that unit tests cannot establish alone.
Default tests may use in-process ASGI transport, but must exercise real HTTP
serialization, authentication dependencies, API routing, and service clients.
They must not claim to prove database locking when using an in-memory store.

The current minimum auth path uses a workspace-scoped Runner service identity,
so concurrent replicas in these tests share one token subject and are fenced by
distinct lease IDs. Per-Runner service-principal workspace grants are a separate
authorization milestone; tests must not substitute administrator credentials.

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
