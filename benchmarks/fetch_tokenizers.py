"""Provision native CN tokenizer assets for the tokenizer-precision gate (R4).

Downloads fast-tokenizer ``tokenizer.json`` files for listed CN providers
from HuggingFace (primary) with a China-reachable mirror fallback, verifies
each asset by loading it through the SAME code path production uses
(``app/services/token_budget._local_provider_tokenizer``), and prints the
exact env exports needed so::

    python benchmarks/gates.py run --name cn_tokenizer_precision

flips from an honest SKIP to a MEASURED result (p95 estimator error < 5%).

No credentials are required — only public model repos are fetched, and no
API keys are ever involved. Assets land under ``assets/tokenizers/``
(gitignored): binary tokenizer files are re-provisioned deterministically,
never committed to the repository.

Usage:
    python benchmarks/fetch_tokenizers.py                 # all providers
    python benchmarks/fetch_tokenizers.py --provider qwen # one provider
    python benchmarks/fetch_tokenizers.py --dry-run       # plan only
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ASSETS_DIR = ROOT / "assets" / "tokenizers"

PRIMARY = "https://huggingface.co"
MIRROR_FALLBACKS = ("https://hf-mirror.com",)

# provider -> HF repo serving a fast tokenizer (tokenizer.json at repo root).
# DeepSeek-V3 keeps its own CN-tuned BPE; Qwen ships the canonical Qwen BPE.
REPOS: dict[str, str] = {
    "qwen": "Qwen/Qwen2.5-7B-Instruct",
    "deepseek": "deepseek-ai/DeepSeek-V3",
}

_PROBE_TEXT = (
    "自动回执：系统检测到配置变更，请前往控制台确认后再执行后续操作。"
    "AgentHub 的中文 token 计费对齐门禁需要在 CI 上给出可复现的实测值。"
)


def _download(repo: str) -> tuple[str, bytes]:
    last_err: Exception | None = None
    for base in (PRIMARY, *MIRROR_FALLBACKS):
        url = f"{base}/{repo}/resolve/main/tokenizer.json"
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "agenthub-r4-tokenizer-gate"})
            with urllib.request.urlopen(request, timeout=90) as resp:
                if getattr(resp, "status", 200) != 200:
                    last_err = RuntimeError(f"HTTP {resp.status} from {url}")
                    continue
                payload = resp.read()
            if payload:
                return url, payload
        except Exception as exc:  # noqa: BLE001 — network probes by nature
            last_err = exc
            print(f"[warn] {url}: {exc}", file=sys.stderr)
    raise ConnectionError(f"no source served tokenizer.json for {repo}: {last_err}")


def _verify_through_production_loader(provider: str, tokenizer_path: Path) -> int:
    """Load via the app runtime and report probe tokens; raises on failure."""
    from app.services.token_budget import (
        _local_provider_tokenizer,
        tokenizer_backend,
    )

    env_key = f"AGENTHUB_TOKENIZER_{provider.upper()}_PATH"
    os.environ[env_key] = str(tokenizer_path)
    _local_provider_tokenizer.cache_clear()

    tokenizer = _local_provider_tokenizer(provider.lower(), "")
    backend = tokenizer_backend(provider.lower(), "")
    if tokenizer is None or backend not in {"registered-native", "local-tokenizer-json"}:
        raise RuntimeError(
            f"asset failed to load in the app runtime (backend={backend!r}); "
            "is the optional 'tokenizers' package installed?")

    encoded = tokenizer.encode(_PROBE_TEXT)
    n_tokens = len(encoded.ids) if hasattr(encoded, "ids") else len(encoded)
    print(f"[ok ] runtime load OK: backend={backend} probe_chars="
          f"{len(_PROBE_TEXT)} probe_tokens={n_tokens}")
    return n_tokens


def fetch(provider: str) -> int:
    repo = REPOS[provider]
    dest_dir = ASSETS_DIR / provider
    dest = dest_dir / "tokenizer.json"

    if dest.is_file():
        print(f"[skip] {dest} already provisioned ({dest.stat().st_size:,} bytes)")
    else:
        url, payload = _download(repo)
        json.loads(payload)  # validity gate before anything touches disk
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()[:16]
        print(f"[ok ] {dest}\n       <- {url}\n"
              f"       bytes={len(payload):,} sha256[:16]={digest}")

    _verify_through_production_loader(provider, dest)

    print("\n# enable the precision gate locally / on CI:")
    print(f"#   AGENTHUB_TOKENIZER_{provider.upper()}_PATH={dest}")
    print("#   AGENTHUB_CN_TOKENIZER_PROVIDER=" + provider)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=sorted(REPOS), action="append",
                        help="fetch only these providers (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the provisioning plan without downloading")
    args = parser.parse_args()

    targets = args.provider or sorted(REPOS)
    if args.dry_run:
        for provider in targets:
            print(f"[plan] {provider}: {REPOS[provider]} -> "
                  f"{(ASSETS_DIR / provider / 'tokenizer.json')}")
        return 0
    rc = 0
    for provider in targets:
        try:
            rc |= fetch(provider)
        except Exception as exc:  # noqa: BLE001 — top-level driver must report all
            print(f"[error] {provider}: {exc}", file=sys.stderr)
            rc |= 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
