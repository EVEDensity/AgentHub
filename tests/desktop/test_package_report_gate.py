"""Execute the report consumer against classified metadata fixtures, not packages."""

from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "desktop/require-package-resource-reports.ps1"
COMMIT = "a" * 40
INVENTORY = {
    "schemaVersion": 1, "scope": "desktop-package-resources", "sourceCommit": COMMIT,
    "buildMode": "local-services",
    "files": [{"path": "metadata-fixture.dat", "sizeBytes": 123456, "sha256": "b" * 64}],
}
INVENTORY_BYTES = json.dumps(INVENTORY).encode("utf-8")
INVENTORY_DIGEST = hashlib.sha256(INVENTORY_BYTES).hexdigest()
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="PowerShell report consumer requires PowerShell")


def report(kind: str) -> dict:
    return {
        "schemaVersion": 1, "scope": "desktop-package-resources", "status": "passed",
        "sourceCommit": COMMIT, "buildMode": "local-services", "kind": kind,
        "packagedApplication": True, "nativeWebView2": False, "realServicesVerified": False,
        "artifact": {"name": "fixture", "sha256": "b" * 64, "sizeBytes": 123},
        "resourceReport": {
            "schemaVersion": 1, "status": "passed", "sourceCommit": COMMIT,
            "scope": "desktop-package-resources", "buildMode": "local-services",
            "evidence": "directory-resource-files-only", "packagedApplication": False,
            "nativeWebView2": False, "realServicesVerified": False,
            "filesVerified": 1, "bytesVerified": 123456, "expectedManifestSha256": INVENTORY_DIGEST,
        },
    }


def invoke(root: Path, reports: list[dict]) -> subprocess.CompletedProcess:
    (root / "expected-resources.json").write_bytes(INVENTORY_BYTES)
    for index, value in enumerate(reports):
        directory = root / f"package-resources-fixture-{index}"
        directory.mkdir()
        (directory / "report.json").write_text(json.dumps(value), encoding="utf-8")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
         str(SCRIPT), "-Directory", str(root), "-SourceCommit", COMMIT],
        capture_output=True, text=True, timeout=30, check=False,
    )


def test_accepts_exactly_two_current_classified_metadata_reports(tmp_path: Path) -> None:
    result = invoke(tmp_path, [report("Portable"), report("Msi")])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("count", [0, 1, 3])
def test_rejects_missing_or_stale_extra_report(tmp_path: Path, count: int) -> None:
    assert invoke(tmp_path, [report("Portable")] * count).returncode != 0


@pytest.mark.parametrize(("field", "value"), [
    ("kind", "Portable"), ("status", "running"), ("status", "failed"),
    ("scope", "browser-shell-preview"), ("sourceCommit", "c" * 40),
    ("buildMode", "shell-only"), ("packagedApplication", False),
    ("nativeWebView2", True), ("realServicesVerified", True),
    ("nativeWebView2", None),
    ("nativeWebView2", "False"), ("packagedApplication", "True"),
    ("artifact", {"sha256": "", "sizeBytes": 123}),
    ("artifact", {"sha256": "b" * 64, "sizeBytes": 0}),
    ("resourceReport", {"status": "failed", "sourceCommit": COMMIT}),
    ("resourceReport", {"status": "passed", "sourceCommit": "c" * 40}),
])
def test_rejects_incomplete_or_misclassified_metadata(tmp_path: Path, field: str, value: object) -> None:
    invalid = report("Msi")
    invalid[field] = value
    assert invoke(tmp_path, [report("Portable"), invalid]).returncode != 0


@pytest.mark.parametrize(("field", "value"), [
    ("schemaVersion", 2), ("scope", "fixture"), ("buildMode", "shell-only"),
    ("evidence", "browser-preview"), ("packagedApplication", True),
    ("nativeWebView2", True), ("realServicesVerified", None),
    ("nativeWebView2", "False"),
    ("filesVerified", 0), ("filesVerified", True), ("filesVerified", "1"),
    ("filesVerified", 0.5), ("bytesVerified", None), ("bytesVerified", 2**54),
    ("filesVerified", 2), ("bytesVerified", 123455),
    ("expectedManifestSha256", ""), ("expectedManifestSha256", "e" * 64),
])
def test_rejects_empty_or_different_inventory_evidence(tmp_path: Path, field: str, value: object) -> None:
    invalid = report("Msi")
    invalid["resourceReport"][field] = value
    assert invoke(tmp_path, [report("Portable"), invalid]).returncode != 0
