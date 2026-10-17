"""Additive Mission budget ledger; historical executions are never backfilled."""
from __future__ import annotations

from app.db.migrations.runner_presence import RUNNER_PRESENCE_REVISION
from app.db.mission_budget_capability import require_atomic_budget_backend

MISSION_BUDGET_REVISION = "e0b4c536f708"
MISSION_BUDGET_DOWN_REVISION = RUNNER_PRESENCE_REVISION

MISSION_BUDGET_UPGRADE = (
    """CREATE TABLE IF NOT EXISTS mission_budget_accounts(
        mission_id TEXT PRIMARY KEY NOT NULL REFERENCES missions(id) CHECK(length(mission_id) BETWEEN 1 AND 255),
        contract_id TEXT NOT NULL CHECK(length(contract_id) BETWEEN 1 AND 255),
        contract_version INTEGER NOT NULL CHECK(contract_version >= 1),
        contract_limit_nano_usd BIGINT NOT NULL CHECK(contract_limit_nano_usd >= 0),
        limit_nano_usd BIGINT NOT NULL CHECK(limit_nano_usd >= 0 AND limit_nano_usd <= contract_limit_nano_usd),
        held_nano_usd BIGINT NOT NULL DEFAULT 0 CHECK(held_nano_usd >= 0),
        spent_nano_usd BIGINT NOT NULL DEFAULT 0 CHECK(spent_nano_usd >= 0),
        blocked INTEGER NOT NULL DEFAULT 0 CHECK(blocked IN (0,1)),
        price_version TEXT NOT NULL CHECK(length(price_version) BETWEEN 1 AND 255),
        pricing_digest TEXT NOT NULL CHECK(length(pricing_digest) BETWEEN 1 AND 255),
        FOREIGN KEY(contract_id,contract_version) REFERENCES mission_contracts(id,version)
    )""",
    """CREATE TABLE IF NOT EXISTS mission_budget_reservations(
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) BETWEEN 1 AND 255),
        mission_id TEXT NOT NULL REFERENCES mission_budget_accounts(mission_id),
        work_unit_id TEXT NOT NULL REFERENCES work_units(id),
        attempt INTEGER NOT NULL CHECK(attempt >= 1),
        operation_id TEXT NOT NULL CHECK(length(operation_id) BETWEEN 1 AND 255),
        lease_id TEXT NOT NULL CHECK(length(lease_id) BETWEEN 1 AND 255),
        runner_id TEXT NOT NULL CHECK(length(runner_id) BETWEEN 1 AND 255),
        payload_digest TEXT NOT NULL CHECK(length(payload_digest) BETWEEN 1 AND 255),
        request_json TEXT NOT NULL,
        reserved_nano_usd BIGINT NOT NULL CHECK(reserved_nano_usd >= 0),
        state TEXT NOT NULL CHECK(state IN ('RESERVED','DISPATCHED','UNKNOWN','SETTLED','RELEASED')),
        dispatch_digest TEXT CHECK(length(dispatch_digest) BETWEEN 1 AND 255),
        unknown_reason TEXT,
        created_at TEXT NOT NULL CHECK(length(created_at) = 27
            AND substr(created_at,11,1) = 'T' AND substr(created_at,27,1) = 'Z'),
        dispatched_at TEXT CHECK(dispatched_at IS NULL OR (length(dispatched_at) = 27
            AND substr(dispatched_at,11,1) = 'T' AND substr(dispatched_at,27,1) = 'Z')),
        finalized_at TEXT CHECK(finalized_at IS NULL OR (length(finalized_at) = 27
            AND substr(finalized_at,11,1) = 'T' AND substr(finalized_at,27,1) = 'Z')),
        actual_nano_usd BIGINT CHECK(actual_nano_usd >= 0),
        UNIQUE(mission_id,work_unit_id,attempt,operation_id),
        CHECK((state IN ('RESERVED','RELEASED') AND dispatch_digest IS NULL)
           OR (state IN ('DISPATCHED','UNKNOWN','SETTLED') AND dispatch_digest IS NOT NULL)),
        CHECK((state = 'SETTLED' AND actual_nano_usd IS NOT NULL)
           OR (state = 'RELEASED' AND actual_nano_usd IS NOT NULL AND actual_nano_usd = 0)
           OR (state IN ('RESERVED','DISPATCHED','UNKNOWN') AND actual_nano_usd IS NULL)),
        CHECK(state <> 'UNKNOWN' OR unknown_reason IS NOT NULL),
        CHECK((state = 'RESERVED' AND dispatched_at IS NULL AND finalized_at IS NULL)
           OR (state IN ('DISPATCHED','UNKNOWN') AND dispatched_at IS NOT NULL AND finalized_at IS NULL)
           OR (state = 'SETTLED' AND dispatched_at IS NOT NULL AND finalized_at IS NOT NULL)
           OR (state = 'RELEASED' AND dispatched_at IS NULL AND finalized_at IS NOT NULL)),
        CHECK(dispatched_at IS NULL OR created_at <= dispatched_at),
        CHECK(finalized_at IS NULL OR created_at <= finalized_at),
        CHECK(dispatched_at IS NULL OR finalized_at IS NULL OR dispatched_at <= finalized_at)
    )""",
    """CREATE TABLE IF NOT EXISTS mission_budget_receipts(
        reservation_id TEXT PRIMARY KEY NOT NULL REFERENCES mission_budget_reservations(id),
        mission_id TEXT NOT NULL REFERENCES mission_budget_accounts(mission_id),
        disposition TEXT NOT NULL CHECK(disposition IN ('SETTLED','RELEASED')),
        payload_digest TEXT NOT NULL CHECK(length(payload_digest) BETWEEN 1 AND 255),
        actual_nano_usd BIGINT NOT NULL CHECK(actual_nano_usd >= 0),
        document TEXT NOT NULL,
        finalized_at TEXT NOT NULL CHECK(length(finalized_at) = 27
            AND substr(finalized_at,11,1) = 'T' AND substr(finalized_at,27,1) = 'Z'),
        provider TEXT CHECK(length(provider) BETWEEN 1 AND 255),
        provider_receipt_id TEXT CHECK(length(provider_receipt_id) BETWEEN 1 AND 255),
        UNIQUE(provider,provider_receipt_id),
        CHECK((disposition = 'SETTLED' AND provider IS NOT NULL AND provider_receipt_id IS NOT NULL)
           OR (disposition = 'RELEASED' AND provider IS NULL AND provider_receipt_id IS NULL AND actual_nano_usd = 0))
    )""",
    """CREATE INDEX IF NOT EXISTS idx_mission_budget_reservations_state
        ON mission_budget_reservations(mission_id,state)""",
    """CREATE OR REPLACE FUNCTION reject_mission_budget_receipt_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'mission budget receipts are insert-only';
        END;
        $$""",
    "DROP TRIGGER IF EXISTS mission_budget_receipts_immutable ON mission_budget_receipts",
    """CREATE TRIGGER mission_budget_receipts_immutable
        BEFORE UPDATE OR DELETE ON mission_budget_receipts
        FOR EACH ROW EXECUTE FUNCTION reject_mission_budget_receipt_mutation()""",
)

