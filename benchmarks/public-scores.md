# Public Benchmark Scores

> Status: live — scores below are replayable with `benchmarks/run_benchmark.py`
> Owner: performance maintainers
> Last reviewed: 2026-09-01
> Scope: citable pass-rate scores for the developer CLI execution loop

Every score on this page comes from a real model channel (no mock, no
synthetic success) and is replayable by anyone with the same model key:

```powershell
$env:AGENTHUB_DESKTOP_MODEL_API_KEY = "sk-..."
py benchmarks\run_benchmark.py --provider <provider> --model <model>
```

## Methodology (honest scope)

- The suite is AgentHub's **local code-generation benchmark v2** — 8
  tasks (3 single-file python bug fixes, 2 cross-file fixes, 2
  from-scratch module implementations, 1 regression fix), each accepted
  only when an out-of-workspace acceptance script exits 0 (the same
  acceptance-command paradigm as Terminal-Bench-style evaluations).
- Pass rate counts cases where the mission reached `SUCCEEDED` **and**
  the acceptance command passed when replayed after the run.
- The verifier is independent of the executing agent; a run is only
  valid when no seeded checker tampering is flagged.
- This is a small in-house suite, **not** the official Terminal-Bench
  task set. Treat the numbers as a reproducible baseline for the CLI
  execution loop on these 8 tasks, not as a Terminal-Bench leaderboard
  entry.

## Scores

| Date | Model | Provider | Score | Avg tokens | Avg wall | Notes |
|---|---|---|---|---|---|---|
| 2026-09-01 | deepseek-v4-flash | deepseek (official API) | **8/8 (100%)** | 40,542 | 59.7s | max_iterations=8; all missions SUCCEEDED with replayed acceptance PASS; full per-case JSON retained locally under `benchmarks/results/` (gitignored, contains machine paths) |

### How to read this

- **Score** — passed cases / total cases on that date, with the model
  named explicitly. Scores are point-in-time: provider-side model
  updates can change results without a change in this repository.
- **Avg tokens** — mean `total_tokens` across cases, summed from
  harness execution checkpoints (real billed usage).
- **Avg wall** — mean seconds from workspace creation to mission
  terminal state.

## Adding a score

1. Run the full suite with a real key (never commit the key).
2. Append one row above with date, model, provider, and the honest
   numbers from the summary table.
3. If any case failed, record the failure too — this page exists to be
   cited, and selective reporting would defeat that.
