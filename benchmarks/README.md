# Benchmark Gates

> Status: draft
> Owner: performance maintainers
> Last reviewed: 2026-08-30
> Scope: CI-enforced performance gates backing public claims + local codegen baseline

## Purpose

Every near-term performance claim in public documentation must be provable by
a gate in this directory. Until a gate exists, the claim is demoted to
"target" (see `docs/zh/guide/what-is-agenthub.md` capability table).

## Local code generation baseline (P0) & test-loop tasks (P1)

`cases/` + `run_benchmark.py` implement the two highest-priority items from
`docs/internal/architecture/codex-capability-gap-analysis.md`: a replayable
code-generation / bug-fix baseline that measures a real pass rate, and the
`VERIFY:` test-loop task type that turns an acceptance command's exit code
into the Mission verdict.

### Structure

- `cases/*.json` — 8 benchmark tasks. Each case carries `id`, `title`,
  `objective` (task description given to the model), `verify_command`
  (acceptance command) and `setup` (seed workspace files). Coverage:
  3 single-file python bugs (logic / boundary / exception handling),
  2 cross-file python tasks (call-signature / return-shape mismatch),
  2 pure-generation tasks (implement a specified module), 1 one-line
  regression fix. Every case ships a `check.py` acceptance script that
  prints `OK` on success.
- All 8 cases are **benchmark v2** (`"check_external": true`): the
  acceptance script source lives in the case's `check` field and is
  materialized by `run_benchmark.py` OUTSIDE the model-reachable workspace,
  under `benchmarks/.runs/<run>/checks/<case>/check.py`. The effective
  `VERIFY:` command executes that script by absolute path with the workspace
  as cwd (the generated script inserts the cwd into `sys.path` so case
  modules still resolve). Cases without `check_external` keep the legacy
  in-workspace behavior.
- `run_benchmark.py` — per case: builds an isolated workspace, starts a
  dedicated mission-control process (SQLite-isolated, desktop local runner
  enabled), creates a Mission whose objective ends with
  `VERIFY: <effective verify command>`, waits for the terminal state, replays
  the acceptance command in the workspace, and aggregates a JSON report plus
  a terminal table.

### Usage

```powershell
# The model API key is read from the environment only (missing -> exit 2,
# never written to disk or to result files).
$env:AGENTHUB_DESKTOP_MODEL_API_KEY = "sk-..."
.venv\Scripts\python.exe benchmarks\run_benchmark.py
# Subset / model overrides:
.venv\Scripts\python.exe benchmarks\run_benchmark.py --cases py-logic-discount,gen-todo `
    --provider deepseek --model deepseek-v4-flash --max-iterations 8
```

Each run keeps its workspaces and per-case SQLite databases under
`benchmarks/.runs/<run_id>/` (gitignored); result JSON lands in
`benchmarks/results/<run_id>.json` (gitignored).

### Metrics

| Metric | Meaning |
|---|---|
| `verify_passed` / pass rate | `verify_command` exit code 0 when replayed in the workspace after the Mission reached a terminal state — this is the benchmark truth |
| `mission_status` | Durable Mission verdict (`SUCCEEDED` means the runner finished AND the `VERIFY:` gate passed; `FAILED` covers both execution and acceptance failure) |
| `metrics.iterations` / `total_tokens` / `model_cost` | Summarized from the mission's harness `execution_checkpoints` (last checkpoint per attempt is cumulative usage) |
| `duration_seconds` | Wall clock from workspace creation to terminal state |
| `setup_files_modified` | Set to true when the model edited a seeded workspace file; the flag keeps such runs detectable (check.py itself is not seeded — see benchmark v2 below) |

### P1 contract: `VERIFY:` test-loop tasks

`app/services/runner/loops.py` (re-exported through
`app/services/desktop_local_runner.py`) parses any objective line starting
with `VERIFY:` before submitting unattended PASS Evidence, runs the command
in the workspace (timeout `AGENTHUB_DESKTOP_LOCAL_RUNNER_VERIFY_COMMAND_TIMEOUT`,
default 120 s) and submits FAIL Evidence with the last 2000 output characters
when it exits non-zero — which transitions the WorkUnit/Mission to `FAILED`.
Derivation does not retry failed Missions on its own, so a FAIL is final.
The benchmark cases are the live carrier of this path.

Checker integrity (benchmark v2): the acceptance script is no longer part of
the model-reachable workspace — `check_external` cases materialize it under
`benchmarks/.runs/<run>/checks/<case>/` and the objective no longer invites
the model to run or modify it. Tampering with the checker is therefore out
of the model's reach; `setup_files_modified` continues to flag edits to the
seeded workspace files, and flagged runs remain invalid rather than
failures.

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