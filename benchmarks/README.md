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

## How to add a gate

1. Add a script under `benchmarks/` producing machine-readable output.
2. Wire it into CI (`.github/workflows/ci.yml`) as a hard gate.
3. Link the gate from the claim's documentation.
4. Record before/after numbers in the PR.