"""Optional real-provider streaming smoke test.

Usage: python scripts/cli_provider_smoke.py
Environment: AGENTHUB_CLI_MODEL_API_KEY, AGENTHUB_CLI_PROVIDER,
AGENTHUB_CLI_MODEL, AGENTHUB_CLI_MODEL_BASE_URL.
The script never prints credentials and returns 0 for PASS or SKIP.
"""
from __future__ import annotations

import json
import os
import sys

import httpx


def main() -> int:
    key = os.environ.get("AGENTHUB_CLI_MODEL_API_KEY", "").strip()
    if not key:
        print("SKIP: AGENTHUB_CLI_MODEL_API_KEY is not set")
        return 0
    provider = os.environ.get("AGENTHUB_CLI_PROVIDER", "openai").strip()
    model = os.environ.get("AGENTHUB_CLI_MODEL", "").strip() or "deepseek-chat"
    base = os.environ.get("AGENTHUB_CLI_MODEL_BASE_URL", "").strip().rstrip("/")
    if not base:
        base = "https://api.deepseek.com"
    url = base + "/v1/chat/completions"
    body = {"model": model, "stream": True, "messages": [{"role": "user", "content": "Reply with the word READY."}]}
    chunks = 0
    try:
        with httpx.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = payload.get("choices") or []
                if choices and (choices[0].get("delta") or {}).get("content"):
                    chunks += 1
    except (httpx.HTTPError, OSError) as exc:
        print(f"FAIL: {provider} streaming request failed ({type(exc).__name__})")
        return 1
    if chunks == 0:
        print(f"FAIL: {provider} returned no text chunks")
        return 1
    print(f"PASS: {provider}/{model} returned {chunks} text chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
