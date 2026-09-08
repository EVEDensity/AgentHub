"""Verify GitHub Release install, upgrade, and rollback in an isolated directory."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.production_evidence import new_evidence, write_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--repository", default="EVEDensity/AgentHub")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--previous-archive", type=Path)
    parser.add_argument("--previous-checksums", type=Path)
    parser.add_argument("--target-archive", type=Path)
    parser.add_argument("--target-checksums", type=Path)
    args = parser.parse_args()
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if platform.system() != "Windows":
        return _emit(args.output, status="SKIP", errorType="unsupported_platform")
    if not powershell:
        return _emit(args.output, status="SKIP", errorType="powershell_unavailable")

    installer = ROOT / "release" / "install.ps1"
    steps: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="agenthub-release-") as temporary:
        temporary_root = Path(temporary)
        install_dir = temporary_root / "bin"
        cached: dict[str, tuple[Path, Path]] = {}
        supplied = {
            args.previous: (args.previous_archive, args.previous_checksums),
            args.target: (args.target_archive, args.target_checksums),
        }
        for version in (args.previous, args.target):
            archive, checksums = supplied[version]
            if bool(archive) != bool(checksums):
                return _emit(
                    args.output, status="FAIL", errorType="incomplete_local_assets",
                    failedStep=f"resolve_{version}", target=args.target,
                    previous=args.previous, repository=args.repository, steps=steps,
                )
            if archive and checksums:
                if not archive.is_file() or not checksums.is_file():
                    return _emit(
                        args.output, status="FAIL", errorType="local_asset_missing",
                        failedStep=f"resolve_{version}", target=args.target,
                        previous=args.previous, repository=args.repository, steps=steps,
                    )
                cached[version] = (archive.resolve(), checksums.resolve())
                steps.append({"name": f"resolve_{version}", "source": "local"})
                continue
            try:
                cached[version] = _download_release_assets(
                    temporary_root, repository=args.repository, version=version
                )
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                steps.append({"name": f"download_{version}", "status": "FAIL"})
                return _emit(
                    args.output, status="FAIL",
                    errorType="release_download_failed",
                    failedStep=f"download_{version}",
                    failureClass=type(exc).__name__, target=args.target,
                    previous=args.previous, repository=args.repository, steps=steps,
                )
        for name, version in (
            ("install_previous", args.previous),
            ("upgrade_target", args.target),
            ("rollback_previous", args.previous),
        ):
            command = [
                powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(installer), "-Version", version,
                "-Repository", args.repository,
                "-InstallDirectory", str(install_dir), "-NoPathUpdate",
                "-ArchivePath", str(cached[version][0]),
                "-ChecksumsPath", str(cached[version][1]),
            ]
            try:
                completed = subprocess.run(
                    command, capture_output=True, text=True, timeout=720
                )
            except subprocess.TimeoutExpired:
                steps.append({"name": name, "status": "TIMEOUT"})
                return _emit(
                    args.output, status="FAIL", errorType="release_install_timeout",
                    failedStep=name, target=args.target, previous=args.previous,
                    repository=args.repository, steps=steps,
                )
            steps.append({"name": name, "exitCode": completed.returncode})
            if completed.returncode != 0:
                return _emit(
                    args.output, status="FAIL", errorType="release_install_failed",
                    failedStep=name, target=args.target, previous=args.previous,
                    repository=args.repository, steps=steps,
                )
            binary = install_dir / "agenthub.exe"
            check = subprocess.run([binary, "doctor"], capture_output=True, text=True, timeout=120)
            steps.append({"name": f"doctor_{name}", "exitCode": check.returncode})
            if check.returncode != 0:
                return _emit(
                    args.output, status="FAIL", errorType="doctor_failed",
                    failedStep=f"doctor_{name}", target=args.target,
                    previous=args.previous, repository=args.repository, steps=steps,
                )
    return _emit(
        args.output, status="PASS", target=args.target, previous=args.previous,
        repository=args.repository, steps=steps,
    )


def _download_release_assets(
    root: Path, *, repository: str, version: str
) -> tuple[Path, Path]:
    """Download each release once; the installer remains the checksum authority."""
    tag = version if version.startswith("cli-v") else f"cli-v{version}"
    version_dir = root / tag
    version_dir.mkdir(parents=True, exist_ok=True)
    archive = version_dir / "agenthub-windows-x64.zip"
    checksums = version_dir / "checksums.txt"
    base = f"https://github.com/{repository}/releases/download/{tag}"
    for name, destination in (
        (archive.name, archive),
        (checksums.name, checksums),
    ):
        request = urllib.request.Request(
            f"{base}/{name}", headers={"User-Agent": "AgentHub-Release-Verifier/1"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            with destination.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
    return archive, checksums


def _emit(output: Path | None, **fields: object) -> int:
    record = new_evidence(scope="github-release", evidence_level="production", **fields)
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    write_evidence(record, scope="github-release", mirror_path=output)
    return 0 if record["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
