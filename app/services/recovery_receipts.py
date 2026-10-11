"""Bounded, verified local tool results for crash recovery.

Receipt metadata remains content-free. Result bodies are explicitly loaded
through this boundary and are excluded from the recovery DTO's representation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_RECEIPT_RESULT_BYTES = 1_048_576
MAX_RECEIPT_RESULT_DEPTH = 64


class RecoveryReceiptError(RuntimeError):
    """A tool outcome cannot be durably recorded or safely recovered."""


@dataclass(frozen=True, slots=True)
class RecoveredToolResult:
    result: Mapping[str, Any] = field(repr=False)
    result_digest: str
    post_workspace_revision: str | None = None


def _validate_json_containers(value: Mapping[str, Any]) -> None:
    pending: list[tuple[Any, int]] = [(dict(value), 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_RECEIPT_RESULT_DEPTH:
            raise RecoveryReceiptError("tool receipt result exceeds nesting limit")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise RecoveryReceiptError("tool receipt result has non-string keys")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend((child, depth + 1) for child in item)


def encode_receipt_result(result: Mapping[str, Any]) -> tuple[str, str]:
    """Encode a complete executor result without truncating recovery content."""
    if not isinstance(result, Mapping):
        raise RecoveryReceiptError("tool receipt result must be an object")
    _validate_json_containers(result)
    encoder = json.JSONEncoder(
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    chunks: list[str] = []
    size = 0
    try:
        for chunk in encoder.iterencode(dict(result)):
            size += len(chunk.encode("utf-8"))
            if size > MAX_RECEIPT_RESULT_BYTES:
                raise RecoveryReceiptError("tool receipt result exceeds size limit")
            chunks.append(chunk)
        body = "".join(chunks)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RecoveryReceiptError("tool receipt result is not valid JSON") from exc
    digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    return body, digest


def decode_receipt_result(
    body: Any,
    digest: Any,
    post_workspace_revision: Any,
) -> RecoveredToolResult:
    """Verify size, digest and canonical representation before exposing content."""
    if not isinstance(body, str) or not isinstance(digest, str):
        raise RecoveryReceiptError("tool receipt has no recoverable result")
    try:
        encoded = body.encode("utf-8")
    except UnicodeError as exc:
        raise RecoveryReceiptError("tool receipt result is corrupt") from exc
    if len(encoded) > MAX_RECEIPT_RESULT_BYTES:
        raise RecoveryReceiptError("tool receipt result exceeds size limit")
    expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
    try:
        digest_matches = hmac.compare_digest(expected, digest)
    except TypeError as exc:
        raise RecoveryReceiptError("tool receipt result digest is invalid") from exc
    if not digest_matches:
        raise RecoveryReceiptError("tool receipt result digest does not match")
    try:
        result = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise RecoveryReceiptError("tool receipt result is corrupt") from exc
    canonical, _ = encode_receipt_result(result)
    if canonical != body:
        raise RecoveryReceiptError("tool receipt result is not canonical JSON")
    if post_workspace_revision is not None and (
        not isinstance(post_workspace_revision, str) or not post_workspace_revision.strip()
    ):
        raise RecoveryReceiptError("tool receipt workspace revision is invalid")
    return RecoveredToolResult(result, expected, post_workspace_revision)


def receipt_result_status(result: Mapping[str, Any]) -> str:
    success = result.get("success", True)
    if type(success) is not bool:
        raise RecoveryReceiptError("tool receipt result has an invalid success flag")
    return "SUCCEEDED" if success else "FAILED"


__all__ = [
    "MAX_RECEIPT_RESULT_BYTES",
    "RecoveredToolResult",
    "RecoveryReceiptError",
    "decode_receipt_result",
    "encode_receipt_result",
]