MISSION_BUDGET_DOWNGRADE = (
    "DROP TABLE IF EXISTS mission_budget_receipts",
    "DROP FUNCTION IF EXISTS reject_mission_budget_receipt_mutation()",
    "DROP TABLE IF EXISTS mission_budget_reservations",
    "DROP TABLE IF EXISTS mission_budget_accounts",
)

# Deliberately independent from the legacy PostgreSQL translator, which strips
# CHECK expressions. SQLite affinity alone accepts REAL money, so each monetary
# column additionally requires integer storage and the signed BIGINT range.
MISSION_BUDGET_SQLITE_UPGRADE = (
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_mission_budget_contract_revision
        ON mission_contracts(id,version)""",
    """CREATE TABLE IF NOT EXISTS mission_budget_accounts(
        mission_id TEXT PRIMARY KEY NOT NULL REFERENCES missions(id) CHECK(length(mission_id) BETWEEN 1 AND 255),
        contract_id TEXT NOT NULL CHECK(length(contract_id) BETWEEN 1 AND 255),
        contract_version INTEGER NOT NULL CHECK(typeof(contract_version) = 'integer' AND contract_version >= 1),
        contract_limit_nano_usd BIGINT NOT NULL CHECK(typeof(contract_limit_nano_usd) = 'integer'
            AND contract_limit_nano_usd BETWEEN 0 AND 9223372036854775807),
        limit_nano_usd BIGINT NOT NULL CHECK(typeof(limit_nano_usd) = 'integer'
            AND limit_nano_usd BETWEEN 0 AND 9223372036854775807 AND limit_nano_usd <= contract_limit_nano_usd),
        held_nano_usd BIGINT NOT NULL DEFAULT 0 CHECK(typeof(held_nano_usd) = 'integer'
            AND held_nano_usd BETWEEN 0 AND 9223372036854775807),
        spent_nano_usd BIGINT NOT NULL DEFAULT 0 CHECK(typeof(spent_nano_usd) = 'integer'
            AND spent_nano_usd BETWEEN 0 AND 9223372036854775807),
        blocked INTEGER NOT NULL DEFAULT 0 CHECK(blocked IN (0,1)),
        price_version TEXT NOT NULL CHECK(length(price_version) BETWEEN 1 AND 255),
        pricing_digest TEXT NOT NULL CHECK(length(pricing_digest) BETWEEN 1 AND 255),
        FOREIGN KEY(contract_id,contract_version) REFERENCES mission_contracts(id,version)
    )""",
    """CREATE TABLE IF NOT EXISTS mission_budget_reservations(
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) BETWEEN 1 AND 255),
        mission_id TEXT NOT NULL REFERENCES mission_budget_accounts(mission_id),
        work_unit_id TEXT NOT NULL REFERENCES work_units(id),
        attempt INTEGER NOT NULL CHECK(typeof(attempt) = 'integer' AND attempt >= 1),
        operation_id TEXT NOT NULL CHECK(length(operation_id) BETWEEN 1 AND 255),
        lease_id TEXT NOT NULL CHECK(length(lease_id) BETWEEN 1 AND 255),
        runner_id TEXT NOT NULL CHECK(length(runner_id) BETWEEN 1 AND 255),
        payload_digest TEXT NOT NULL CHECK(length(payload_digest) BETWEEN 1 AND 255),
        request_json TEXT NOT NULL,
        reserved_nano_usd BIGINT NOT NULL CHECK(typeof(reserved_nano_usd) = 'integer'
            AND reserved_nano_usd BETWEEN 0 AND 9223372036854775807),
        state TEXT NOT NULL CHECK(state IN ('RESERVED','DISPATCHED','UNKNOWN','SETTLED','RELEASED')),
        dispatch_digest TEXT CHECK(length(dispatch_digest) BETWEEN 1 AND 255),
        unknown_reason TEXT,
        created_at TEXT NOT NULL CHECK(length(created_at) = 27
            AND substr(created_at,11,1) = 'T' AND substr(created_at,27,1) = 'Z'),
        dispatched_at TEXT CHECK(dispatched_at IS NULL OR (length(dispatched_at) = 27
            AND substr(dispatched_at,11,1) = 'T' AND substr(dispatched_at,27,1) = 'Z')),
        finalized_at TEXT CHECK(finalized_at IS NULL OR (length(finalized_at) = 27
            AND substr(finalized_at,11,1) = 'T' AND substr(finalized_at,27,1) = 'Z')),
        actual_nano_usd BIGINT CHECK(actual_nano_usd IS NULL OR (typeof(actual_nano_usd) = 'integer'
            AND actual_nano_usd BETWEEN 0 AND 9223372036854775807)),
        UNIQUE(mission_id,work_unit_id,attempt,operation_id),
        CHECK((state IN ('RESERVED','RELEASED') AND dispatch_digest IS NULL)
           OR (state IN ('DISPATCHED','UNKNOWN','SETTLED') AND dispatch_digest IS NOT NULL)),
        CHECK((state = 'SETTLED' AND actual_nano_usd IS NOT NULL)
           OR (state = 'RELEASED' AND actual_nano_usd IS NOT NULL AND actual_nano_usd = 0)
           OR (state IN ('RESERVED','DISPATCHED','UNKNOWN') AND actual_nano_usd IS NULL)),
        CHECK(state <> 'UNKNOWN' OR unknown_reason IS NOT NULL),
        CHECK((state = 'RESERVED' AND dispatched_at IS NULL AND finalized_at IS NULL)
           OR (state IN ('DISPATCHED','UNKNOWN') AND dispatched_at IS NOT NULL AND finalized_at IS NULL)
           OR (state = 'SETTLED' AND dispatched_at IS NOT NULL AND finalized_at IS NOT NULL)
           OR (state = 'RELEASED' AND dispatched_at IS NULL AND finalized_at IS NOT NULL)),
        CHECK(dispatched_at IS NULL OR created_at <= dispatched_at),
        CHECK(finalized_at IS NULL OR created_at <= finalized_at),
        CHECK(dispatched_at IS NULL OR finalized_at IS NULL OR dispatched_at <= finalized_at)
    )""",
    """CREATE TABLE IF NOT EXISTS mission_budget_receipts(
        reservation_id TEXT PRIMARY KEY NOT NULL REFERENCES mission_budget_reservations(id),
        mission_id TEXT NOT NULL REFERENCES mission_budget_accounts(mission_id),
        disposition TEXT NOT NULL CHECK(disposition IN ('SETTLED','RELEASED')),
        payload_digest TEXT NOT NULL CHECK(length(payload_digest) BETWEEN 1 AND 255),
        actual_nano_usd BIGINT NOT NULL CHECK(typeof(actual_nano_usd) = 'integer'
            AND actual_nano_usd BETWEEN 0 AND 9223372036854775807),
        document TEXT NOT NULL,
        finalized_at TEXT NOT NULL CHECK(length(finalized_at) = 27
            AND substr(finalized_at,11,1) = 'T' AND substr(finalized_at,27,1) = 'Z'),
        provider TEXT CHECK(length(provider) BETWEEN 1 AND 255),
        provider_receipt_id TEXT CHECK(length(provider_receipt_id) BETWEEN 1 AND 255),
        UNIQUE(provider,provider_receipt_id),
        CHECK((disposition = 'SETTLED' AND provider IS NOT NULL AND provider_receipt_id IS NOT NULL)
           OR (disposition = 'RELEASED' AND provider IS NULL AND provider_receipt_id IS NULL AND actual_nano_usd = 0))
    )""",
    """CREATE INDEX IF NOT EXISTS idx_mission_budget_reservations_state
        ON mission_budget_reservations(mission_id,state)""",
    """CREATE TRIGGER IF NOT EXISTS mission_budget_receipts_no_replace
        BEFORE INSERT ON mission_budget_receipts
        WHEN EXISTS(SELECT 1 FROM mission_budget_receipts WHERE reservation_id = NEW.reservation_id)
          OR (NEW.provider IS NOT NULL AND EXISTS(
              SELECT 1 FROM mission_budget_receipts
              WHERE provider = NEW.provider AND provider_receipt_id = NEW.provider_receipt_id))
        BEGIN
            SELECT RAISE(ABORT, 'mission budget receipts are insert-only');
        END""",
    """CREATE TRIGGER IF NOT EXISTS mission_budget_receipts_no_update
        BEFORE UPDATE ON mission_budget_receipts BEGIN
            SELECT RAISE(ABORT, 'mission budget receipts are insert-only');
        END""",
    """CREATE TRIGGER IF NOT EXISTS mission_budget_receipts_no_delete
        BEFORE DELETE ON mission_budget_receipts BEGIN
            SELECT RAISE(ABORT, 'mission budget receipts are insert-only');
        END""",
)


async def upgrade_mission_budget_sqlite(connection) -> None:
    require_atomic_budget_backend(connection)
    async with connection.transaction():
        for statement in MISSION_BUDGET_SQLITE_UPGRADE:
            await connection.execute(statement)
