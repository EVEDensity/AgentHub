# Benchmark Gates

> Status: draft
> Owner: performance maintainers
> Last reviewed: 2026-08-26
> Scope: CI-enforced performance gates backing public claims

## Purpose

Every near-term performance claim in public documentation must be provable by
a gate in this directory. Until a gate exists, the claim is demoted to
"target" (see `docs/zh/guide/what-is-agenthub.md` capability table).

## Rule

- A claim may appear as "implemented" only when the matching gate passes on CI.
- A claim without a gate is a target value and must be worded as such.
- Benchmark regressions fail the merge; the author reports before/after
  numbers in the PR description.

## Initial gates (Phase R1)

| Gate | Claim it proves | Source of truth |
|---|---|---|
| `api_latency_p95` | API P95 < 200ms per `docs/zh/advanced/performance.md` | `app/services/performance_monitor.py` histogram + `tests/` contract |
| `token_compaction_ratio` | compaction reduction >= 25% | `app/services/context_compaction.py` test fixtures |
| `knowledge_retrieval_p95` | retrieval P95 < 80ms (target until gate passes) | Qdrant/BM25 probe |
| `streaming_ttft` | streaming first token < threshold | gateway SSE probe (`deploy/newapi/channel_probe.py`) |

## Phase R4 additions

| Tool/Gate | Purpose | Notes |
|---|---|---|
| `cn_tokenizer_precision` | billing parity: `count_tokens` must reproduce native-tokenizer counts within <5% p95 once an asset is provisioned; honest SKIP otherwise | assets under `assets/tokenizers/` (gitignored); estimator residuals are reported in the gate detail for calibration follow-up only |
| `fetch_tokenizers.py` | provision Qwen / DeepSeek fast tokenizers from HF (mirror fallback), verified through the production loader path | no credentials involved; prints exact env exports for CI |
| `calibrate_cn_estimator.py` | re-derive per-provider `CN_TOKEN_RATIOS` (tokens/wide-CJK char) whenever a family's vocab refreshes | never hand-edit constants — re-run this against fresh assets |
| `knowledge_retrieval_p95` | L2VectorIndex.search() p95 < 80ms over the offline eval set (210 samples, correctness@3 sanity) | seeded from the same corpus as the recall gate; zero-hit probe fails loudly |
| `code_file_size` / `code_complexity` | new code stays <=800 lines and McCabe <=20; legacy ratchet recorded in `quality_exemptions.json` may only shrink (any growth beyond the recorded cap fails) | keys are class-qualified (`file:Class.method`) so same-name methods never collide; refresh baselines deliberately via `gen_quality_exemptions.py` |
| `test_coverage` | python-service line-rate >= 60% when a coverage.xml artifact exists; honest SKIP otherwise | enforced in the CI `python` job which runs `pytest --cov=services --cov-report=xml` |
| `multimodal_e2e_probe` | opt-in vision e2e through a real new-api channel: dual-track image+text must get a non-empty reply with billed prompt tokens | honest SKIP without `NEWAPI_BASE_URL` + `AGENTHUB_TEST_CHANNEL_KEY` (secrets only via env); CI reads them from repo secrets |

Workflow for a new CN provider family:

```bash
# 1. add its HF repo to REPOS in fetch_tokenizers.py, then:
python benchmarks/fetch_tokenizers.py --provider <name>
# 2. derive the constant and extend CN_TOKEN_RATIOS in app/services/token_budget.py:
python benchmarks/calibrate_cn_estimator.py --provider <name>
# 3. prove the wiring end to end:
AGENTHUB_TOKENIZER_<NAME>_PATH=assets/tokenizers/<name> \
AGENTHUB_CN_TOKENIZER_PROVIDER=<name> \
python benchmarks/gates.py run --name cn_tokenizer_precision
```

## How to add a gate

1. Add a script under `benchmarks/` producing machine-readable output.
2. Wire it into CI (`.github/workflows/ci.yml`) as a hard gate.
3. Link the gate from the claim's documentation.
4. Record before/after numbers in the PR.