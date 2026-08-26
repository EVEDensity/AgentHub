"""T2/T3/T5 — migration integrity, rollback drill, auth & secret hygiene.

Unit-level enforcement of the rollout plan:
  T2  model_configs/env → new-api channels: inactive & local rows skipped,
      dedup by (provider, base_url), report never persists raw keys.
  T3  rollback: gateway switch off restores per-provider adapters; adapter
      falls back to mock when ENABLE_REAL_LLM is off / key missing.
  T5  secret hygiene: env-key loader collects only known *_API_KEY vars; the
      migration report artifact never contains raw keys (real 401 rejection
      is exercised in the e2e matrix).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.newapi.migrate_models import (
    MigratedChannel,
    MigrationReport,
    _load_env_keys,
    _redact,
    _report_payload,
    build_channels,
)

# ── T2: migration integrity ────────────────────────────────────────────────

def test_migration_skips_inactive_and_local_provider_rows() -> None:
    rows = [
        {"provider": "openai", "model_name": "gpt-4o", "api_key": "sk-real-encrypted",
         "base_url": "https://api.openai.com/v1", "is_active": True},
        {"provider": "openai", "model_name": "gpt-4o-mini", "api_key": "sk-real-encrypted",
         "base_url": "https://api.openai.com/v1", "is_active": True},
        {"provider": "qwen", "model_name": "qwen-max", "api_key": "", "base_url": "", "is_active": True},
        {"provider": "mock", "model_name": "mock", "api_key": "", "base_url": "", "is_active": True},
        {"provider": "ollama", "model_name": "llama3", "api_key": "", "base_url": "http://127.0.0.1:11434", "is_active": True},
        {"provider": "deepseek", "model_name": "deepseek-chat", "api_key": "sk-dead", "base_url": "", "is_active": False},
    ]
    channels, skipped = build_channels(rows, {}, include_mock=False)
    openai = [c for c in channels if c.provider == "openai"]
    # openai rows dedup into one channel carrying both models
    assert len(openai) == 1 and sorted(openai[0].models) == ["gpt-4o", "gpt-4o-mini"]
    assert not [c for c in channels if c.provider == "deepseek"]  # inactive excluded
    assert any(c.provider == "qwen" for c in channels)  # console completes key
    assert {s.provider for s in skipped} == {"mock", "ollama"}
    assert all(s.skip_reason for s in skipped)


def test_migration_report_never_persists_raw_keys(tmp_path) -> None:
    secret = "sk-super-secret-value-1234567890"
    channels = [
        MigratedChannel("openai", "c1", 1, "https://x/v1", ["m1"],
                        key=secret, key_redacted=_redact(secret)),
    ]
    report = MigrationReport(channels=channels, skipped=[], token_name="agenthub-gateway")
    blob = json.dumps(_report_payload(report), ensure_ascii=False)
    assert secret not in blob
    assert re.search(r"sk-super-secret", blob) is None
    assert _report_payload(report)["channels"][0]["key"] == ""
    assert _report_payload(report)["channels"][0]["key_redacted"].endswith("***")


def test_redact_short_and_long_values() -> None:
    assert _redact("") == ""
    assert _redact("abc") == "***"
    assert _redact("sk-abcdefgh1234") == "sk-a***"


# ── T3: rollback drill ─────────────────────────────────────────────────────

def test_rollback_switch_restores_per_provider_adapters(monkeypatch) -> None:
    from app.services.adapter_manager import AdapterManager

    manager = AdapterManager()
    monkeypatch.setattr("app.services.adapter_manager.LLM_GATEWAY", "newapi")
    assert type(manager.get_adapter("qwen")).__name__ == "NewAPIGatewayAdapter"
    assert type(manager.get_adapter("mock")).__name__ == "MockAdapter"  # local never hijacked
    monkeypatch.setattr("app.services.adapter_manager.LLM_GATEWAY", "")  # rollback
    assert type(manager.get_adapter("qwen")).__name__ == "QwenAdapter"
    manager.adapters.pop("newapi", None)


def test_adapter_falls_back_to_mock_without_key_or_flag(monkeypatch) -> None:
    import asyncio

    from app.services.adapter_manager import NewAPIGatewayAdapter, adapter_manager

    monkeypatch.setattr("app.services.adapter_manager.LLM_GATEWAY", "newapi")
    monkeypatch.setattr("app.services.adapter_manager.ENABLE_REAL_LLM", False)
    adapter = adapter_manager.get_adapter("openai")
    assert isinstance(adapter, NewAPIGatewayAdapter)
    try:
        result = asyncio.run(adapter.execute_prompt("回滚演练", model="mock-llm", api_key="", base_url=""))
        assert "mock" in result.lower()
    finally:
        adapter_manager.adapters.pop("newapi", None)


# ── T5: secret hygiene ─────────────────────────────────────────────────────

def test_env_keys_loader_collects_only_known_api_keys(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-ok-123\n"
        "DATABASE_URL=postgres://user:pass@host/db\n"
        "ANTHROPIC_API_KEY=sk-ant-zzz\n",
        encoding="utf-8",
    )
    keys = _load_env_keys(str(env_file))
    assert keys["OPENAI_API_KEY"] == "sk-ok-123"
    assert keys["ANTHROPIC_API_KEY"] == "sk-ant-zzz"
    assert "DATABASE_URL" not in keys  # unrelated secrets never collected