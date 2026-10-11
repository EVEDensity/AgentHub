# ADR-0113: Explicit session workspace ownership and bounded event cursors

> Status: accepted
> Owner: Mission Control maintainers
> Date: 2026-10-09

## Context

The v1 event adapters authorized a caller-supplied workspace without checking
the session's persisted workspace. A caller could read another session while
supplying their own authorized workspace. The legacy `sessions` table also
predated the v1 Session model: `CREATE TABLE IF NOT EXISTS` did not add its
workspace columns, so real application startup could not persist v1 sessions.

Event pagination searched only the first 500 records for `afterId`. Streaming
repeatedly read the first 200 events, leaving longer histories undelivered.

## Decision

The existing sessions repository remains the ownership authority. Each v1
session access first authorizes the requested workspace, then resolves the
session and requires its durable `workspace_id` to match. Unknown, unscoped,
and differently scoped sessions produce the same 404 response. Cursor events
must belong to that validated session; an arbitrary event ID is not authority.

An independent PostgreSQL revision `c8f2a314d5e6` and SQLite schema version 4
add the v1 columns to the existing table. Historical rows retain NULL workspace
scope. Neither a legacy owner, participant, event, nor caller supplies a
backfilled workspace. New v1 writes populate the compatibility name and human
owner projection while explicitly persisting workspace ownership. Session
timestamps retain the legacy ISO-text storage representation; domain reads
normalize them into aware datetimes. PostgreSQL startup also installs the
previously omitted session event and pending-confirmation tables.

SQLite adds columns, event tables, and its version marker within the startup
transaction. Missing tables and failed upgrades fail startup without advancing
the marker. PostgreSQL application rollback retains the additive fields and
event tables, including scoped conversation data; Alembic downgrade changes
the recorded revision without deleting this data. Reupgrade is idempotent.

Both event surfaces use a `(created_at, id)` keyset cursor under the session
filter. Event-type filtering does not require the cursor event to share the
requested type. Streaming drains full pages immediately and retains only the
last delivered cursor. The documented camelcase query parameters are bound
explicitly so web reconnects can pass `afterId`.

Confirmation and cancellation consume PENDING with a database compare-and-set
and expiry predicate. Confirmation consumption, Mission/Contract admission,
start, WorkUnit dispatch, and decision/session receipts share one connection and
transaction. An error restores PENDING and leaves no admitted Mission; concurrent
confirmation or cancellation admits only the winning result. Expiry is recorded
before returning 410. A failed confirmation-storage write cannot bypass consent.

The SQLite adapter gives transaction ownership to the actual asyncio task.
Only that task can nest scopes; other transactions and plain reads/writes wait
for commit or rollback. A losing request cannot poison another request's
transaction. Threaded SQLite operations finish before cancellation propagates;
implicit writes, including UPDATE RETURNING, commit normally or roll back on
failure/cancellation before releasing the connection.

## Consequences and verification

Legacy conversations remain available through legacy adapters, but cannot be
read or appended through the v1 workspace boundary until an explicit migration
assigns trustworthy scope. This decision does not define such a migration.
The ordering contract assumes events are appended with chronological creation
times; accepting late historical insertion would require a sequence contract.

Tests exercise real SQLite startup and repositories through FastAPI with two
ordinary principals, cross-workspace denial, cursor denial, equal timestamps,
601-event pagination, and SSE reconnect. Persistence tests verify preservation,
transaction rollback, repeated startup, and scoped roundtrips. Optional real
PostgreSQL tests exercise the corresponding Alembic upgrade and repository path.
Confirmation persistence and real API tests cover duplicate consumption,
confirmation/cancellation races, expiry, injected dispatch failure and retry.
