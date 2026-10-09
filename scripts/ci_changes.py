"""Select expensive CI checks from versioned dependency boundaries.

The root Python suite, PostgreSQL integration and quality gate always run.
An absent base (manual run or initial push) runs every optional check. Invalid
Git references fail instead of silently omitting validation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

GO_IMAGES = (
    ("gateway", "gateway-service"),
    ("orchestrator", "realtime-orchestrator"),
    ("session", "session-service"),
    ("stream-delivery", "stream-delivery-service"),
    ("sandbox", "sandbox-service"),
    ("mcp-gateway", "mcp-gateway"),
    ("iam", "iam-service"),
    ("audit-log", "audit-log-service"),
)


def select_checks(paths: list[str], *, all_checks: bool = False) -> dict:
    paths = [path.replace("\\", "/") for path in paths]
    all_checks |= any(
        path.startswith(".github/workflows/")
        or path in {"scripts/ci_changes.py", ".dockerignore"}
        for path in paths
    )

    def changed(*prefixes: str) -> bool:
        return all_checks or any(path.startswith(prefixes) for path in paths)

    contracts = changed("platform/contracts/", "tests/contracts/")
    go = contracts or changed("services/go/")
    frontend = contracts or changed("frontend/", "app/api/", "tests/api/")
    rust = contracts or changed("services/rust/")
    windows_cli = contracts or changed(
        "app/", "desktop/", "tests/cli/", "tests/desktop/", "tests/npm/",
        "requirements", "pyproject.toml", "release/", "scripts/build-cli-",
    )
    images = []
    if go:
        images.extend(
            {"name": name, "file": f"services/go/{module}/Dockerfile"}
            for name, module in GO_IMAGES
        )
    if changed("services/python/"):
        images.append({
            "name": "model-adapter",
            "file": "services/python/model_adapter_service/Dockerfile",
        })
    if frontend:
        images.append({"name": "frontend", "file": "frontend/Dockerfile"})
    return {
        "go": go, "frontend": frontend, "rust": rust,
        "windows_cli": windows_cli, "docker": bool(images),
        "images": {"include": images},
        "smoke": go or changed(
            "services/python/", "deploy/", "scripts/ci-smoke-test.sh",
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--head-ref", default="HEAD")
    args = parser.parse_args()
    all_checks = not args.base_ref or set(args.base_ref) == {"0"}
    paths = []
    if not all_checks:
        diff = subprocess.run(
            ["git", "diff", "--name-only", "-z", args.base_ref, args.head_ref],
            check=True, capture_output=True,
        )
        paths = [os.fsdecode(path) for path in diff.stdout.split(b"\0") if path]
    checks = select_checks(paths, all_checks=all_checks)
    print(json.dumps(checks, indent=2))
    if output := os.getenv("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.writelines(
                f"{name}={json.dumps(value, separators=(',', ':'))}\n"
                for name, value in checks.items()
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
