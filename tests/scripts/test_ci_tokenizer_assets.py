"""Tokenizer caching must never admit corrupted data or unchecked downloads."""

from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci_prepare_tokenizers import ensure_asset


class TokenizerAssetIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "tokenizer"
        self.payload = b"versioned tokenizer asset"
        self.digest = hashlib.sha256(self.payload).hexdigest()

    def test_valid_cached_asset_never_accesses_network(self) -> None:
        self.path.write_bytes(self.payload)
        with patch("urllib.request.urlopen") as request:
            ensure_asset(self.path, ("https://example.test/tokenizer",), self.digest)
        request.assert_not_called()

    def test_corrupt_cached_asset_fails_instead_of_silently_using_it(self) -> None:
        self.path.write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "Cached tokenizer checksum mismatch"):
            ensure_asset(self.path, ("https://example.test/tokenizer",), self.digest)

    def test_checksum_mismatch_is_not_written_to_disk(self) -> None:
        with (
            patch("urllib.request.urlopen", return_value=io.BytesIO(b"corrupted")),
            self.assertRaisesRegex(RuntimeError, "checksum mismatch"),
        ):
            ensure_asset(self.path, ("https://example.test/tokenizer",), self.digest)
        self.assertFalse(self.path.exists())

    def test_verified_fallback_download_is_atomically_cached(self) -> None:
        with patch("urllib.request.urlopen", side_effect=[OSError("offline"), io.BytesIO(self.payload)]):
            ensure_asset(self.path, (
                "https://example.test/tokenizer", "https://mirror.example.test/tokenizer",
            ), self.digest)
        self.assertEqual(self.path.read_bytes(), self.payload)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
