"""Budget persistence bound to the current Mission Control transaction."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

from app.db.mission_budget_capability import require_atomic_budget_backend
from app.domain.mission_budget import (
    BudgetAccount, BudgetConflict, BudgetError, BudgetReceipt, BudgetReservation, ReservationState,
)


def _reject_constant(_value):
    raise BudgetError("Contract budget contains a nonfinite number")


def _value(row, model):
    if row is None:
        return None
    values = dict(row)
    for field in ("state", "disposition"):
        if field in values:
            values[field] = ReservationState(values[field])
    return model.model_validate(values)


class MissionBudgetRepository:
    def __init__(self, *, execute, fetch_one):
        require_atomic_budget_backend(getattr(execute, "__self__", None))
        require_atomic_budget_backend(getattr(fetch_one, "__self__", None))
        self._execute = execute
        self._fetch_one = fetch_one

    @classmethod
    def from_mission_transaction(cls, repository):
        # MissionRepository.transaction yields a repository built from exactly
        # one connection. No pool acquisition or nested independent transaction.
        return cls(execute=repository._execute, fetch_one=repository._fetch_one)

    async def contract_cap(self, contract_id: str, version: int):
        row = await self._fetch_one(
            "SELECT document::text AS document FROM mission_contracts WHERE id=$1 AND version=$2",
            contract_id, version,
        )
        if row is None:
            raise BudgetError("actual Mission Contract is unavailable")
        try:
            document = json.loads(row["document"], parse_float=Decimal, parse_constant=_reject_constant)
            return document["budgets"]["modelCost"]
        except (KeyError, TypeError, ValueError) as exc:
            raise BudgetError("actual Contract requires an exact modelCost budget") from exc

    async def account(self, mission_id: str, *, lock=False) -> BudgetAccount | None:
        query = "SELECT * FROM mission_budget_accounts WHERE mission_id=$1"
        return _value(await self._fetch_one(query + (" FOR UPDATE" if lock else ""), mission_id), BudgetAccount)

    async def reservation(self, identifier: str, *, lock=False) -> BudgetReservation | None:
        query = "SELECT * FROM mission_budget_reservations WHERE id=$1"
        return _value(await self._fetch_one(query + (" FOR UPDATE" if lock else ""), identifier), BudgetReservation)

    async def receipt(self, identifier: str) -> BudgetReceipt | None:
        return _value(await self._fetch_one(
            "SELECT * FROM mission_budget_receipts WHERE reservation_id=$1", identifier,
        ), BudgetReceipt)

    async def receipt_for_usage(self, provider: str, identifier: str) -> BudgetReceipt | None:
        return _value(await self._fetch_one(
            "SELECT * FROM mission_budget_receipts WHERE provider=$1 AND provider_receipt_id=$2", provider, identifier,
        ), BudgetReceipt)

    async def insert(self, value: BudgetAccount | BudgetReservation | BudgetReceipt) -> None:
        tables = {BudgetAccount: "mission_budget_accounts", BudgetReservation: "mission_budget_reservations",
                  BudgetReceipt: "mission_budget_receipts"}
        values = value.model_dump(mode="json")
        placeholders = ",".join(f"${index}" for index in range(1, len(values) + 1))
        try:
            await self._execute(f"INSERT INTO {tables[type(value)]}({','.join(values)}) VALUES({placeholders})", *values.values())
        except Exception as exc:
            unique = getattr(exc, "sqlstate", None) == "23505" or getattr(exc, "sqlite_errorcode", None) in {
                sqlite3.SQLITE_CONSTRAINT_UNIQUE, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
            }
            if isinstance(value, BudgetReceipt) and unique:
                raise BudgetConflict("a final receipt or provider usage identity already exists") from exc
            raise

    async def balances(self, account: BudgetAccount) -> None:
        await self._execute(
            "UPDATE mission_budget_accounts SET held_nano_usd=$2,spent_nano_usd=$3,blocked=$4 WHERE mission_id=$1",
            account.mission_id, account.held_nano_usd, account.spent_nano_usd, account.blocked,
        )

    async def save_state(self, reservation: BudgetReservation) -> None:
        await self._execute(
            """UPDATE mission_budget_reservations SET state=$2,dispatch_digest=$3,unknown_reason=$4,
               actual_nano_usd=$5,finalized_at=$6 WHERE id=$1""", reservation.id, reservation.state.value,
            reservation.dispatch_digest, reservation.unknown_reason, reservation.actual_nano_usd, reservation.finalized_at,
        )

    async def claim_dispatch(self, identifier: str, digest: str, dispatched_at: str) -> bool:
        row = await self._fetch_one(
            """UPDATE mission_budget_reservations SET state='DISPATCHED',dispatch_digest=$2,dispatched_at=$3
               WHERE id=$1 AND state='RESERVED' RETURNING id""", identifier, digest, dispatched_at,
        )
        return row is not None
