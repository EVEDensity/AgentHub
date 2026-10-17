"""Trusted in-process ledger/HTTP integration for one frozen execution scope."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

from app.domain.mission_budget import BudgetScope
from app.services.mission_budget_service import MissionBudgetService
from app.services.model_contract import ModelRequest, ModelResponse, ModelStreamEvent

from .deadline import ensure_active
from .errors import (
    StrictProviderError,
    StrictProviderReplayError,
    StrictProviderUsageError,
)
from .profile import StrictProviderProfile
from .transport import send_once
from .wire import PreparedRequest, parse_result, parse_usage, prepare

_CLEANUP_SECONDS = 0.25


def _consume(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


async def _bounded_cleanup(action) -> None:
    task = asyncio.create_task(action)
    task.add_done_callback(_consume)
    try:
        done, _ = await asyncio.wait({task}, timeout=_CLEANUP_SECONDS)
        if done:
            task.result()
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 - retained hold is safe on cleanup failure
        return
    finally:
        if not task.done():
            task.cancel()


class BudgetedModelPort:
    """Credentials, actual usage and lease authority stay outside model metadata.

    A dispatch claim is durable before HTTP. Lost acknowledgements/crashes retain
    the hold; an already claimed operation is never resent. No response cache or
    indeterminate-call recovery is supplied by this experimental port.
    """

    def __init__(
        self,
        scope: BudgetScope,
        service: MissionBudgetService,
        profile: StrictProviderProfile,
        api_key: str,
        *,
        limit_usd: Decimal | str | int | None = None,
    ):
        if not isinstance(scope, BudgetScope) or not isinstance(
            profile, StrictProviderProfile
        ):
            raise StrictProviderError(
                "strict provider requires trusted scope and profile"
            )
        if (
            not isinstance(api_key, str)
            or not 1 <= len(api_key) <= 8192
            or not api_key.isascii()
            or any(ord(c) <= 32 or ord(c) == 127 for c in api_key)
        ):
            raise StrictProviderError(
                "strict provider requires a private bearer credential"
            )
        self._scope, self._service, self._profile = scope, service, profile
        self._api_key = api_key
        self._limit_usd = limit_usd
        self._bounds_violated = False

    @property
    def recovery_manifest(self) -> dict:
        return {
            "kind": "strict-budget-provider.v1",
            "profileDigest": self._profile.digest,
            "provider": self._profile.provider,
            "model": self._profile.model,
            "replay": "unsupported",
        }

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self._bounds_violated:
            raise StrictProviderError("strict provider billing bounds were violated")
        prepared = prepare(request, self._profile)
        if self._api_key in prepared.operation_id:
            raise StrictProviderError(
                "strict provider operation contains a private credential"
            )
        reservation = None
        dispatch_attempted = False
        dispatched = False
        deadline = asyncio.get_running_loop().time() + prepared.timeout_seconds
        try:
            async with asyncio.timeout(prepared.timeout_seconds):
                await self._service.initialize_account(
                    self._scope.mission_id, limit_usd=self._limit_usd
                )
                reservation = await self._service.reserve(
                    self._scope,
                    prepared.operation_id,
                    provider=self._profile.provider,
                    model=self._profile.model,
                    max_input_tokens=self._profile.max_billing_input_tokens,
                    max_output_tokens=self._profile.max_completion_tokens,
                )
                dispatch_attempted = True
                claim = await self._service.dispatch_once(
                    self._scope, reservation.id, request_digest=prepared.digest
                )
                if not claim.claimed:
                    raise StrictProviderReplayError(
                        "strict provider operation was already dispatched; replay is unavailable"
                    )
                dispatched = True
                ensure_active(deadline)
                return await self._receive(prepared, reservation.id, deadline)
        except BaseException as error:
            if reservation is not None:
                await self._cleanup(
                    reservation.id, dispatch_attempted, dispatched, error
                )
            raise

    async def _receive(
        self, request: PreparedRequest, reservation_id: str, deadline: float
    ) -> ModelResponse:
        content = await send_once(
            self._profile, request, self._api_key, deadline=deadline
        )
        payload, usage = parse_usage(content, self._profile)
        if self._api_key in usage.provider_receipt_id:
            raise StrictProviderUsageError(
                "strict provider receipt contains a private credential"
            )
        settled = await self._service.settle_trusted(reservation_id, usage)
        if (
            usage.input_tokens > self._profile.max_billing_input_tokens
            or usage.output_tokens > self._profile.max_completion_tokens
        ):
            self._bounds_violated = True
            raise StrictProviderError("strict provider billing bounds were violated")
        if _contains_credential(payload, self._api_key):
            raise StrictProviderError(
                "strict provider response contains a private credential"
            )
        result = parse_result(payload, usage, settled.actual_nano_usd)
        decoded = {
            "content": result.content,
            "calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in result.tool_calls
            ],
        }
        if _contains_credential(decoded, self._api_key):
            raise StrictProviderError(
                "strict provider response contains a private credential"
            )
        ensure_active(deadline)
        return result

    async def _cleanup(
        self, identifier: str, attempted: bool, dispatched: bool, error: BaseException
    ) -> None:
        if dispatched or (
            attempted and isinstance(error, (asyncio.CancelledError, TimeoutError))
        ):
            reason = (
                "cancelled"
                if isinstance(error, asyncio.CancelledError)
                else "unknown_transport"
            )
            if isinstance(error, TimeoutError):
                reason = "timeout"
            if isinstance(error, StrictProviderUsageError):
                reason = "missing_usage"
            await _bounded_cleanup(
                self._service.mark_unknown(identifier, reason=reason)
            )
        elif not attempted:
            await _bounded_cleanup(self._service.release_unsubmitted(identifier))

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise StrictProviderError("strict provider streaming is unsupported")
        yield  # pragma: no cover - define the ModelPort async-iterator shape


def _contains_credential(value: object, credential: str) -> bool:
    if isinstance(value, str):
        return credential in value
    if isinstance(value, dict):
        return any(
            _contains_credential(key, credential)
            or _contains_credential(item, credential)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_credential(item, credential) for item in value)
    return False
