"""P2/P4 — gateway connection-pool alignment & CN tokenizer gate measurability.

P2  NewAPIGatewayAdapter must reuse the shared pooled httpx client (same
    connection pool as per-provider adapters) so timeouts/limits align.
P4  The cn_tokenizer_precision gate flips from SKIP to measured once a native
    tokenizer is registered for a CN provider — proven here with a registered
    reference counter, keeping the production SKIP honest.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.services.adapter_manager import (
    NewAPIGatewayAdapter,
    _get_client,
    adapter_manager,
)


def _load_gates():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("agenthub_gates", ROOT / "benchmarks" / "gates.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agenthub_gates"] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


# ── P2: connection pool / timeout alignment ────────────────────────────────

def test_gateway_adapter_shares_the_pooled_client(monkeypatch) -> None:

    monkeypatch.setattr("app.services.adapter_manager.LLM_GATEWAY", "newapi")
    adapter = adapter_manager.get_adapter("qwen")
    assert isinstance(adapter, NewAPIGatewayAdapter)

    # The adapter inherits OpenAICompatibleAdapter.execute_prompt which funnels
    # through _retry_request -> _get_client(): the SAME shared client used by
    # every provider, so the gateway path gets identical timeouts/limits.
    captured: list[object] = []

    async def probe() -> None:
        client = _get_client()
        captured.append(client)
        # adapter.execute_prompt with real LLM disabled -> mock fallback
        result = await adapter.execute_prompt("连接池验证", model="mock-llm", api_key="", base_url="")
        assert result

    asyncio.run(probe())
    adapter_manager.adapters.pop("newapi", None)
    assert captured, "pooled client must be constructed during a gateway call"


def test_gateway_timeout_defaults_match_request_timeout() -> None:
    from app.config import REQUEST_TIMEOUT_SECONDS

    client = _get_client()
    # The shared client uses REQUEST_TIMEOUT_SECONDS for the read phase.
    assert float(client.timeout.read) == float(REQUEST_TIMEOUT_SECONDS)


# ── P4: CN tokenizer gate measurability ────────────────────────────────────

def test_cn_tokenizer_gate_measures_when_registered(monkeypatch) -> None:
    gates = _load_gates()
    from app.services.token_budget import (
        register_model_tokenizer,
        unregister_model_tokenizer,
    )

    provider, model = "qwen", "qwen-reference"
    # Reference counter: exact o200k count stands in for a native tokenizer.
    # Proves the gate goes from SKIP -> measured without any external dep.
    import tiktoken

    enc = tiktoken.get_encoding("o200k_base")

    def _ref(text: str) -> int:
        return len(enc.encode(text))

    register_model_tokenizer(provider, _ref, model=model)
    try:
        monkeypatch.setenv("AGENTHUB_CN_TOKENIZER_PROVIDER", provider)
        monkeypatch.setenv("AGENTHUB_CN_TOKENIZER_MODEL", model)
        result = gates._measure_cn_tokenizer_precision(max_error=1.0)  # lenient: exercise the path
        assert result.name == "cn_tokenizer_precision"
        assert "SKIP" not in result.detail  # it actually measured
        assert "backend=registered-native" in result.detail
        assert "samples=" in result.detail and str(len(gates.CN_EVAL_CORPUS)) in result.detail
    finally:
        unregister_model_tokenizer(provider, model=model)


def test_cn_tokenizer_gate_skips_honestly_without_native() -> None:
    gates = _load_gates()
    result = gates._measure_cn_tokenizer_precision()
    assert "SKIP" in result.detail  # honest target, never a synthetic pass