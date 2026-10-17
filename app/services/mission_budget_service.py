"""Internal, persistent finance infrastructure; no provider dispatch is wired here."""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

from app.domain.mission_budget import (
    BudgetAccount, BudgetConflict, BudgetError, BudgetExhausted, BudgetReceipt,
    BudgetReservation, BudgetScope, DispatchClaim, ReservationState, TrustedModelUsage,
    checked_amount, usd_to_nano,
)
from app.repositories import MissionRepository
from app.repositories.mission_budget_repository import MissionBudgetRepository
from app.services.model_pricing import ModelPrice, ModelPriceTable, canonical_json, payload_digest


def _require_identifier(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 255:
        raise BudgetError("operation identifier must be a bounded string")
    return value


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def _reservation_id(scope: BudgetScope, operation_id: str) -> str:
    identity = [scope.mission_id, scope.work_unit_id, scope.attempt, _require_identifier(operation_id)]
    return "budget-" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()


def _active_scope(mission, unit, scope: BudgetScope) -> None:
    if mission.status.value != "RUNNING" or unit.status.value != "RUNNING" or unit.lease is None:
        raise BudgetError("budget reserve/dispatch requires a RUNNING Mission and WorkUnit lease")
    actual = (unit.attempt, unit.lease.id, unit.lease.runner_id)
    if actual != (scope.attempt, scope.lease_id, scope.runner_id) or unit.lease.expires_at <= datetime.now(timezone.utc):
        raise BudgetError("budget operation has a stale attempt, runner or lease")


def _bound_scope(reservation, scope) -> None:
    if scope is not None and any(getattr(reservation, field) != getattr(scope, field) for field in BudgetScope.model_fields):
        raise BudgetConflict("reservation belongs to another execution scope")


def _balances(account, *, held, spent, blocked=None):
    return BudgetAccount.model_validate({**account.model_dump(), "held_nano_usd": checked_amount(held),
                                        "spent_nano_usd": checked_amount(spent),
                                        "blocked": account.blocked if blocked is None else int(blocked)})


class MissionBudgetService:
    """Called only by trusted in-process MC code, never a Runner settlement API."""
    def __init__(self, repository: MissionRepository, prices: ModelPriceTable):
        self._missions, self._prices = repository, prices

    async def initialize_account(self, mission_id: str, *, limit_usd: Decimal | str | int | None = None) -> BudgetAccount:
        async with self._missions.transaction() as repository:
            ledger = MissionBudgetRepository.from_mission_transaction(repository)
            mission = await repository.get_mission_for_update(mission_id)
            if mission is None:
                raise BudgetError("Mission does not exist")
            cap = usd_to_nano(await ledger.contract_cap(mission.contract_id, mission.contract_version))
            limit = cap if limit_usd is None else usd_to_nano(limit_usd)
            if limit > cap:
                raise BudgetError("server limit cannot exceed the actual Contract budget")
            expected = BudgetAccount(mission_id=mission.id, contract_id=mission.contract_id,
                                     contract_version=mission.contract_version, contract_limit_nano_usd=cap,
                                     limit_nano_usd=limit, held_nano_usd=0, spent_nano_usd=0,
                                     price_version=self._prices.version, pricing_digest=self._prices.digest)
            existing = await ledger.account(mission.id, lock=True)
            if existing is None:
                await ledger.insert(expected)
                return expected
            immutable = set(BudgetAccount.model_fields) - {"held_nano_usd", "spent_nano_usd", "blocked"}
            if any(getattr(existing, field) != getattr(expected, field) for field in immutable):
                raise BudgetConflict("Mission account is pinned to another Contract, limit or price table")
            return existing

    @asynccontextmanager
    async def _locked(self, *, scope: BudgetScope | None = None, reservation_id: str | None = None, active=False):
        async with self._missions.transaction() as repository:
            ledger = MissionBudgetRepository.from_mission_transaction(repository)
            # This first read does not take a lock. Every lock follows the same
            # Mission -> WorkUnit -> account -> reservation order below.
            seed = await ledger.reservation(reservation_id) if reservation_id is not None else None
            if reservation_id is not None and seed is None:
                raise BudgetError("reservation does not exist")
            if seed is not None:
                _bound_scope(seed, scope)
            identity = scope if scope is not None else seed
            mission = await repository.get_mission_for_update(identity.mission_id)
            unit = await repository.get_work_unit_for_update(identity.work_unit_id)
            if mission is None or unit is None or unit.mission_id != mission.id:
                raise BudgetError("budget operation has no actual Mission/WorkUnit scope")
            if active:
                _active_scope(mission, unit, scope)
            account = await ledger.account(mission.id, lock=True)
            if account is None:
                raise BudgetError("Mission budget account must be initialized explicitly")
            if active:
                self._require_current_account(account, mission)
            reservation = await ledger.reservation(reservation_id, lock=True) if reservation_id is not None else None
            if reservation is not None:
                _bound_scope(reservation, scope)
            yield ledger, account, reservation

    def _require_current_account(self, account, mission) -> None:
        if (account.contract_id, account.contract_version) != (mission.contract_id, mission.contract_version):
            raise BudgetConflict("account belongs to another actual Contract revision")
        if (account.price_version, account.pricing_digest) != (self._prices.version, self._prices.digest):
            raise BudgetConflict("server price table differs from the account's frozen version")

    async def reserve(self, scope: BudgetScope, operation_id: str, *, provider: str, model: str,
                      max_input_tokens: int, max_output_tokens: int) -> BudgetReservation:
        price = self._prices.price(provider, model)
        amount = price.cost(max_input_tokens, max_output_tokens)
        request = {"scope": scope.model_dump(), "operationId": _require_identifier(operation_id),
                   "provider": provider, "model": model, "priceVersion": self._prices.version,
                   "pricingDigest": self._prices.digest, "price": price.to_dict(),
                   "maxInputTokens": checked_amount(max_input_tokens), "maxOutputTokens": checked_amount(max_output_tokens)}
        identifier, digest = _reservation_id(scope, operation_id), payload_digest(request)
        async with self._locked(scope=scope, active=True) as (ledger, account, _):
            existing = await ledger.reservation(identifier, lock=True)
            if existing is not None:
                if existing.payload_digest != digest:
                    raise BudgetConflict("reservation operation was reused with different payload")
                return existing
            if account.blocked or amount > account.limit_nano_usd - account.spent_nano_usd - account.held_nano_usd:
                raise BudgetExhausted("Mission budget is exhausted or blocked by an over-reservation charge")
            reservation = BudgetReservation(id=identifier, **scope.model_dump(), operation_id=operation_id,
                                            payload_digest=digest, request_json=canonical_json(request),
                                            reserved_nano_usd=amount, created_at=_utc_timestamp(), state=ReservationState.RESERVED)
            await ledger.balances(_balances(account, held=account.held_nano_usd + amount, spent=account.spent_nano_usd))
            await ledger.insert(reservation)
            return reservation

    async def dispatch_once(self, scope: BudgetScope, reservation_id: str, *, request_digest: str) -> DispatchClaim:
        if not isinstance(request_digest, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", request_digest) is None:
            raise BudgetError("dispatch requires a canonical actual request digest")
        async with self._locked(scope=scope, reservation_id=reservation_id, active=True) as (ledger, account, reservation):
            if reservation.dispatch_digest is not None:
                if reservation.dispatch_digest != request_digest:
                    raise BudgetConflict("dispatch payload differs from the first dispatch claim")
                return DispatchClaim(reservation=reservation, claimed=False)
            if reservation.state != ReservationState.RESERVED:
                raise BudgetConflict("only a reserved operation can claim dispatch")
            if account.blocked:
                raise BudgetExhausted("Mission account is blocked by an over-reservation charge")
            dispatched_at = _utc_timestamp()
            claimed = await ledger.claim_dispatch(reservation.id, request_digest, dispatched_at)
            if not claimed:
                raise BudgetConflict("dispatch compare-and-set did not claim the reserved operation")
            return DispatchClaim(reservation=reservation.model_copy(update={"state": ReservationState.DISPATCHED,
                                                                        "dispatch_digest": request_digest,
                                                                        "dispatched_at": dispatched_at}), claimed=True)

    async def mark_unknown(self, reservation_id: str, *, reason: str) -> BudgetReservation:
        if reason not in {"timeout", "cancelled", "missing_usage", "unknown_transport"}:
            raise BudgetError("unsupported unknown execution reason")
        async with self._locked(reservation_id=reservation_id) as (ledger, _account, reservation):
            if reservation.state == ReservationState.UNKNOWN and reservation.unknown_reason == reason:
                return reservation
            if reservation.state != ReservationState.DISPATCHED:
                raise BudgetConflict("only a dispatched operation can become UNKNOWN")
            updated = reservation.model_copy(update={"state": ReservationState.UNKNOWN, "unknown_reason": reason})
            await ledger.save_state(updated)
            return updated

    async def release_unsubmitted(self, reservation_id: str) -> BudgetReservation:
        document = {"action": "release-unsubmitted", "reservationId": reservation_id}
        async with self._locked(reservation_id=reservation_id) as (ledger, account, reservation):
            return await self._finalize(ledger, account, reservation, document, amount=0, usage=None)

    async def settle_trusted(self, reservation_id: str, usage: TrustedModelUsage | None) -> BudgetReservation:
        if usage is None:
            return await self.mark_unknown(reservation_id, reason="missing_usage")
        if not isinstance(usage, TrustedModelUsage):
            raise BudgetError("settlement requires trusted typed usage, not Runner supplied cost")
        async with self._locked(reservation_id=reservation_id) as (ledger, account, reservation):
            request = json.loads(reservation.request_json)
            if (usage.provider, usage.model) != (request["provider"], request["model"]):
                raise BudgetConflict("trusted usage does not belong to the reserved provider/model")
            rates = request["price"]
            amount = ModelPrice(rates["inputNanoUsdPerMillion"], rates["outputNanoUsdPerMillion"]).cost(
                usage.input_tokens, usage.output_tokens,
            )
            document = {"action": "settle", "reservationId": reservation_id, "usage": usage.model_dump(),
                        "priceVersion": request["priceVersion"], "price": rates, "actualNanoUsd": amount}
            return await self._finalize(ledger, account, reservation, document, amount=amount, usage=usage)

    async def _finalize(self, ledger, account, reservation, document, *, amount, usage):
        document = {**document, "missionId": account.mission_id, "contractId": account.contract_id,
                    "contractVersion": account.contract_version, "workUnitId": reservation.work_unit_id,
                    "attempt": reservation.attempt}
        digest = payload_digest(document)
        existing = await ledger.receipt(reservation.id)
        if existing is not None:
            if existing.payload_digest != digest:
                raise BudgetConflict("final receipt is immutable and has a different payload")
            return reservation
        target = ReservationState.SETTLED if usage is not None else ReservationState.RELEASED
        allowed = {ReservationState.DISPATCHED, ReservationState.UNKNOWN} if usage is not None else {ReservationState.RESERVED}
        if reservation.state not in allowed:
            raise BudgetConflict("dispatch uncertainty cannot be released or settled before dispatch")
        if usage is not None and await ledger.receipt_for_usage(usage.provider, usage.provider_receipt_id) is not None:
            raise BudgetConflict("trusted provider usage was already settled for another reservation")
        finalized_at = _utc_timestamp()
        receipt = BudgetReceipt(reservation_id=reservation.id, mission_id=reservation.mission_id,
                                disposition=target, payload_digest=digest, actual_nano_usd=amount,
                                finalized_at=finalized_at,
                                document=canonical_json({**document, "createdAt": reservation.created_at,
                                                         "dispatchedAt": reservation.dispatched_at, "finalizedAt": finalized_at}),
                                provider=usage.provider if usage else None,
                                provider_receipt_id=usage.provider_receipt_id if usage else None)
        await ledger.insert(receipt)
        balances = _balances(account, held=account.held_nano_usd - reservation.reserved_nano_usd,
                             spent=account.spent_nano_usd + amount,
                             blocked=bool(account.blocked or amount > reservation.reserved_nano_usd))
        await ledger.balances(balances)
        updated = reservation.model_copy(update={"state": target, "actual_nano_usd": amount, "finalized_at": finalized_at})
        await ledger.save_state(updated)
        return updated
