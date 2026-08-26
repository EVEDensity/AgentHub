# AgentHub Memory Architecture

> Status: implemented baseline  
> Last reviewed: 2026-08-08  
> This component is being consolidated into the Context Compiler and L0-L4
> Memory Layers described in the private target architecture.

## 1. Current online projection

Memory now has two orthogonal classifications:

- Existing `type=user|feedback|project|reference` remains the content category.
- `memory_type=working|episodic|semantic|procedural` is the cognitive type.
- `scope`, `source`, and monotonic `version` define ownership, provenance, and
  optimistic evolution without changing the underlying file/database storage.

AgentHub does not send every stored memory item to every model call. The online
prompt is assembled as a bounded projection:

| Layer | Current source | Online behavior | Maturity |
| --- | --- | --- | --- |
| L0 working memory | PostgreSQL `messages` | Latest conversation transcript, deduplicated and token-budgeted | Usable |
| L1 session memory | `users/{user}/sessions/{session}/conversation.md` and session summary | Recent durable turns plus semantic session summary | Usable, partly duplicated at rest |
| L2 retrieval memory | File memories and memory search tools | Not injected by default; loaded on demand through `memory_search` | Partial; no unified vector lifecycle |
| L3 global memory | Per-user global summary | Cross-session decisions and preferences, token-budgeted | Partial; freshness and provenance are coarse |
| Segment compaction | Rust memory-segment-core | Triggered from the online session path, emits token reduction and retained messages | Integrated |
| Semantic consolidation | Python summarization-service | Converts Rust structural compaction into a durable semantic summary | Integrated |

The request path is:

```text
DB history (L0)
  -> token budget
  -> session summary + durable recent turns + global summary
  -> semantic/exact overlap removal
  -> per-model memory budget
  -> agent prompt
```

Session conversations, session summaries, and `task_execution_history` are
classified as Episodic Memory. Existing Markdown files without the new
frontmatter fields are read as user-scoped Semantic Memory from a legacy file.

Explicit durable signals in episodic summaries (`preference`, `decision`,
`constraint`, and confirmed `fact`) are projected into structured Semantic
Memory records under each user's existing memory directory. Records retain
source session/event, confidence, status, and version. A conflicting value for
the same semantic key supersedes the old record instead of overwriting its
history. Prompt assembly retrieves query-relevant active records plus durable
preferences; ordinary narrative sentences are not promoted automatically.

Procedural Memory is exposed as a read-through catalog over existing sources
of truth. Skills, DAG templates, user agent routes, memory files marked as SOP,
registered tools, and tool permission rules keep their original storage and
execution owners. The catalog adds a stable record ID, source/version,
content hash, scope, kind, and risk level without copying procedure bodies.
`GET /api/memory/procedural` provides the combined catalog and query view.

The prompt budget uses one model-aware cognitive context pool. Allocation is
intent-sensitive: conversational continuity favors Working/Episodic, research
favors Semantic, implementation/deployment/workflow tasks favor Procedural,
and planning balances Episodic with Procedural. Each class has an enforced
sub-budget in addition to the final model context-window guard.

The asynchronous write-back path is:

```text
session turn persisted
  -> agenthub.memory.compact.requested
  -> Rust compact/prune metrics
  -> memory.compact.completed
  -> Python semantic summary
  -> session.summary.generated
  -> online summary consumer
  -> per-user session summary + prompt cache invalidation
```

## 2. Compression policy

- DB history is limited before prompt construction using the selected model's
  tokenizer and context window.
- Session summary has higher prompt priority than raw durable turns.
- Raw durable turns that overlap the DB transcript are removed by normalized
  block and shingle similarity.
- Global summary is added last and is removed when it duplicates session
  context.
- File-backed knowledge is retrieval-only and does not consume every request's
  context window.
- Rust compaction defaults remain 40 messages or 32,000 estimated tokens, with
  10 recent messages retained.
- The online publisher samples every 10 turns; Rust decides whether the actual
  threshold has been reached.

## 3. Token budget

`app/services/token_budget.py` is the single budget authority.

- Uses `tiktoken` for supported OpenAI model families.
- Uses a conservative multilingual fallback for providers without a bundled
  native tokenizer.
- Resolves model context windows and reserves output capacity.
- Applies section budgets for history, memory, preprocessing, collaboration,
  tools, and current user content.
- Applies a final prompt guard to every agent prompt, including specialized
  CodeGen, Orchestrator, Architect, and Deploy prompts.

## 4. Observability

`GET /api/system/metrics` now exposes `tokenEconomy`:

