"""Validate CLI release prerequisites without mutating the workspace."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys


def main() -> int:
    checks = {
        "python": sys.version_info >= (3, 11),
        "git": shutil.which("git") is not None,
        "node": shutil.which("node") is not None,
        "npm": shutil.which("npm") is not None,
    }
    result = subprocess.run([sys.executable, "-m", "app.cli", "--help"], capture_output=True, text=True)
    checks["cli_help"] = result.returncode == 0
    print(json.dumps({"schemaVersion": 1, "checks": checks, "ready": all(checks.values())}))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
