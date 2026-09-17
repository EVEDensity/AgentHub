"""P3-3a: durable desktop secret key provisioning."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from app.services.desktop_secret import (
    SECRET_KEY_ENV,
    ensure_secret_key,
    secret_key_file,
)


class DesktopSecretKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.local_data = Path(self._tmp.name)
        self.env: dict[str, str] = {}

    def _env(self) -> dict[str, str]:
        return {**self.env, "AGENTHUB_LOCAL_DATA": str(self.local_data)}

    def test_generates_and_persists_a_strong_key(self) -> None:
        key = ensure_secret_key(self._env())

        self.assertTrue(key)
        self.assertGreaterEqual(len(key), 32)
        secret_file = self.local_data / ".secret_key"
        self.assertTrue(secret_file.exists())
        self.assertEqual(secret_file.read_text(encoding="utf-8"), key)

    def test_reuses_the_persisted_key_on_the_next_call(self) -> None:
        first = ensure_secret_key(self._env())
        second = ensure_secret_key(self._env())

        self.assertEqual(first, second)
        secret_file = self.local_data / ".secret_key"
        self.assertEqual(secret_file.read_text(encoding="utf-8"), first)

    def test_does_not_overwrite_an_existing_secret_file(self) -> None:
        secret_file = self.local_data / ".secret_key"
        secret_file.write_text("pre-existing-key", encoding="utf-8")

        key = ensure_secret_key(self._env())

        self.assertEqual(key, "pre-existing-key")
        self.assertEqual(
            secret_file.read_text(encoding="utf-8"), "pre-existing-key"
        )

    def test_env_key_wins_and_never_touches_the_file(self) -> None:
        key = ensure_secret_key(
            {**self._env(), SECRET_KEY_ENV: "  env-provided-key  "}
        )

        self.assertEqual(key, "env-provided-key")
        self.assertFalse((self.local_data / ".secret_key").exists())

    def test_production_path_setdefaults_the_process_environment(self) -> None:
        saved_key = os.environ.pop(SECRET_KEY_ENV, None)
        saved_local = os.environ.get("AGENTHUB_LOCAL_DATA")
        os.environ["AGENTHUB_LOCAL_DATA"] = str(self.local_data)
        try:
            key = ensure_secret_key()
            self.assertEqual(os.environ.get(SECRET_KEY_ENV), key)
            self.assertEqual(len(key), 64)  # token_urlsafe(48) -> 64 chars
        finally:
            if saved_key is None:
                os.environ.pop(SECRET_KEY_ENV, None)
            else:
                os.environ[SECRET_KEY_ENV] = saved_key
            if saved_local is None:
                os.environ.pop("AGENTHUB_LOCAL_DATA", None)
            else:
                os.environ["AGENTHUB_LOCAL_DATA"] = saved_local

    def test_secret_file_resolves_under_local_data(self) -> None:
        self.assertEqual(
            secret_key_file(self._env()),
            self.local_data / ".secret_key",
        )


if __name__ == "__main__":
    unittest.main()