- Tokens before and after compaction by section.
- Tokens saved and reduction rate.
- Truncation count.
- Route/agent summary cache entries, hits, misses, and hit ratio.
- Semantic summary tokens and estimated cost.
- Heuristic summary quality score and sample count.
- Operational answer-quality proxy and sample count, for detecting obvious
  empty/error/repetition regressions after budget changes.

The summarization service also exports Prometheus counters/gauges for summary
tokens, estimated cost, compaction tokens before/after, latency, status, and
quality.

Cross-service verification is available through
`app/services/memory/test_nats_memory_pipeline_integration.py`. With NATS,
memory-segment-core, summarization-service, and model-adapter-service running,
set `AGENTHUB_RUN_NATS_INTEGRATION=1` to verify request, Rust compaction,
semantic summarization, coverage ranges, and final summary publication.

Summary write-back stores `covered_sequence_start/end`, generation time, and
source event ID. Duplicate events, older coverage, and older generation times
are rejected under a per-session lock. Native Chinese-provider tokenizers can
be registered in code or loaded from local `tokenizer.json` paths through the
`AGENTHUB_TOKENIZER_<PROVIDER>_PATH` variables; the fallback remains explicitly
reported as a multilingual estimator. Route/agent cache invalidation keeps a
local L1 cache while Redis version counters and Pub/Sub invalidate peer workers.
Set `AGENTHUB_RUN_REDIS_INTEGRATION=1` to run the real two-client peer
invalidation test against `REDIS_URL`/`REDIS_ADDR`.

## 5. Current assessment

The memory subsystem is now an operational multi-layer pipeline, but it is not
yet a complete ContextOS implementation.

Strengths:

- Tenant-scoped durable memory and summaries.
- Prompt projection is bounded and retrieval-first.
- Structural compaction and semantic summarization form an event-driven loop.
- Idempotent online summary consumption and explicit cache invalidation.
- Cost and compression visibility exists at both online and offline layers.

Remaining gaps:

1. Native tokenizers are not yet available for every Chinese provider. The
   fallback is safe for limits but cannot guarantee exact billing parity. The
   `cn_tokenizer_precision` bench gate measures estimator error against a
   configured reference tokenizer (`AGENTHUB_CN_TOKENIZER_PROVIDER` /
   `AGENTHUB_TOKENIZER_<PROVIDER>_PATH`) and reports an honest SKIP until one
   is provisioned.
2. L2 now has a unified embedding version, retention policy, provenance
   model, and deletion propagation (`app/services/memory/l2_vector.py`,
   integrated into `SemanticMemoryStore`). Remaining: swap the default local
   hashing embedder for a model-based one once a remote embedding endpoint is
   configured, and offline retrieval recall is enforced by the
   `knowledge_retrieval_recall` gate (recall@3 > 85% on the internal eval set
   in `benchmarks/gates.py`).
3. L3 global summaries do not yet distinguish durable facts, preferences,
   hypotheses, and expired information.
4. Summary quality is a heuristic operational signal, not an evaluator-model
   or human-rated regression score.
5. In-process caches and the online consumer are worker-local. Multi-replica
   deployment needs Redis-backed versions and a single durable consumer group.
6. Summary write-back is last-write-wins. Concurrent summaries need sequence
   or covered-range conflict checks.

## 6. Next improvements

### Short term

- Add Qwen, DeepSeek, Doubao, GLM, and Claude native tokenizer adapters
  (load path exists via `AGENTHUB_TOKENIZER_<PROVIDER>_PATH`; parity enforced
  by the `cn_tokenizer_precision` gate once a reference is provisioned).
- Include covered message sequence ranges in summary state and reject stale
  write-back events.
- Add integration tests with NATS, Rust core, summarization-service, and the
  online consumer in Docker Compose.
- Replace the heuristic history metric with exact before/after counters on all
  streaming and non-streaming paths.

### Medium term

- Introduce a memory record schema containing tenant, session, source,
  provenance, confidence, sensitivity, TTL, embedding version, and tombstone.
  (L2 lifecycle — embedding version, TTL, tombstone, deletion propagation —
  is implemented in `app/services/memory/l2_vector.py`; sensitivity and
  team-scoped multi-tenant write paths remain.)
- Split L3 into durable facts, user/team preferences, decisions, and expired
  candidates; refresh incrementally instead of regenerating all summaries.
- Move cache versions and summary checkpoints to Redis/PostgreSQL for replicas.

### Long term

- Implement AutoDream consolidation with contradiction detection and approval
  rules for high-risk enterprise memory.
- Add knowledge-graph memory with temporal edges and source citations.
- Evaluate summaries against retained facts, unresolved tasks, and answer
  quality using offline datasets and canary traffic.
