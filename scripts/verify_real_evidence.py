"""Run opt-in real-environment gates and emit an honest evidence record.

No secret is printed. Missing prerequisites produce SKIP, never PASS.
"""
from __future__ import annotations
import json, os, platform, shutil, subprocess, sys
from datetime import datetime, timezone

def check(name: str, available: bool, detail: str) -> dict:
    return {"name": name, "status": "READY" if available else "SKIP", "detail": detail}

def main() -> int:
    results = [
        check("real-provider", bool(os.environ.get("AGENTHUB_CLI_MODEL_API_KEY")), "secret configured" if os.environ.get("AGENTHUB_CLI_MODEL_API_KEY") else "AGENTHUB_CLI_MODEL_API_KEY missing"),
        check("real-tty", sys.stdin.isatty() and sys.stdout.isatty(), f"stdin={sys.stdin.isatty()} stdout={sys.stdout.isatty()}"),
        check("npm-registry", bool(shutil.which("npm")), shutil.which("npm") or "npm missing"),
        check("postgres-listener", bool(os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://"))), "DATABASE_URL configured" if os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")) else "PostgreSQL DATABASE_URL missing"),
    ]
    payload = {"schemaVersion": 1, "verificationLevel": "external", "platform": platform.platform(), "timestamp": datetime.now(timezone.utc).isoformat(), "results": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
