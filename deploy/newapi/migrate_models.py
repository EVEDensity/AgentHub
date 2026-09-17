"""Migrate AgentHub LLM provider configuration into new-api channels.

Reads sources of truth and creates equivalent new-api channels + one
aggregated token via the new-api admin API.

Sources
-------
1. PostgreSQL ``model_configs`` table (provider/model_name/api_key/base_url),
   when ``AGENTHUB_DATABASE_URL`` is set.
2. Environment-based keys (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, ...)
   read from the current process env (or ``--env-file``).

Mapping
-------
* provider -> new-api channel: OpenAI-compatible providers become channel
  ``type=1`` with their base_url; anthropic becomes ``type=2``; mock/ollama
  rows are skipped unless ``--include-mock``.
* token: one channel-token ``agenthub-gateway`` bound to all migrated models.
* artifact: writes ``migration-report.json`` (secrets redacted) + a mapping
  table for the AgentHub side.

Usage
-----
  python deploy/newapi/migrate_models.py --dry-run
  python deploy/newapi/migrate_models.py --apply
  python deploy/newapi/migrate_models.py --apply --base-url http://127.0.0.1:3000 --root-token sk-agenthub-root
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:3000"
# new-api channel types (subset relevant to AgentHub providers).
CHANNEL_TYPES: dict[str, int] = {
    "openai": 1,
    "deepseek": 1,
    "minimax": 1,
    "zhipu": 1,
    "qwen": 1,
    "doubao": 1,
    "kimi": 1,
    "customopenai": 1,
    "openai_compatible": 1,
    "anthropic": 2,
    "ollama": 1,
    "mock": 1,
}
# Environment keys that carry provider credentials (provider -> env var).
PROVIDER_ENV_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}

# Providers that cannot be reimbursed (local) — skipped by default.
LOCAL_PROVIDERS = {"ollama", "mock"}


@dataclass
class MigratedChannel:
    provider: str
    name: str
    type: int
    base_url: str
    models: list[str]
    key: str = ""
    key_redacted: str = ""
    created_id: int | None = None
    skip_reason: str = ""


@dataclass
class MigrationReport:
    channels: list[MigratedChannel]
    skipped: list[MigratedChannel]
    token_name: str
    token_value_redacted: str = ""
    model_map: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.model_map is None:
            self.model_map = {}


def _load_env_keys(env_file: str | None) -> dict[str, str]:
    keys: dict[str, str] = {}
    sources: list[tuple[str, Path | None]] = [(p, Path(p)) for p in (env_file,) if env_file]
    for var in PROVIDER_ENV_KEYS.values():
        value = os.getenv(var, "")
        if value:
            keys[var] = value
    if not sources and os.getenv("AGENTHUB_ENV_FILE"):
        sources.append(("dotenv", Path(os.getenv("AGENTHUB_ENV_FILE", ""))))
    for _, path in sources:
        if not path or not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            if key in PROVIDER_ENV_KEYS.values():
                keys[key] = value
    return keys


def _load_db_configs() -> list[dict[str, Any]]:
    """Read model_configs from Postgres when a database URL is configured."""
    dsn = os.getenv("AGENTHUB_DATABASE_URL", os.getenv("DATABASE_URL", "")).strip()
    if not dsn:
        return []
    try:
        import asyncpg  # type: ignore[import-not-found]
    except ImportError:
        print("[warn] asyncpg not installed; skipping DB model_configs", file=sys.stderr)
        return []

    import asyncio

    async def fetch() -> list[dict[str, Any]]:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                "SELECT provider, model_name, api_key, base_url, is_active "
                "FROM model_configs ORDER BY id"
            )
            return [
                {"provider": str(r["provider"]), "model_name": str(r["model_name"]),
                 "api_key": r["api_key"] or "", "base_url": str(r["base_url"] or ""),
                 "is_active": bool(r["is_active"])}
                for r in rows
            ]
        finally:
            await conn.close()

    return asyncio.run(fetch())


def _decrypt_db_key(cipher: str) -> str:
    """Decrypt secrets stored via app.services.secret_service (Fernet)."""
    if not cipher:
        return ""
    try:
        from app.services.secret_service import decrypt_secret
        return decrypt_secret(cipher)
    except Exception:  # noqa: BLE001 — any decrypt failure means "no usable key"
        return ""


def _redact(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "***" if len(value) > 8 else "***"


def _report_payload(report: MigrationReport) -> dict[str, Any]:
    """Serialize a report without ever persisting raw channel keys."""
    payload = asdict(report)
    for ch in payload["channels"] + payload["skipped"]:
        ch["key"] = ""
    return payload


def build_channels(db_rows: list[dict[str, Any]], env_keys: dict[str, str],
                   *, include_mock: bool) -> tuple[list[MigratedChannel], list[MigratedChannel]]:
    channels: list[MigratedChannel] = []
    skipped: list[MigratedChannel] = []
    seen: set[tuple[str, str]] = set()  # (provider, base_url)

    def add(provider: str, model: str, key: str, base_url: str) -> None:
        provider_key = provider.lower()
        if (provider_key in LOCAL_PROVIDERS and not include_mock) or provider_key == "mock" and not include_mock:
            skipped.append(MigratedChannel(provider, "", 0, "", [model], key, _redact(key), skip_reason="local provider"))
            return
        bucket = (provider_key, base_url or "default")
        if bucket in seen:
            for ch in channels:
                if (ch.provider, ch.base_url or "default") == bucket:
                    ch.models.append(model)
                    return
        seen.add(bucket)
        channels.append(MigratedChannel(
            provider=provider_key,
            name=f"agenthub-{provider_key}-{len(seen)}",
            type=CHANNEL_TYPES.get(provider_key, 1),
            base_url=base_url,
            models=[model],
            key=key,
            key_redacted=_redact(key),
        ))

    for row in db_rows:
        if not row["is_active"]:
            continue
        key = _decrypt_db_key(str(row.get("api_key") or ""))
        if not key and row["provider"].lower() in {"mock", "ollama"}:
            key = "not-needed"
        add(row["provider"], row["model_name"], key, row.get("base_url") or "")

    for provider, env_var in PROVIDER_ENV_KEYS.items():
        value = env_keys.get(env_var)
        if not value:
            continue
        existing = next((ch for ch in channels if ch.provider == provider), None)
        if existing:
            continue
        add(provider, provider, value, CHANNEL_TYPES.get(provider, 1) == 2 and "https://api.anthropic.com" or "")

    # Deduplicate model names per channel.
    for ch in channels:
        ch.models = sorted(set(ch.models))
    return channels, skipped


async def newapi_admin_init(base_url: str, root_token: str, admin_token: str = "") -> str:
    """Return an admin auth token.

    Priority: explicit ``--admin-token``; then ``root_token`` used as a
    Bearer token if it already works; otherwise root/password login where
    ``root_token`` is the password (Covers both deployments that set
    INITIAL_ROOT_TOKEN as a real master token and those where setup creates
    the root account with a chosen password).
    """
    async with httpx.AsyncClient(timeout=10) as client:
        if admin_token:
            return admin_token
        if root_token.startswith("sk-"):
            probe = await client.get(
                f"{base_url}/api/self",
                headers={"Authorization": f"Bearer {root_token}"},
            )
            if probe.status_code == 200:
                return root_token
        resp = await client.post(
            f"{base_url}/api/user/login",
            json={"username": "root", "password": root_token},
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("data") or {}).get("access_token", "")


async def apply_migration(base_url: str, token: str,
                          channels: list[MigratedChannel],
                          token_name: str = "agenthub-gateway") -> tuple[MigrationReport, dict[str, str]]:
    headers = {"Authorization": f"Bearer {token}"}
    model_map: dict[str, str] = {}
    existing: set[tuple[str, int]] = set()
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(f"{base_url}/api/channel/?p=1&size=100", headers=headers)
            resp.raise_for_status()
            payload = resp.json().get("data") or {}
            items = payload.get("items") if isinstance(payload, dict) else payload
            for item in (items or []):
                existing.add((item.get("name"), int(item.get("type") or 0)))
        except Exception as exc:  # noqa: BLE001 — upstream API contract variance; proceed
            print(f"[warn] channel list failed: {exc}", file=sys.stderr)

        for ch in channels:
            if (ch.name, ch.type) in existing:
                print(f"[skip] channel {ch.name!r} already exists")
                continue
            payload = {
                "channel": {
                    "name": ch.name,
                    "type": ch.type,
                    "key": ch.key or "not-needed",
                    "base_url": ch.base_url,
                    "models": ",".join(ch.models),
                    "model_mapping": json.dumps({m: m for m in ch.models}, ensure_ascii=False),
                    "group": "default",
                },
                "mode": "single",
            }
            resp = await client.post(f"{base_url}/api/channel/", headers=headers, json=payload)
            if resp.status_code >= 400:
                print(f"[warn] channel {ch.name} create failed: {resp.status_code} {resp.text[:200]}")
                continue
            body = resp.json()
            if not body.get("success", True):
                print(f"[warn] channel {ch.name} create rejected: {body.get('message', '')[:200]}")
                continue
            ch.created_id = int((body.get("data") or {}).get("id", 0) or 0)
            for m in ch.models:
                model_map[m] = m
            print(f"[ok] channel {ch.name} created (id={ch.created_id}, models={ch.models})")

        # idempotent token: reuse the existing gateway token if present
        token_value = ""
        existing_tokens = set()
        try:
            resp = await client.get(f"{base_url}/api/token/?p=1&size=100", headers=headers)
            payload = resp.json().get("data") or {}
            items = payload.get("items") if isinstance(payload, dict) else payload
            for item in (items or []):
                existing_tokens.add(item.get("name"))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] token list failed: {exc}", file=sys.stderr)

        if token_name in existing_tokens:
            print(f"[skip] token {token_name} already exists")
        else:
            token_payload = {
                "name": token_name,
                "remain_quota": -1,
                "expired_time": -1,
                "unlimited_quota": True,
                "model_limit_enabled": False,
                "models": "",
            }
            resp = await client.post(f"{base_url}/api/token/", headers=headers, json=token_payload)
            if resp.status_code < 400:
                payload = resp.json().get("data")
                if isinstance(payload, str):
                    token_value = payload
                elif isinstance(payload, dict):
                    token_value = str(payload.get("key") or "")
                if not token_value:
                    # new-api masks keys in API responses; the operator copies the
                    # full key from the token creation toast / console once.
                    print("[note] token created but key is masked by the API; copy it "
                          "from the new-api console and set AGENTHUB_NEWAPI_API_KEY")
                else:
                    print(f"[ok] token {token_name} created -> {_redact(token_value)}")
            else:
                print(f"[warn] token create failed: {resp.status_code} {resp.text[:200]}")

    return MigrationReport(channels=channels, skipped=[], token_name=token_name,
                           token_value_redacted=_redact(token_value), model_map=model_map), model_map


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="migrate AgentHub LLM configs into new-api")
    parser.add_argument("--apply", action="store_true", help="apply migration (default dry-run)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--root-token", default=os.getenv("NEWAPI_ROOT_TOKEN", "123456"))
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--include-mock", action="store_true")
    parser.add_argument("--report", default=str(ROOT / "deploy" / "newapi" / "migration-report.json"))
    args = parser.parse_args(argv)

    db_rows = _load_db_configs()
    env_keys = _load_env_keys(args.env_file)
    channels, skipped = build_channels(db_rows, env_keys, include_mock=args.include_mock)

    if not channels and not args.include_mock:
        # Always seed one OpenAI-compatible canary channel pointing at mock-llm
        # so the pipeline is testable end-to-end without provider keys.
        # NOTE: new-api auto-appends /v1 to the channel base_url, so the canary
        # base must NOT carry a /v1 suffix (else requests double into /v1/v1).
        canary_url = os.getenv("MOCK_LLM_URL", "http://127.0.0.1:8101")
        channels.append(MigratedChannel(
            provider="openai", name="agenthub-mock-canary", type=1,
            base_url=canary_url, models=["mock-llm"],
            key="not-needed", key_redacted="not-needed",
        ))

    print(f"[info] sources: db_rows={len(db_rows)} env_keys={list(env_keys)}")
    print(f"[info] channels={len(channels)} skipped={len(skipped)}")
    for ch in channels:
        print(f"   - {ch.name} type={ch.type} base={ch.base_url or '-'} models={ch.models} key={ch.key_redacted}")

    if not args.apply:
        print("[dry-run] no changes applied")
        report = MigrationReport(channels=channels, skipped=skipped, token_name="agenthub-gateway")
        Path(args.report).write_text(json.dumps(_report_payload(report), ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    import asyncio
    token = asyncio.run(newapi_admin_init(args.base_url.rstrip("/"), args.root_token))
    report, _map = asyncio.run(apply_migration(args.base_url.rstrip("/"), token, channels))
    report.skipped = skipped
    Path(args.report).write_text(json.dumps(_report_payload(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())