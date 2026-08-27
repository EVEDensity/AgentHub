"""M2 — daily usage exporter for the new-api gateway.

Pulls the gateway's consumption logs (quota/token/model per request) and
writes a per-day JSON report, plus an optional Prometheus textfile-style
gauge (``agenthub_newapi_*``) so the M3 alert
``NewAPIChannelKeyBalanceLow`` and cost panels can consume it.

HTTP failures are retried up to 2 times with exponential backoff (2s/4s);
every failure emits a structured JSON log line on stderr (never silent).

Usage:
  python deploy/newapi/export_usage.py --since-days 1 \
      --base-url http://127.0.0.1:3000 --root-token sk-agenthub-root
  python deploy/newapi/export_usage.py --out data/newapi-usage-daily.json

Secrets are never written to the report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.newapi.migrate_models import newapi_admin_init

DEFAULT_BASE_URL = "http://127.0.0.1:3000"


def _get_with_retry(base_url: str, path: str, headers: dict,
                    *, attempts: int = 3) -> httpx.Response:
    """带重试的 GET：初次请求 + 最多 2 次重试，指数退避（2s/4s）。

    每次失败都向 stderr 打一行结构化 JSON 日志（不静默吞掉），
    重试耗尽后抛出最后一次异常，由调用方/外层循环决定下一步。
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = httpx.get(f"{base_url}{path}", headers=headers, timeout=15)
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001 — 统一在此记录后按策略重试
            last_error = exc
            payload = {
                "level": "error",
                "event": "export_http_failure",
                "url": f"{base_url}{path}",
                "attempt": attempt + 1,
                "attempts_total": attempts,
                "retries_left": attempts - attempt - 1,
                "error_type": type(exc).__name__,
                "error": str(exc)[:200],
            }
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
            if attempt < attempts - 1:
                time.sleep(float(2 ** (attempt + 1)))
    assert last_error is not None
    raise last_error


def _collect_logs(base: str, headers: dict, since_ts: int) -> list[dict]:
    """Page through new-api /api/log/ and keep entries after ``since_ts``."""
    collected: list[dict] = []
    page = 1
    while True:
        r = _get_with_retry(base, f"/api/log/?p={page}&page_size=100", headers)
        r.raise_for_status()
        payload = r.json().get("data") or {}
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not items:
            break
        for item in items:
            if (item.get("created_at") or 0) >= since_ts:
                collected.append(item)
        if len(items) < 100:
            break
        page += 1
    return collected


def _summarize(logs: list[dict]) -> dict:
    by_model: dict[str, dict] = defaultdict(lambda: {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0})
    by_channel: dict[str, dict] = defaultdict(lambda: {"requests": 0, "tokens": 0})
    total = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
    for log in logs:
        model = str(log.get("model_name") or "unknown")
        channel = str(log.get("channel_name") or "unknown")
        prompt_t = int(log.get("prompt_tokens") or 0)
        completion_t = int(log.get("completion_tokens") or 0)
        total["requests"] += 1
        total["prompt_tokens"] += prompt_t
        total["completion_tokens"] += completion_t
        by_model[model]["requests"] += 1
        by_model[model]["prompt_tokens"] += prompt_t
        by_model[model]["completion_tokens"] += completion_t
        by_channel[channel]["requests"] += 1
        by_channel[channel]["tokens"] += prompt_t + completion_t
    return {
        "total": total,
        "by_model": dict(by_model),
        "by_channel": dict(by_channel),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="export new-api daily usage")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--root-token", default=os.getenv("NEWAPI_ROOT_TOKEN", "sk-agenthub-root"))
    parser.add_argument("--since-days", type=int, default=1)
    parser.add_argument("--out", default=str(ROOT / "data" / "newapi-usage-daily.json"))
    parser.add_argument("--prometheus-gauge", default="",
                        help="also write a Prometheus textfile with channel balances")
    args = parser.parse_args(argv)

    import asyncio

    token = asyncio.run(newapi_admin_init(args.base_url.rstrip("/"), args.root_token))
    if not token:
        print(json.dumps({
            "level": "error",
            "event": "admin_token_failed",
            "detail": "could not obtain admin token from new-api",
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    headers = {"Authorization": f"Bearer {token}"}

    since = datetime.now(UTC) - timedelta(days=args.since_days)
    since_ts = int(since.timestamp())
    logs = _collect_logs(args.base_url.rstrip("/"), headers, since_ts)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "since_iso": since.isoformat(),
        "records": len(logs),
        **_summarize(logs),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] {len(logs)} records -> {out}")
    print(f"     total requests={report['total']['requests']} "
          f"prompt={report['total']['prompt_tokens']} "
          f"completion={report['total']['completion_tokens']}")

    if args.prometheus_gauge:
        # Textfile-format gauge for channel balances (M3 alert support).
        channels = _get_with_retry(args.base_url.rstrip("/"),
                                   "/api/channel/?p=1&size=100",
                                   headers).json().get("data") or {}
        items = channels.get("items") if isinstance(channels, dict) else channels
        lines = ["# HELP agenthub_newapi_channel_min_balance minimum channel balance",
                 "# TYPE agenthub_newapi_channel_min_balance gauge"]
        balance = 0.0
        for ch in (items or []):
            try:
                b = float(ch.get("balance") or 0.0)
            except (TypeError, ValueError):
                b = 0.0
            if b and (balance == 0.0 or b < balance):
                balance = b
        lines.append(f"agenthub_newapi_channel_min_balance {balance}")
        Path(args.prometheus_gauge).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[ok] prometheus gauge -> {args.prometheus_gauge}")
    return 0


if __name__ == "__main__":
    sys.exit(main())