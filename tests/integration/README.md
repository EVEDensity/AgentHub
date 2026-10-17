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
`start`, `text`, `expire`, `complete`, or `decision`. It targets the requested
admitted WorkUnit using real transactional `lease_work_unit` and start commands;
the normal production workspace claim selector is unchanged. `text` runs the production Harness with an explicitly fixed
test ModelPort, `complete` publishes real CAS output and stops at `VERIFYING`,
and `decision` supplies only a `report` so the default Contract's missing
`test-result` requirement creates real pending governance. The driver never
submits PASS Evidence. `expire` waits for an actual one-second lease instead of
changing a clock or persisted status. The private workspace's `fixture-confirm`
keyword rule exercises transactional pending confirmation and cancellation.

`/fixture/health` exposes the process ID and request counters; the authenticated
Mission snapshot exposes actual durable state for browser assertions. Multiple
sessions may retain PENDING Missions while the driver leases a particular one.
Before exercising `decision`, other eligible VERIFYING Missions must be handled
because production verification discovery still selects by its normal workspace
policy. Reconnection replays persisted WorkUnit
events with the same IDs; consumers must deduplicate those identities.
`test_mission_chat_http_fixture.py` verifies the HTTP/SQLite/Harness boundary,
real CAS, keyset cursors, failure/authorization and cleanup. It does not prove
real provider accuracy, tool execution, full conversation-history recovery or
native Desktop packaging.

`test_native_session_history.py` exercises the native Session list/detail and
source-bound chat Mission directory through real admission and SQLite HTTP
serialization, including workspace/legacy rejection, unknown/foreign/non-chat
cursors, pagination during new admission, Unicode objective excerpts and
state-neutral reads. `test_session_mission_history_order.py` executes the new
history repository's SQL directly on SQLite and opt-in PostgreSQL JSONB with
native TIMESTAMPTZ. It covers timezone offsets, naive UTC, exact microseconds,
equal-instant ID ties, every keyset boundary and source/workspace filtering.
These projections recover saved task goals and bounded observations, not a
lossless transcript. `tests/contracts/test_session_history.py` validates actual
HTTP producer payloads against the versioned directory/detail/Mission-page
schema and checks strict DTO/schema parity and limits.

`test_mission_sse_cursor_sqlite.py` protects Mission stream resume against
aggregate-local sequence confusion. Only a cursor belonging to the authorized
Mission's own Mission aggregate advances its sequence. A WorkUnit, foreign
Mission or unknown event ID safely replays from zero, preserving stable event
IDs for client deduplication instead of skipping unrelated sequence ranges.

PostgreSQL-backed tests require `AGENTHUB_TEST_POSTGRES_DSN` pointing to a
dedicated disposable database. Each test creates and drops its own unique
schema. Never point this variable at production or a shared developer database.

`artifact_log_http_fixture.py` is the separate UI-3 browser fixture, run with
`python -m tests.integration.artifact_log_http_fixture`. It owns
`127.0.0.1:38100` only for its test file, with workspace `workspace-log` and
Mission `mis-log-browser`; do not reuse another test's listener. Fixed Bearer
identities `artifact-log-human` and `artifact-log-foreign` are local to this
fixture. A real `TrustedLocalTestProducer` executes a frozen external pytest
suite using its production process backend. One deliberate failing test makes
pytest retain the actual ANSI/CJK, stdout/stderr, HTML and terminal-control
output. The returned log bytes are published unchanged to CAS and registered
under a real MissionService lease before completion to VERIFYING. The Mission
stays RUNNING without Evidence. Reading these bytes neither verifies the entire
signed test bundle nor establishes acceptance; this fixture registers only its
log, not a complete trusted-test verification profile.

`artifact-log-real` is this actual producer output. `artifact-log-empty`,
`-large`, `-denied`, `-tampered`, `-missing`, `-invalid` and `-bounded` are explicitly
artificial boundary cases, registered through the same lease/CAS path. The
last two storage fault cases intentionally corrupt or remove their own CAS
object; no production command or arbitrary filesystem path is exposed.
The bounded case is 2,002 logical lines without a trailing newline; line index
2,000 contains 2,500 CJK code points, and the last line is the unique
`fixture-log-bounded-tail-2001` marker. Raw bytes remain below 256 KiB. The
client's tail-2,000-line, 2,000-code-point row projection should omit two source
lines and truncate one row; the HTTP API still returns the original verified
bytes. This is an artificial display-boundary fixture, not producer output.
`/fixture/health` reports process ID, metadata/source labels and per-Artifact
byte-read counts. The real log digest varies with actual pytest timing and
owned temporary paths. `test_artifact_log_http_fixture.py` checks actual
process capture, HTTP integrity/authorization, no byte I/O before denial,
failure statuses, state-neutral reads and owned cleanup. Existing UI-1 seeds
and the production preview endpoint remain unchanged. This is a registered
log snapshot, not a live terminal, separated stdout/stderr or complete history.

Run the default integration layer from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration -q
```

Run the PostgreSQL layer after setting the dedicated DSN:

```powershell
$env:AGENTHUB_TEST_POSTGRES_DSN = "postgresql://user:pass@localhost/test_db"
.\.venv\Scripts\python.exe -m pytest tests\integration -q
```
