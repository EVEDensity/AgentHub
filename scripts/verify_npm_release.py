"""Run an explicit clean-environment npm install/upgrade/rollback gate.

This command mutates only the invoking user's global npm prefix and should be
run inside a disposable VM/container.  It records command names and exit
codes, never command output, tokens, or environment secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.production_evidence import new_evidence, write_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if shutil.which("npm") is None:
        return _emit(args.output, status="SKIP", errorType="npm_unavailable")
    if platform.system() != "Windows":
        return _emit(args.output, status="SKIP", errorType="unsupported_platform", platform=platform.system())

    commands = [
        ("view_target", ["npm", "view", f"@agenthub/cli@{args.target}", "version"]),
        ("install_target", ["npm", "install", "--global", f"@agenthub/cli@{args.target}"]),
        ("doctor_target", ["agenthub", "doctor"]),
        ("version_target", ["agenthub", "--version"]),
        ("install_previous", ["npm", "install", "--global", f"@agenthub/cli@{args.previous}"]),
        ("version_previous", ["agenthub", "--version"]),
        ("install_target_again", ["npm", "install", "--global", f"@agenthub/cli@{args.target}"]),
        ("version_target_again", ["agenthub", "--version"]),
        ("rollback_previous", ["npm", "install", "--global", f"@agenthub/cli@{args.previous}"]),
        ("version_rollback", ["agenthub", "--version"]),
    ]
    results: list[dict[str, object]] = []
    for name, command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
            results.append({"name": name, "exitCode": completed.returncode})
            if completed.returncode != 0:
                return _emit(args.output, status="FAIL", errorType="command_failed", failedStep=name, steps=results)
        except FileNotFoundError:
            error_type = "npm_unavailable" if command[0] == "npm" else "launcher_unavailable"
            status = "SKIP" if command[0] == "npm" else "FAIL"
            return _emit(args.output, status=status, errorType=error_type, failedStep=name, steps=results)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _emit(args.output, status="FAIL", errorType=type(exc).__name__, failedStep=name, steps=results)
    return _emit(args.output, status="PASS", target=args.target, previous=args.previous, steps=results)


def _emit(output: Path | None, **fields: object) -> int:
    record = new_evidence(scope="registry", evidence_level="production", **fields)
    rendered = json.dumps(record, ensure_ascii=False, sort_keys=True)
    print(rendered)
    write_evidence(record, scope="registry", mirror_path=output)
    return 0 if record["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
