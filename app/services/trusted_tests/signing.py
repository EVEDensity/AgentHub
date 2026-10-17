"""Operator-owned producer keys, scoped to exact trusted-local test profiles."""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.trusted_tests.models import (
    Digest,
    Identifier,
    ReceiptPayload,
    TrustedTestReceipt,
    canonical_bytes,
    parse_document,
)

TRUST_KEYS_FILE_ENV = "AGENTHUB_TRUSTED_TEST_KEYS_FILE"
SIGNATURE_DOMAIN = "agenthub.trusted-test-receipt.v1"


class TrustedTestTrustError(ValueError):
    """No operator-authorized key can attest to this receipt and profile."""


class TrustedProducerKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    producerId: Identifier
    keyId: Identifier
    publicKeyBase64: Annotated[str, Field(strict=True, min_length=44, max_length=44)]
    testProfileDigests: Annotated[tuple[Digest, ...], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def validate_key(self):
        public = base64.b64decode(self.publicKeyBase64, validate=True)
        if len(public) != 32 or len(set(self.testProfileDigests)) != len(self.testProfileDigests):
            raise ValueError("invalid public key or repeated trusted profile")
        return self


class TrustedProducerRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schemaVersion: Literal[1]
    keys: Annotated[tuple[TrustedProducerKey, ...], Field(max_length=256)] = ()

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value):
        if isinstance(value, dict) and "schemaVersion" in value and type(value["schemaVersion"]) is not int:
            raise ValueError("schemaVersion must be an integer")
        return value

    @model_validator(mode="after")
    def unique_keys(self):
        if len({(key.producerId, key.keyId) for key in self.keys}) != len(self.keys):
            raise ValueError("producer trust registry contains duplicate identities")
        return self

    def public_key(self, receipt: TrustedTestReceipt) -> Ed25519PublicKey:
        for key in self.keys:
            if ((key.producerId, key.keyId) == (receipt.producerId, receipt.keyId)
                    and receipt.testProfileDigest in key.testProfileDigests):
                return Ed25519PublicKey.from_public_bytes(base64.b64decode(key.publicKeyBase64, validate=True))
        raise TrustedTestTrustError("producer key is not operator-authorized for this test profile")


def load_trust_registry() -> TrustedProducerRegistry:
    """Only an operator environment path can supply trust, never report fields."""
    path = os.environ.get(TRUST_KEYS_FILE_ENV, "").strip()
    if not path:
        return TrustedProducerRegistry(schemaVersion=1)
    try:
        with Path(path).open("rb") as source:
            content = source.read(1024 * 1024 + 1)
        return parse_document(content, TrustedProducerRegistry)
    except (OSError, ValueError) as exc:
        raise TrustedTestTrustError("operator producer trust registry is unavailable or invalid") from exc


def signing_bytes(receipt: ReceiptPayload | Mapping[str, Any]) -> bytes:
    values = receipt.model_dump(mode="json") if isinstance(receipt, ReceiptPayload) else dict(receipt)
    values.pop("signature", None)
    payload = ReceiptPayload.model_validate(values)
    return canonical_bytes({"domain": SIGNATURE_DOMAIN, "receipt": payload.model_dump(mode="json")})


def sign_receipt(payload: ReceiptPayload | Mapping[str, Any], private_key: Ed25519PrivateKey) -> TrustedTestReceipt:
    values = payload.model_dump(mode="json") if isinstance(payload, ReceiptPayload) else dict(payload)
    values.pop("signature", None)
    values = {"schemaVersion": 1, "receiptType": "trusted-test-run", "executionMode": "trusted-local", **values}
    signature = base64.b64encode(private_key.sign(signing_bytes(values))).decode("ascii")
    return TrustedTestReceipt.model_validate({**values, "signature": signature})


def verify_receipt(receipt: TrustedTestReceipt, registry: TrustedProducerRegistry) -> None:
    try:
        signature = base64.b64decode(receipt.signature, validate=True)
        registry.public_key(receipt).verify(signature, signing_bytes(receipt))
    except (InvalidSignature, ValueError) as exc:
        raise TrustedTestTrustError("trusted test producer signature is invalid or unauthorized") from exc
