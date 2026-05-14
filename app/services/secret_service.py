from __future__ import annotations

import base64
import hashlib
import hmac
import os

_SECRET = os.getenv("AGENTHUB_SECRET_KEY", "agenthub-local-dev-secret").encode("utf-8")
_PREFIX = "enc:"


def _keystream(length: int, salt: bytes) -> bytes:
    chunks: list[bytes] = []
    counter = 0
    while sum(len(chunk) for chunk in chunks) < length:
        counter_bytes = counter.to_bytes(4, "big")
        chunks.append(hmac.new(_SECRET, salt + counter_bytes, hashlib.sha256).digest())
        counter += 1
    return b"".join(chunks)[:length]


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    salt = os.urandom(16)
    raw = value.encode("utf-8")
    stream = _keystream(len(raw), salt)
    cipher = bytes(a ^ b for a, b in zip(raw, stream))
    return _PREFIX + base64.urlsafe_b64encode(salt + cipher).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value
    payload = base64.urlsafe_b64decode(value[len(_PREFIX):].encode("ascii"))
    salt, cipher = payload[:16], payload[16:]
    stream = _keystream(len(cipher), salt)
    return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")
