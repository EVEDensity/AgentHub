"""Operator-owned read-only prices; never use model/Runner supplied cost."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from app.domain.mission_budget import BudgetError, checked_amount, usd_to_nano


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def payload_digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class ModelPrice:
    input_nano_usd_per_million: int
    output_nano_usd_per_million: int

    def __post_init__(self):
        checked_amount(self.input_nano_usd_per_million)
        checked_amount(self.output_nano_usd_per_million)

    def cost(self, input_tokens: int, output_tokens: int) -> int:
        numerator = (checked_amount(input_tokens) * self.input_nano_usd_per_million
                     + checked_amount(output_tokens) * self.output_nano_usd_per_million)
        # Round fractional nano-USD upward once for the complete call.
        return checked_amount((numerator + 999_999) // 1_000_000)

    def to_dict(self) -> dict:
        return {"inputNanoUsdPerMillion": self.input_nano_usd_per_million,
                "outputNanoUsdPerMillion": self.output_nano_usd_per_million}


def _price_entry(key, rates) -> ModelPrice:
    if not isinstance(key, tuple) or len(key) != 2:
        raise BudgetError("server provider/model keys require a two-item tuple")
    if any(not isinstance(part, str) or not 1 <= len(part) <= 255 for part in key):
        raise BudgetError("invalid server provider/model identity")
    if not isinstance(rates, (tuple, list)) or len(rates) != 2:
        raise BudgetError("server input/output rates require a two-item tuple or list")
    return ModelPrice(*(usd_to_nano(rate) for rate in rates))


class ModelPriceTable:
    def __init__(self, version: str, prices: Mapping[tuple[str, str], tuple[Decimal | str | int, Decimal | str | int]]):
        if not isinstance(version, str) or not 1 <= len(version) <= 255:
            raise BudgetError("server pricing requires a bounded version")
        if not isinstance(prices, Mapping) or not prices or len(prices) > 1000:
            raise BudgetError("server pricing requires a bounded version and nonempty table")
        entries = {key: _price_entry(key, rates) for key, rates in prices.items()}
        self._version = version
        self._entries = MappingProxyType(entries)
        document = [{"provider": key[0], "model": key[1], **price.to_dict()}
                    for key, price in sorted(entries.items())]
        self._digest = payload_digest({"version": version, "prices": document})

    @property
    def version(self) -> str:
        return self._version

    @property
    def digest(self) -> str:
        return self._digest

    def price(self, provider: str, model: str) -> ModelPrice:
        try:
            return self._entries[(provider, model)]
        except KeyError as exc:
            raise BudgetError("provider/model is absent from server pricing") from exc
