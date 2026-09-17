"""Generic real-channel probe through the local new-api gateway (A1).

Idempotent per (channel name); the provider key arrives via env only.
Usage:
    AGENTHUB_TEST_CHANNEL_KEY=<key> python deploy/newapi/channel_probe.py \
        --channel-name zhipu-real --upstream https://open.bigmodel.cn/api/paas/v4 \
        --model glm-4-flash [--channel-type 24]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="probe a real provider channel via new-api")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--channel-name", required=True)
    parser.add_argument("--channel-type", type=int, default=1, help="new-api channel type (1=OpenAI)")
    parser.add_argument("--upstream", required=True, help="channel base_url WITHOUT /v1 when type=1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--root-password", default=os.getenv("NEWAPI_ROOT_PASSWORD", "sk-agenthub-root"))
    parser.add_argument("--sqlite-db", default=str(Path(os.environ.get("TEMP", "")) / "newapi" / "one-api.db"))
    parser.add_argument("--no-stream", action="store_true", help="skip SSE probe (some models stream-empty)")
    args = parser.parse_args(argv)
    KEY = os.environ.get("AGENTHUB_TEST_CHANNEL_KEY", "")
    if not KEY:
        print("[FAIL] AGENTHUB_TEST_CHANNEL_KEY not set (env only — never on argv)")
        return 1

    BASE = args.base_url.rstrip("/")
    MODEL = args.model
    results: list[tuple[str, str, bool]] = []

    def record(name: str, detail: str, ok: bool) -> None:
        results.append((name, detail, ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    # ── ensure root/channel/token exist (idempotent) ────────────────────
    status0 = httpx.get(f"{BASE}/api/status", timeout=10).json()["data"]
    if not status0.get("setup"):
        httpx.post(f"{BASE}/api/setup", json={
            "username": "root", "password": args.root_password,
            "confirmPassword": args.root_password, "siteName": "agenthub-e2e"})
    login = httpx.post(f"{BASE}/api/user/login",
                       json={"username": "root", "password": args.root_password})
    assert login.status_code == 200 and login.json().get("success"), login.text
    hdr = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    listing = httpx.get(f"{BASE}/api/channel/?p=1&size=100", headers=hdr, timeout=15).json()
    items = ((listing.get("data") or {}).get("items")) or []
    mine = [c for c in items if c.get("name") == args.channel_name]
    for ch in mine:
        if (MODEL not in str(ch.get("models") or "")
                or int(ch.get("type") or 0) != args.channel_type
                or str(ch.get("base_url") or "") != args.upstream):
            httpx.delete(f"{BASE}/api/channel/{ch['id']}", headers=hdr, timeout=15)
            print(f"[info] dropped stale channel id={ch['id']} type={ch.get('type')} "
                  f"base={ch.get('base_url')} models={ch.get('models')}")
    if any(MODEL in str(c.get("models")) for c in mine):
        record("channel-create",
               f"exists type={args.channel_type} upstream={args.upstream} models=[{MODEL}]", True)
    else:
        r = httpx.post(f"{BASE}/api/channel/", headers=hdr, json={
            "channel": {"name": args.channel_name, "type": args.channel_type,
                        "key": KEY, "base_url": args.upstream,
                        "models": MODEL, "group": "default"},
            "mode": "single"})
        body = r.json()
        record("channel-create",
               f"type={args.channel_type} upstream={args.upstream} "
               f"models=[{MODEL}] success={body.get('success')}",
               r.status_code == 200 and body.get("success"))

    conn = sqlite3.connect(args.sqlite_db)
    row = conn.execute("SELECT key FROM tokens WHERE name='e2e-gateway'").fetchone()
    conn.close()
    if row:
        gw_key = row[0]
        record("token-created", f"name=e2e-gateway key={gw_key[:6]}*** (existing)", True)
    else:
        httpx.post(f"{BASE}/api/token/", headers=hdr, json={
            "name": "e2e-gateway", "remain_quota": -1, "expired_time": -1,
            "unlimited_quota": True, "model_limit_enabled": False, "models": ""})
        conn = sqlite3.connect(args.sqlite_db)
        gw_key = conn.execute("SELECT key FROM tokens WHERE name='e2e-gateway'").fetchone()[0]
        conn.close()
        record("token-created", f"name=e2e-gateway key={gw_key[:6]}*** (new)", True)

    ghdr = {"Authorization": f"Bearer {gw_key}"}

    t0 = time.perf_counter()
    probe = httpx.get(f"{BASE}/v1/models", headers=ghdr, timeout=15)
    record("models-listed",
           f"status={probe.status_code} models={[m.get('id') for m in probe.json().get('data', [])]}",
           probe.status_code == 200)

    # ── sync chat ────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    r = httpx.post(f"{BASE}/v1/chat/completions", headers=ghdr, timeout=60, json={
        "model": MODEL,
        "messages": [{"role": "user", "content": "只回复四个字：链路正常"}]})
    ok = r.status_code == 200
    detail = ""
    if ok:
        body = r.json()
        msg = body["choices"][0]["message"]
        content = str(msg.get("content") or "")
        reasoning = str(msg.get("reasoning_content") or "")
        usage = body.get("usage", {})
        shown = content or (f"[reasoning-only] {reasoning[:80]}...")
        detail = (f"{(time.perf_counter() - t0) * 1000:.0f}ms content={shown[:60]!r} "
                  f"usage={usage.get('prompt_tokens')}+{usage.get('completion_tokens')}")
        ok = bool(content or reasoning)
    else:
        detail = f"status={r.status_code} {r.text[:220]}"
    record("chat-sync", detail, ok)

    # ── SSE streaming ────────────────────────────────────────────────────
    if not args.no_stream:
        ttft = None
        chunks = 0
        got_content = False
        with httpx.stream("POST", f"{BASE}/v1/chat/completions", headers=ghdr, timeout=60,
                          json={"model": MODEL, "stream": True,
                                "messages": [{"role": "user", "content": "数到三，用中文"}]}) as resp:
            status = resp.status_code
            if status == 200:
                start = time.perf_counter()
                for line in resp.iter_lines():
                    if line.startswith("data:") and "[DONE]" not in line:
                        chunks += 1
                        if ttft is None:
                            ttft = (time.perf_counter() - start) * 1000
                        if '"content"' in line and len(line) > 40:
                            got_content = True
        record("chat-sse",
               (f"status={status} chunks={chunks} ttft={ttft:.0f}ms" if ttft is not None else f"status={status}")
               + (" (content-bearing chunk seen)" if got_content else ""),
               status == 200 and chunks > 0)

    # ── tool calls passthrough ───────────────────────────────────────────
    r = httpx.post(f"{BASE}/v1/chat/completions", headers=ghdr, timeout=60, json={
        "model": MODEL,
        "messages": [{"role": "user", "content": "北京今天天气如何？请调用工具查询"}],
        "tools": [{"type": "function", "function": {
            "name": "web_search", "description": "搜索实时信息",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                           "required": ["query"]}}}],
        "tool_choice": "auto"})
    ok = r.status_code == 200
    if ok:
        msg = r.json()["choices"][0]["message"]
        tcs = msg.get("tool_calls") or []
        detail = ("tool_calls=" + "; ".join(
            f"{tc['function']['name']}({str(tc['function'].get('arguments'))[:80]})" for tc in tcs)
            if tcs else f"plain-text={str(msg.get('content'))[:60]!r}")
    else:
        detail = f"status={r.status_code} {r.text[:180]}"
    record("tool-calls", detail, ok)

    print("\n==== summary ====")
    failed = [n for n, _d, ok2 in results if not ok2]
    for name, d, ok2 in results:
        print(f"{'PASS' if ok2 else 'FAIL'} {name}: {d}")
    print(f"\nverified_at={datetime.now(UTC).isoformat()} gateway={BASE}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())