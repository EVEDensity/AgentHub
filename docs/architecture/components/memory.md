# AgentHub Memory Architecture

> Status: slimmed to L0/L1 (ADR-0107)
> Last reviewed: 2026-09-01
> ADR: [0107-memory-slimming-web-chat-decommission](../decisions/0107-memory-slimming-web-chat-decommission.md)

## 1. What remains

The multi-layer cognitive pipeline (L2 vector retrieval, L3 global
summaries, semantic extraction/consolidation, procedural catalog, and the
NATS→Rust→Python summarization consumer) was removed with the web-chat
memory decommission. Memory is now a two-layer store:

| Layer | Source | Online behavior |
| --- | --- | --- |
| L0 working memory | PostgreSQL `messages` / Mission SQLite transcript | Latest conversation transcript, deduplicated and token-budgeted |
| L1 session memory | `users/{user}/sessions/{session}/conversation.md` + session summary | Recent durable turns plus a semantic session summary |

Online projection for the legacy chat runtime is built by
`app/services/agent/context.py::_build_memory_context` and is L0/L1 only:
session summary takes priority, the recent durable tail follows, and DB
history is the working transcript (excluded from the projection). New
product surfaces (CLI `run/exec`, Mission) build their own compact context
via `app/cli/runtime.py::build_compact_context` — an incremental,
change-only rollup over Mission state (objective, decisions, constraints,
open items), which is the canonical "distill-what-changed" pattern for the
whole product.

Request path:

```text
DB history (L0)
  -> token budget
  -> session summary + recent durable turns (L1)
  -> agent prompt
```

## 2. Compression policy

- DB history is limited before prompt construction using the selected
  model's tokenizer and context window.
- Session summary has higher prompt priority than raw durable turns.
- Raw durable turns that overlap the DB transcript are removed by
  normalized block/shingle similarity.
- File-backed knowledge files are retrieval-only and do not consume every
  request's context window.

## 3. Token budget

`app/services/token_budget.py` is the single budget authority.

- Uses `tiktoken` for supported OpenAI model families.
- Uses a conservative multilingual fallback for providers without a bundled
  native tokenizer.
- Resolves model context windows and reserves output capacity.
- Applies section budgets for history, memory, preprocessing, collaboration,
  tools, and current user content.

Native Chinese-provider tokenizers can be registered in code or loaded from
local `tokenizer.json` paths through the
`AGENTHUB_TOKENIZER_<PROVIDER>_PATH` variables; the fallback remains
explicitly reported as a multilingual estimator. Billing parity is enforced
by the `cn_tokenizer_precision` bench gate
(`benchmarks/gates.py`, run in the `docs-gates` CI job; honest SKIP until
an asset is provisioned).

## 4. Guidance for future memory work

Per ADR-0107, any new memory surface must stay a horizontal, token-lean
slice:

1. **L0 = transcript, single source of truth** — never duplicate it into
   another store.
2. **L1 = incremental, change-only summaries** — fold new turns into an
   existing digest with a covered-range marker; never re-summarize the
   whole history (`build_compact_context` is the reference implementation).
3. **Project facts = flat, key-scoped notes** (e.g. `.agenthub/memory.md`,
   Markdown headers per `AGENTS.md` sections); new value supersedes old,
   same key — no full rewrites.
4. **Gated injection, no default retrieval** — inject only what matches the
   current objective's tags/keywords; no embedding/vector path by default.
   Add one only behind an explicit opt-in with acceptance criteria.

## 5. Current assessment

Strengths:

- Tenant-scoped durable session memory with summaries.
- Prompt projection is bounded, deduplicated, and retrieval-first.
- Token economy has a single authority (`token_budget`) with explicit
  multilingual fallback.

Remaining gaps:

1. Native CN tokenizers not yet available for every provider; parity gate
   SKIPs honestly until provisioned.
2. Summary quality is a heuristic signal, not an evaluator-model score.
3. In-process prompt caches are worker-local; multi-replica deployments
   still need shared cache invalidation.

## 6. Next improvements

### Short term

- Add Qwen, DeepSeek, Doubao, GLM, and Claude native tokenizer adapters
  (load path exists; parity enforced by `cn_tokenizer_precision`).
- ✅ Done (2026-09-01): L1 session summaries are now change-only folds —
  `SessionMemoryManager.update_session_summary` merges the existing digest
  with only the turns after its cursor
  (`app/services/memory/session_memory.py`; covered by
  `app/services/memory/test_session_incremental.py`). Same
  "distill-what-changed" pattern as `build_compact_context`.

### Medium term

- Move the web surface onto Mission + v1 API and let it reuse
  `build_compact_context` end-to-end (removes the legacy L1 chat store).
- Revisit vector retrieval only behind a concrete use case with acceptance
  criteria (ADR-0107).

### Long term

- Knowledge-graph memory stays off the near-term plan (ADR-0106 / ADR-0107);
  revisit when a cross-mission entity-query consumer lands on the roadmap.