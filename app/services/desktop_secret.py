"""Durable desktop secret-key provisioning (P3-3a).

Desktop/local profiles have no operator to set ``AGENTHUB_SECRET_KEY``; a
fresh install would silently run on the built-in default secret, which breaks
the "never ship a known key" rule for JWT signing and API-key decryption.
``ensure_secret_key()`` provisions a strong random key once, persists it under
``AGENTHUB_LOCAL_DATA`` and injects it into the process environment before any
secret-dependent service initializes.

Priority order (fail-closed to the existing behavior when nothing works):

1. ``AGENTHUB_SECRET_KEY`` from the environment (always wins, never overridden);
2. ``<AGENTHUB_LOCAL_DATA>/.secret_key`` from a previous run;
3. ``secrets.token_urlsafe(48)`` written to that file (0600 semantics,
   best-effort on Windows — a failed chmod only logs a warning).
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger("agenthub.startup")

SECRET_KEY_ENV = "AGENTHUB_SECRET_KEY"
LOCAL_DATA_ENV = "AGENTHUB_LOCAL_DATA"
_SECRET_FILE_NAME = ".secret_key"


def secret_key_file(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the durable secret-key file path under the local data root."""
    environment = os.environ if env is None else env
    local_data = environment.get(LOCAL_DATA_ENV, "").strip()
    if local_data:
        return Path(local_data) / _SECRET_FILE_NAME
    from app.core.config import settings

    return settings.data_dir.parent / _SECRET_FILE_NAME


def _read_secret_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning(
            "desktop secret key file %s is unreadable (%s); generating a new one",
            path,
            exc,
        )
        return ""


def _write_secret_file(path: Path, key: str) -> bool:
    """Create the secret file with owner-only permissions; best-effort."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= getattr(os, "O_NOFOLLOW")
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, key.encode("utf-8"))
        finally:
            os.close(descriptor)
    except FileExistsError:
        # Another process provisioned the key concurrently — reuse it.
        return True
    except OSError as exc:
        logger.warning(
            "desktop secret key could not be persisted to %s: %s", path, exc
        )
        return False
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:  # noqa: PERF203 - Windows chmod is best-effort
        logger.warning(
            "desktop secret key file %s could not be restricted: %s", path, exc
        )
    return True


def ensure_secret_key(env: Mapping[str, str] | None = None) -> str:
    """Guarantee a non-default ``AGENTHUB_SECRET_KEY`` and return it.

    With ``env is None`` (production path) the resolved key is ``setdefault``
    into ``os.environ`` so every later ``os.getenv`` reader sees it; with an
    explicit mapping (tests) the mapping is read-only and the key is only
    returned.
    """
    environment = os.environ if env is None else env
    existing = environment.get(SECRET_KEY_ENV, "").strip()
    if existing:
        return existing

    path = secret_key_file(environment)
    key = _read_secret_file(path) if path.exists() else ""
    generated = False
    if not key:
        key = secrets.token_urlsafe(48)
        generated = True
        _write_secret_file(path, key)
        # Concurrent provisioning: another process may have won the O_EXCL
        # create — the durable file is authoritative either way.
        key = (_read_secret_file(path) if path.exists() else "") or key
    if generated:
        logger.info(
            "desktop secret key generated and persisted to %s", path
        )
    else:
        logger.info("desktop secret key loaded from %s", path)

    if env is None:
        os.environ.setdefault(SECRET_KEY_ENV, key)
    return key


__all__ = [
    "SECRET_KEY_ENV",
    "ensure_secret_key",
    "secret_key_file",
]
