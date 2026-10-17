"""Exact money and immutable internal Mission budget ledger values."""
from __future__ import annotations

from decimal import Decimal, DecimalException, localcontext
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_INT64 = 9_223_372_036_854_775_807
NANO_USD_PER_USD = 1_000_000_000
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=255)]
Amount = Annotated[int, Field(strict=True, ge=0, le=MAX_INT64)]
Digest = Annotated[str, Field(strict=True, pattern=r"^sha256:[a-f0-9]{64}$")]
Timestamp = Annotated[str, Field(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")]


class BudgetError(ValueError):
    """A ledger operation cannot be admitted."""


class BudgetConflict(BudgetError):
    """An immutable operation or account was reused with different input."""


class BudgetExhausted(BudgetError):
    """The Mission has insufficient unreserved funds."""


def checked_amount(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INT64:
        raise BudgetError("amount/count must be a non-negative signed 64-bit integer")
    return value


def usd_to_nano(value: Decimal | str | int) -> int:
    """No binary float, implicit rounding, nonfinite value or integer overflow."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise BudgetError("USD requires Decimal, decimal text or an integer")
    if isinstance(value, str) and len(value) > 128:
        raise BudgetError("USD decimal text exceeds its bound")
    try:
        decimal = Decimal(value)
        if not decimal.is_finite() or decimal < 0 or len(decimal.as_tuple().digits) > 128:
            raise BudgetError("USD must be finite, non-negative and bounded")
        if decimal == 0:
            return 0
        if decimal < Decimal('0.000000001'):
            raise BudgetError("positive USD below one nano cannot be represented")
        with localcontext() as context:
            context.prec = 160
            scaled = decimal * NANO_USD_PER_USD
            if scaled > MAX_INT64 or scaled != scaled.to_integral_value():
                raise BudgetError("USD exceeds int64 or has unsupported sub-nano precision")
            return checked_amount(int(scaled))
    except DecimalException as exc:
        raise BudgetError("invalid USD decimal") from exc


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("created_at", "dispatched_at", "finalized_at", check_fields=False)
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None:
            datetime.fromisoformat(value)
        return value


class ReservationState(str, Enum):
    RESERVED = "RESERVED"
    DISPATCHED = "DISPATCHED"
    UNKNOWN = "UNKNOWN"
    SETTLED = "SETTLED"
    RELEASED = "RELEASED"


class BudgetScope(_Value):
    mission_id: Identifier
    work_unit_id: Identifier
    attempt: Annotated[int, Field(strict=True, ge=1, le=MAX_INT64)]
    lease_id: Identifier
    runner_id: Identifier


class BudgetAccount(_Value):
    mission_id: Identifier
    contract_id: Identifier
    contract_version: Annotated[int, Field(strict=True, ge=1)]
    contract_limit_nano_usd: Amount
    limit_nano_usd: Amount
    held_nano_usd: Amount
    spent_nano_usd: Amount
    blocked: Annotated[int, Field(strict=True, ge=0, le=1)] = 0
    price_version: Identifier
    pricing_digest: Digest


class BudgetReservation(_Value):
    id: Identifier
    mission_id: Identifier
    work_unit_id: Identifier
    attempt: Annotated[int, Field(strict=True, ge=1)]
    operation_id: Identifier
    lease_id: Identifier
    runner_id: Identifier
    payload_digest: Digest
    request_json: str
    reserved_nano_usd: Amount
    created_at: Timestamp
    state: ReservationState
    dispatch_digest: Digest | None = None
    dispatched_at: Timestamp | None = None
    unknown_reason: Identifier | None = None
    actual_nano_usd: Amount | None = None
    finalized_at: Timestamp | None = None


class TrustedModelUsage(_Value):
    """Only an internal trusted provider-accounting integration may supply this."""
    provider: Identifier
    model: Identifier
    provider_receipt_id: Identifier
    input_tokens: Amount
    output_tokens: Amount


class BudgetReceipt(_Value):
    reservation_id: Identifier
    mission_id: Identifier
    disposition: Literal[ReservationState.SETTLED, ReservationState.RELEASED]
    payload_digest: Digest
    actual_nano_usd: Amount
    finalized_at: Timestamp
    document: str
    provider: Identifier | None = None
    provider_receipt_id: Identifier | None = None


class DispatchClaim(_Value):
    reservation: BudgetReservation
    claimed: bool
