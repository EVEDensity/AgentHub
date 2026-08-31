"""Tests for the stack manifest generator (north-star M3).

The generator must produce manifests the Python installer and the Rust
bootstrap module both accept: schemaVersion 1, POSIX relative paths
under local-services/, sha256 + size per file, and exclusion of pin
files / previous manifests.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "make_stack_manifest.py"


class MakeStackManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.stack_dir = Path(self._tmp.name) / "stack"
        (self.stack_dir / "sub").mkdir(parents=True)
        (self.stack_dir / "app.exe").write_bytes(b"EXE-BYTES")
        (self.stack_dir / "sub" / "lib.dll").write_bytes(b"DLL-BYTES")
        # Excluded: previous manifest and pin markers never ship.
        (self.stack_dir / "stack-manifest.json").write_text("{}", encoding="utf-8")
        (self.stack_dir / ".pinned").write_text("old", encoding="utf-8")

    def _generate(self) -> dict:
        out = Path(self._tmp.name) / "dist" / "stack-manifest.json"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--stack-dir",
                str(self.stack_dir),
                "--version",
                "0.2.0",
                "--commit",
                "abc123",
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("manifest written", result.stdout)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_manifest_shape(self) -> None:
        manifest = self._generate()
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertEqual(manifest["commit"], "abc123")
        self.assertIn("generatedAt", manifest)
        paths = [entry["path"] for entry in manifest["files"]]
        self.assertIn("local-services/app.exe", paths)
        self.assertIn("local-services/sub/lib.dll", paths)

    def test_digests_match_files(self) -> None:
        manifest = self._generate()
        by_path = {entry["path"]: entry for entry in manifest["files"]}
        app = by_path["local-services/app.exe"]
        self.assertEqual(
            app["sha256"], hashlib.sha256(b"EXE-BYTES").hexdigest()
        )
        self.assertEqual(app["size"], len(b"EXE-BYTES"))

    def test_exclusions_never_ship(self) -> None:
        manifest = self._generate()
        paths = [entry["path"] for entry in manifest["files"]]
        self.assertNotIn("local-services/stack-manifest.json", paths)
        self.assertNotIn("local-services/.pinned", paths)

    def test_empty_stack_fails_honestly(self) -> None:
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--stack-dir",
                    str(empty),
                    "--version",
                    "x",
                    "--out",
                    str(Path(self._tmp.name) / "x.json"),
                ],
                capture_output=True,
                text=True,
                check=True,
            )

    def test_python_installer_accepts_generated_manifest(self) -> None:
        from app.cli.stack_installer import parse_manifest

        manifest = self._generate()
        out = Path(self._tmp.name) / "dist" / "stack-manifest.json"
        parsed = parse_manifest(out.read_bytes())
        self.assertEqual(parsed.version, "0.2.0")
        self.assertEqual(len(parsed.files), 2)


if __name__ == "__main__":
    unittest.main()
