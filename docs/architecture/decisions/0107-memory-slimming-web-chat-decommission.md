# ADR-0107: Slim the Memory Subsystem to L0/L1 and Decommission the Web Chat Memory Path

> Status: accepted
> Owner: memory & retrieval maintainers
> Date: 2026-09-01
> Scope: app/services/memory/, app/api/memory.py, app/services/agent/,
> app/services/memory_summary_consumer.py, benchmarks/, docs/

## Context

The memory subsystem (~3,370 lines across 13 modules) is built around a
cognitive L0-L4 pipeline: L2 vector retrieval (`l2_vector.py`), L3 global
summaries, semantic extraction/consolidation, a procedural catalog, and a
NATS→Rust compaction→Python summarization consumer. Code review on
2026-09-01 established two facts:

1. **Only one entry point consumes the heavy layers**: the legacy web chat
   runtime `agent_service.py → orchestrator → _build_memory_context`. The
   product path the roadmap actually ships around — CLI `run/exec`, Mission
   control, `/compact` and `/replay` — never imports any of it; it builds
   context with `app/cli/runtime.py::build_compact_context` over the Mission
   SQLite state (a Codex-style lightweight design, already in production).
2. **The web chat path is being decommissioned.** The frontend chat surface
   remains temporarily unavailable while the web surface is reworked onto
   the v1/Mission API; nothing new may be built on the orchestrator runtime.

Keeping ~2,500 lines of multi-layer memory machinery alive solely for a
decommissioned entry point is the "memory is too heavy" debt the north-star
plan calls out (`docs/roadmaps/north-star-developer-cli-experience.md` §2/§3).

## Decision

**Replace the heavy memory subsystem with a two-layer store and remove the
layers that have no consumer.**

* **Keep (L0/L1, the only layers with live consumers)**:
  `app/services/memory/models.py`, `storage.py`, `session_memory.py`
  (session summaries), `session_store.py` (durable conversation files),
  `summary_version.py`. These back the legacy session durability and the
  reduced chat context injection.
* **Delete** (no consumer after the web chat decommission):
  `l2_vector.py`, `semantic_memory.py`, `procedural_memory.py`,
  `consolidator.py`, `extractor.py`, `scanner.py`,
  `app/services/memory_context.py`, `app/services/memory_summary_consumer.py`
  and their package-local tests.
* **Decommission the `/api/memory` HTTP surface** and the NATS summary
  consumer wiring (`main.py` lifespan, `websocket_processor.py`).
* **Narrow `_build_memory_context`** (`app/services/agent/context.py`) to
  L0/L1 only: session summary (priority) + recent durable turns. Semantic,
  L3-global, procedural, and budgeted-section projection are gone.
* **Retire the L2 benchmark gates** (`knowledge_retrieval_recall`,
  `knowledge_retrieval_p95`) and their CI steps, since their dependency
  (`l2_vector.py`) is deleted. This supersedes the L2 contract recorded in
  ADR-0106; L3 stays off any near-term timeline and is now further out —
  revisit only when a concrete consumer lands on the roadmap.

### Recommended storage shape going forward (token-efficient, accurate)

For the v1/Mission surface (CLI today, web after rework), the memory
subsystem should stay a horizontal slice, not a layered pipeline:

1. **L0 working memory = the session/Mission transcript** in SQLite. It is
   the only replayable source of truth; never duplicated into another store.
2. **L1 rollup = incremental, change-only summaries.** Each compaction folds
   the *new* turns into an existing digest (objective, decisions,
   constraints, open items, covered message range) instead of re-summarizing
   the whole history — token cost stays bounded and monotonically flat.
   The production `/compact` implementation
   (`app/cli/runtime.py::build_compact_context`) is the canonical example.
3. **Project facts = a flat, key-scoped notes file** (`.agenthub/memory.md`,
   Markdown headers per `AGENTS.md`-style sections). Facts are appended or
   replaced in place (new value supersedes old, same key), never re-written
   from scratch. This mirrors how Codex/Claude Code keep durable facts cheap.
4. **Gated injection, no default retrieval.** Only inject what matches the
   current objective's tags/keywords; never inject the whole store. No
   embedding/vector path by default — add one only behind an explicit
   opt-in when a retrieval use case with acceptance criteria exists.

Rationale for token reduction: ignoring duplicates (1), diff-only summaries
(2), flat facts with supersede semantics (3), and keyword-gated injection
(4) eliminate the systematic waste of the old design — whole-corpus
re-summarization, dedup pass per call, and budget arbitration across four
cognitive classes.

Rationale for accuracy: injecting only relevant, versioned facts (newer
wins) reduces noise in the prompt, which measurably beats injecting more
context for next-token quality on retrieval tasks.

## Consequences

* `app/services/memory/` shrinks from ~3,370 to ~1,300 lines (models,
  storage, session_memory, session_store, summary_version).
* The web chat runtime compiles and runs with L0/L1-only memory; its
  `/api/memory` panel endpoints return 404 (accepted; web chat is
  temporarily unavailable per roadmap direction).
* CI `docs-gates` job loses two steps and the R4 benchmark table loses the
  retrieval rows; `performance.md` keeps its latencies with gates where
  the implementation still exists.
* No new work is built on the orchestrator runtime; new chat surfaces must
  target Mission + v1 API + `build_compact_context`-style memory.

## Alternatives considered

* **Keep the subsystem, disable at runtime** — rejected: the weight is
  mostly dead code and maintenance surface; a flag does not remove it.
* **Delete `orchestrator`/`agent_service` entirely now** — rejected for
  scope: the L0/L1 store still serves session durability during the web
  rework, and removing the whole runtime is a separate, later step.
* **Keep L2 behind a flag** — rejected: L2's only consumer was the web
  chat search, which is decommissioned; a dormant vector store is exactly
  the "heavy memory" this ADR removes.

## Verification

* `python -m pytest` for kept modules' tests
  (`app/services/memory/test_summary_version.py`,
  `test_cognitive_memory_models.py`) passes.
* `python benchmarks/gates.py check-docs` and `check-links` pass; no gate
  references deleted modules.
* `node_modules/.bin/tsc --noEmit` in `frontend/` still compiles (frontend
  memory UI is runtime-only 404s, no type break).
* Grep shows no import of the deleted module names anywhere outside
  `docs/` history.

## Supersedes

Partially supersedes ADR-0106: "L2 gates remain the retrieval contract" no
longer holds — L2 is removed, not failed. The L3 judgment ("stays off the
near-term timeline") is unchanged and reinforced.