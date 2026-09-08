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
    args = parser.parse_args()
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if platform.system() != "Windows":
        return _emit(args.output, status="SKIP", errorType="unsupported_platform")
    if not powershell:
        return _emit(args.output, status="SKIP", errorType="powershell_unavailable")

    installer = ROOT / "release" / "install.ps1"
    steps: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="agenthub-release-") as temporary:
        install_dir = Path(temporary) / "bin"
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
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
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


def _emit(output: Path | None, **fields: object) -> int:
    record = new_evidence(scope="github-release", evidence_level="production", **fields)
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    write_evidence(record, scope="github-release", mirror_path=output)
    return 0 if record["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
