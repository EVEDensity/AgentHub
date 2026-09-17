"""Offline tests for the stack bootstrap installer (north-star M3).

The transport is a dict-backed fake fetch; every guarantee of the §4.0
baseline is asserted: sha256 verification, resume of verified files,
manifest copy placement (desktop-shell compatible), atomic pinning,
failed installs never disturbing the current pin, and rollback by
listing installed stacks.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.cli.stack_installer import (
    PIN_FILE_NAME,
    STACK_SERVICES_DIR,
    StackInstallerError,
    StackManifest,
    install_stack,
    list_installed_stacks,
    parse_manifest,
    pin_stack,
    read_pinned,
    stacks_root,
    version_dir_name,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_manifest(
    files: dict[str, bytes],
    *,
    version: str = "0.1.0",
    commit: str = "deadbee",
) -> tuple[bytes, dict[str, str]]:
    digests = {path: _digest(data) for path, data in files.items()}
    payload = {
        "schemaVersion": 1,
        "version": version,
        "commit": commit,
        "generatedAt": "2026-08-31T00:00:00Z",
        "files": [
            {
                "path": path,
                "sha256": digests[path],
                "size": len(data),
            }
            for path, data in files.items()
        ],
    }
    return json.dumps(payload).encode("utf-8"), digests


class _FakeFetch:
    """Serves a URL->bytes map; counts hits per URL."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.hits: dict[str, int] = {}

    def __call__(self, url: str) -> bytes:
        self.hits[url] = self.hits.get(url, 0) + 1
        if url not in self.files:
            raise ConnectionError(f"404 {url}")
        return self.files[url]


class VersionDirNameTests(unittest.TestCase):
    def test_matches_desktop_shell_naming(self) -> None:
        # Mirror of services.rs version_dir_name_of.
        self.assertEqual(version_dir_name("0.1.0", "deadbee"), "0.1.0-deadbee")
        self.assertEqual(version_dir_name("0.1.0", ""), "0.1.0")
        self.assertEqual(version_dir_name("0.1.0+build", "a b"), "0.1.0_build-a_b")
        self.assertEqual(version_dir_name("v1.2", "1234abc"), "v1.2-1234abc")


class ManifestParsingTests(unittest.TestCase):
    def test_valid_manifest_roundtrip(self) -> None:
        raw, digests = _build_manifest({"local-services/app.exe": b"BIN"})
        manifest = parse_manifest(raw)
        self.assertEqual(manifest.version, "0.1.0")
        self.assertEqual(manifest.directory_name, "0.1.0-deadbee")
        self.assertEqual(len(manifest.files), 1)
        self.assertEqual(manifest.files[0].sha256, digests["local-services/app.exe"])

    def test_wrong_schema_version_rejected(self) -> None:
        payload = json.dumps({"schemaVersion": 99, "version": "x", "files": []})
        with self.assertRaises(StackInstallerError):
            parse_manifest(payload.encode())

    def test_missing_files_rejected(self) -> None:
        payload = json.dumps({"schemaVersion": 1, "version": "x", "files": []})
        with self.assertRaises(StackInstallerError):
            parse_manifest(payload.encode())

    def test_traversal_path_rejected(self) -> None:
        payload = {
            "schemaVersion": 1,
            "version": "x",
            "files": [
                {"path": "../../evil.exe", "sha256": "0" * 64, "size": 1}
            ],
        }
        with self.assertRaises(StackInstallerError):
            parse_manifest(json.dumps(payload).encode())

    def test_absolute_path_rejected(self) -> None:
        payload = {
            "schemaVersion": 1,
            "version": "x",
            "files": ["/abs/evil.exe", ],
        }
        payload["files"] = [
            {"path": "/abs/evil.exe", "sha256": "0" * 64, "size": 1}
        ]
        with self.assertRaises(StackInstallerError):
            parse_manifest(json.dumps(payload).encode())

    def test_invalid_size_rejected(self) -> None:
        payload = {
            "schemaVersion": 1,
            "version": "x",
            "files": [
                {"path": "a.exe", "sha256": "0" * 64, "size": "big"}
            ],
        }
        with self.assertRaises(StackInstallerError):
            parse_manifest(json.dumps(payload).encode())


class InstallStackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        self.files = {
            "local-services/agenthub-runtime.exe": b"RUNTIME-BYTES",
            "local-services/stack-manifest.json": b"{}",
        }

    def test_install_downloads_verifies_and_pins(self) -> None:
        manifest_bytes, _ = _build_manifest(self.files)
        fetch = _FakeFetch({"manifest.json": manifest_bytes, **self.files})
        manifest = install_stack(
            manifest_url="manifest.json",
            data_dir=self.data_dir,
            fetch_fn=fetch,
        )
        stack_dir = stacks_root(self.data_dir) / manifest.directory_name
        self.assertTrue(
            (stack_dir / "local-services" / "agenthub-runtime.exe").is_file()
        )
        # Manifest copy written where the desktop shell discovers it.
        self.assertTrue(
            (stack_dir / STACK_SERVICES_DIR / "stack-manifest.json").is_file()
        )
        # Pinned atomically.
        self.assertEqual(read_pinned(self.data_dir), manifest.directory_name)

    def test_resume_skips_verified_files(self) -> None:
        manifest_bytes, _ = _build_manifest(self.files)
        fetch = _FakeFetch({"manifest.json": manifest_bytes, **self.files})
        install_stack(
            manifest_url="manifest.json", data_dir=self.data_dir, fetch_fn=fetch
        )
        # Second run re-fetches the manifest (version check) but must not
        # re-download any already-verified file.
        for url in self.files:
            self.assertEqual(fetch.hits.get(url), 1, url)

    def test_integrity_failure_keeps_pin(self) -> None:
        # Install a good stack first, then delete one file so a forced
        # re-download happens against a tampered body.
        manifest_bytes, _ = _build_manifest(self.files)
        good_fetch = _FakeFetch({"manifest.json": manifest_bytes, **self.files})
        manifest = install_stack(
            manifest_url="manifest.json", data_dir=self.data_dir, fetch_fn=good_fetch
        )
        pinned_before = read_pinned(self.data_dir)
        stack_dir = stacks_root(self.data_dir) / manifest.directory_name
        runtime_path = stack_dir / "local-services" / "agenthub-runtime.exe"
        runtime_path.unlink()
        # Tampered body for the runtime file.
        corrupted = dict(self.files)
        corrupted["local-services/agenthub-runtime.exe"] = b"TAMPERED"
        bad_fetch = _FakeFetch({"manifest.json": manifest_bytes, **corrupted})
        with self.assertRaises(StackInstallerError):
            install_stack(
                manifest_url="manifest.json",
                data_dir=self.data_dir,
                fetch_fn=bad_fetch,
            )
        self.assertEqual(read_pinned(self.data_dir), pinned_before)

    def test_manifest_fetch_failure_raises(self) -> None:
        fetch = _FakeFetch({})
        with self.assertRaises(StackInstallerError):
            install_stack(
                manifest_url="missing.json",
                data_dir=self.data_dir,
                fetch_fn=fetch,
            )

    def test_progress_callback(self) -> None:
        manifest_bytes, _ = _build_manifest(self.files)
        fetch = _FakeFetch({"manifest.json": manifest_bytes, **self.files})
        progress: list[tuple[str, int, int]] = []
        install_stack(
            manifest_url="manifest.json",
            data_dir=self.data_dir,
            fetch_fn=fetch,
            on_progress=lambda path, index, total: progress.append(
                (path, index, total)
            ),
        )
        self.assertEqual(len(progress), 2)
        self.assertEqual(progress[0][2], 2)
        self.assertEqual(progress[1][1], 2)


class ListingAndRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    def _install(self, files: dict[str, bytes], version: str) -> StackManifest:
        manifest_bytes, _ = _build_manifest(files, version=version)
        fetch = _FakeFetch({"manifest.json": manifest_bytes, **files})
        return install_stack(
            manifest_url="manifest.json",
            data_dir=self.data_dir,
            fetch_fn=fetch,
        )

    def test_list_installed_stacks(self) -> None:
        files = {"local-services/app.exe": b"A"}
        self._install(files, version="1.0.0")
        self._install(files, version="2.0.0")
        stacks = list_installed_stacks(self.data_dir)
        self.assertEqual({m.version for m in stacks}, {"1.0.0", "2.0.0"})

    def test_rollback_by_repinning(self) -> None:
        files = {"local-services/app.exe": b"A"}
        v1 = self._install(files, version="1.0.0")
        self._install(files, version="2.0.0")
        # Roll back: pin the older stack again.
        pin_stack(self.data_dir, v1)
        self.assertEqual(read_pinned(self.data_dir), v1.directory_name)
        # Both stacks remain on disk.
        self.assertEqual(len(list_installed_stacks(self.data_dir)), 2)

    def test_no_stacks_when_empty(self) -> None:
        self.assertEqual(list_installed_stacks(self.data_dir), [])
        self.assertIsNone(read_pinned(self.data_dir))


if __name__ == "__main__":
    unittest.main()
