"""Post-deployment verification for the new-api LLM gateway.

Checks, in order:
  1. new-api is reachable (login with root token / use bearer).
  2. A canary channel exists pointing at the local OpenAI-compatible
     ``mock-llm`` upstream (creates it on first run via migration primitives).
  3. A scoped sub-token ``agenthub-gateway`` exists (created on first run).
     new-api masks keys in API responses; for local verification the raw key
     is read from the local SQLite file; otherwise pass --gateway-key.
  4. ``/v1/models`` and ``/v1/chat/completions`` return successfully through
     the gateway using that token — end-to-end AgentHub → new-api → upstream.

Usage:
  python deploy/newapi/verify_newapi.py
  python deploy/newapi/verify_newapi.py --gateway-key sk-xxxx
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.newapi.migrate_models import (  # noqa: E402
    DEFAULT_BASE_URL,
    MigratedChannel,
    apply_migration,
    newapi_admin_init,
)

CANARY = MigratedChannel(
    provider="openai", name="agenthub-mock-canary", type=1,
    base_url="http://127.0.0.1:8099/v1", models=["mock-llm"],
    key="not-needed", key_redacted="not-needed",
)


def _read_gateway_key_from_sqlite(db_path: str) -> str:
    """Read the raw token key from the local new-api SQLite file.

    Local-verification only: new-api masks keys in every API response, so the
    only programmatic way to obtain it locally is the database file.
    """
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT key FROM tokens WHERE name='agenthub-gateway' ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        conn.close()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row else ""


def main(argv: list[str] | None = None) -> int:
    import asyncio

    from datetime import UTC, datetime

    parser = argparse.ArgumentParser(description="verify new-api gateway is callable")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--root-token", default="sk-agenthub-root")
    parser.add_argument("--mock-url", default="http://127.0.0.1:8099")
    parser.add_argument("--gateway-key", default="")
    parser.add_argument("--sqlite-db", default="one-api.db")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")

    async def run() -> int:
        token = await newapi_admin_init(base, args.root_token)
        if not token:
            print("[FAIL] could not obtain new-api admin token")
            return 1
        headers = {"Authorization": f"Bearer {token}"}

        # 1) canary channel + scoped token (idempotent via migrate primitives)
        CANARY.base_url = args.mock_url
        report, _map = await apply_migration(base, token, [CANARY], token_name="agenthub-gateway")
        print(f"[ok] channels/token ensured (token={report.token_value_redacted or 'masked'})")

        # 2) resolve the callable gateway key (local sqlite or explicit)
        gateway_key = args.gateway_key or _read_gateway_key_from_sqlite(args.sqlite_db)
        if not gateway_key:
            print("[FAIL] no gateway key available; pass --gateway-key (copied from "
                  "the new-api console token page after creation)")
            return 1
        print(f"[ok] gateway key used: {gateway_key[:6]}***")

        # 3) confirm canary channel exists via admin API (paginated list)
        resp = await httpx.AsyncClient(timeout=15).get(
            f"{base}/api/channel/?p=1&size=100", headers=headers)
        items = ((resp.json().get("data") or {}).get("items") or [])
        canary = [c for c in items if c.get("name") == "agenthub-mock-canary"]
        print(f"[ok] canary channel present: {bool(canary)} (total channels={len(items)})")

        # 4) OpenAI-compatible probes through the gateway entry
        gheaders = {"Authorization": f"Bearer {gateway_key}"}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{base}/v1/models", headers=gheaders)
            print(f"[probe] GET /v1/models -> {resp.status_code}")
            if resp.status_code != 200:
                print(resp.text[:300])
                return 1
            models = [m.get("id") for m in (resp.json().get("data") or [])]
            print(f"[ok] models exposed: {sorted(models)}")
            if "mock-llm" not in models:
                print(f"[warn] mock-llm not in {models}; check channel model mapping")

            chat = await client.post(
                f"{base}/v1/chat/completions",
                headers=gheaders,
                json={"model": "mock-llm", "messages": [{"role": "user", "content": "验证网关链路"}]},
            )
            print(f"[probe] POST /v1/chat/completions -> {chat.status_code}")
            if chat.status_code != 200:
                print(chat.text[:400])
                return 1
            content = chat.json()["choices"][0]["message"]["content"]
            print(f"[ok] gateway reply: {content[:120]}")
            if "mock-llm" not in content:
                print("[warn] reply does not contain mock marker; check channel routing")

        print(f"[PASS] gateway verified at {datetime.now(UTC).isoformat()}")
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())