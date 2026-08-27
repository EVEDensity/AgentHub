"""Calibrate the per-provider CN token ratio from real tokenizer assets (R4).

Given provisioned native tokenizers (see ``benchmarks/fetch_tokenizers.py``),
this tool measures each provider's actual ``tokens / CJK-char`` distribution
over the gate's eval corpus and prints suggested calibration constants for
``app/services/token_budget.CN_TOKEN_RATIOS``.

This exists because the generic CJK coefficient (0.9) was measured at ~40%
error against Qwen's BPE — the precision gate caught it, and this is how the
per-family constants get re-derived whenever a new provider family is added
or its vocab refreshes. Never hand-wave the constant; re-run this against a
fresh asset.

Usage:
    python benchmarks/calibrate_cn_estimator.py                 # all with assets
    python benchmarks/calibrate_cn_estimator.py --provider qwen
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Must mirror app.services.token_budget._WIDE_CJK_RE (han + CJK punctuation
# + fullwidth forms) — the calibration constant is fitted on this denominator.
WIDE_CJK_RE = re.compile(r"[\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF]")
PROVIDERS = ("qwen", "deepseek")


def calibrate(provider: str) -> None:
    from app.services.token_budget import (
        _local_provider_tokenizer,
        estimate_tokens_multilingual,
    )

    tokenizer = _local_provider_tokenizer(provider, "")
    if tokenizer is None:
        print(f"[skip] {provider}: no asset configured "
              f"(run benchmarks/fetch_tokenizers.py --provider {provider})")
        return

    # Gate corpus lives in gates.py; import lazily so this tool stays usable
    # even if that module grows CLI behaviour.
    spec_dependent = __import__("benchmarks.gates", fromlist=["CN_EVAL_CORPUS"])
    corpus: list[str] = getattr(spec_dependent, "CN_EVAL_CORPUS", [])

    exact: list[int] = []
    wide_counts: list[int] = []
    ascii_counts: list[int] = []
    old_errors: list[float] = []
    for text in corpus:
        encoded = tokenizer.encode(text)
        n = len(encoded.ids) if hasattr(encoded, "ids") else len(encoded)
        wide = len(WIDE_CJK_RE.findall(text))
        ascii_rest = len(text) - wide
        exact.append(n)
        wide_counts.append(wide)
        ascii_counts.append(ascii_rest)
        old_errors.append(abs(estimate_tokens_multilingual(text, provider) - n) / max(1, n))

    ratios = sorted(
        (n - (a + 3) // 4) / w
        for n, w, a in zip(exact, wide_counts, ascii_counts)
        if w > 0
    )
    raw_ratios = sorted(
        n / w for n, w in zip(exact, wide_counts) if w > 0
    )
    suggestion = round(statistics.median(ratios), 2)

    print(f"== {provider} ==")
    print(f"  samples={len(corpus)} raw tokens/wide-cjk "
          f"min={raw_ratios[0]:.3f} med={raw_ratios[len(raw_ratios) // 2]:.3f} "
          f"max={raw_ratios[-1]:.3f}")
    print(f"  current-table p95 error="
          f"{sorted(old_errors)[int(len(old_errors) * .95) - 1]:.1%}")
    print(f"  -> suggest CN_TOKEN_RATIOS['{provider}'] = {suggestion}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, action="append",
                        help="calibrate only these providers (default: all)")
    args = parser.parse_args()
    for provider in (args.provider or PROVIDERS):
        try:
            calibrate(provider)
        except Exception as exc:  # noqa: BLE001 — driver reports every failure
            print(f"[error] {provider}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
