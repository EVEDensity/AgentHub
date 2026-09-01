# ADR-0106: L3 Knowledge Graph Stays on the R5 Timeline

> Status: accepted  
> Owner: memory & retrieval maintainers  
> Date: 2026-09-01  
> Scope: app/services/memory/, benchmarks/, docs/roadmaps

## Context

The L3 knowledge graph is scheduled for the 2027 R5 line
(`docs/roadmaps/` R5 product surface). The I-6 tool-surface push
(north-star M3 remainder) asked whether L3 should be pulled forward to
ride the current development momentum.

Current retrieval reality (verified 2026-09-01):

* **L2 is the only retrieval layer in production**:
  `app/services/memory/l2_vector.py` — file-backed JSON vector store,
  tenant-scoped, pluggable embedder (bundled `LocalHashEmbedder`,
  deterministic, dependency-free).
* **L2 has measured gates on CI**:
  `knowledge_retrieval_recall` (offline eval set, recall > 85%) and
  `knowledge_retrieval_p95` (p95 < 80ms) run in the `docs-gates` CI job.
* **No L3 code exists** — no entity/relation schema, no graph store, no
  traversal query path. The memory component doc lists L3 as a gap.

## Decision

**Do not pull L3 forward. It stays on the R5 timeline.**

Rationale:

1. **No consumer would use it yet.** L3's value is cross-mission /
   cross-session entity linking ("which decisions touched this
   module?"). The current CLI/chat surface (I-6c) chains and compacts
   mission context; there is no feature on the near roadmap that issues
   graph-shaped queries. Building the store first would be
   infrastructure waiting for a consumer.
2. **L2 gates have not saturated.** Recall and latency are passing with
   the local hash embedder, but real semantic recall with a model-based
   embedder is unproven — the honest next increment in retrieval quality
   is an embedder upgrade behind the existing `EmbeddingProvider`
   protocol, not a new tier.
3. **The R5 dependency is real, not arbitrary.** The workflow-template
   marketplace and scoped API tokens (R5 items) are the first natural
   L3 consumers; they define the entity/relation shapes L3 must
   support. Building L3 before its consumers risks the wrong schema.
4. **M3 bandwidth is committed.** The remaining north-star items (npm
   release, public scores, PR review, tool tiers) close the loop that
   L3 does not affect.

**What may change this decision** (record here when it happens):

* A concrete product need for cross-mission entity queries lands on the
  roadmap with acceptance criteria;
* L2 recall gates start failing at the current threshold after an
  embedder upgrade — i.e. vector retrieval has demonstrably plateaued;
* R5 is pulled forward wholesale.

## Consequences

* R5 planning keeps L3 as originally scoped; no schema commitments are
  made now.
* Retrieval improvements in the near term go to L2 (embedder swap,
  retention tuning) behind the existing protocol.
* This ADR is the honest answer to "should L3 be pulled forward?" —
  evaluated, decided, revisit triggers recorded.

## Alternatives considered

* **Pull L3 forward now** — rejected: no consumer, no saturated L2, and
  it would displace the M3 close-out work that actually gates the
  public release.
* **Prototype L3 behind a flag** — rejected: an unreferenced prototype
  still costs schema review and CI surface, and invites accidental
  dependency before consumers exist.

## Verification

* L2 gates remain the retrieval contract: `python benchmarks/gates.py
  run --name knowledge_retrieval_recall` and `--name
  knowledge_retrieval_p95` on CI.
* Absence of L3 remains intentional: no `l3` module under
  `app/services/memory/`, and this ADR is the recorded reason.

## Supersedes

None.
