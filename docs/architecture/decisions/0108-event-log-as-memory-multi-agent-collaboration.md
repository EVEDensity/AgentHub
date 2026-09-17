# ADR-0108: Event Log as Memory for Multi-Agent Collaboration

> Status: accepted
> Owner: architecture maintainers
> Date: 2026-09-01
> Scope: app/services/memory/, app/cli/, app/api/, mission event store, docs/

## Context

AgentHub's product north star is a chat-shaped multi-agent collaboration
system: humans and agents share one session, users wake an agent with
`@agent`, and the agent executes the task end to end. A 2026-09-01 research
pass benchmarked this vision against Block's Buzz and the mainstream memory
solutions shipped by AutoGen, LangGraph, CrewAI, Mem0, Letta/MemGPT,
Zep/Graphiti, OpenHands, and AnythingLLM
(`docs/roadmaps/multi-agent-memory-architecture.md`).

Three facts drive the decision:

1. **The durable event stream already exists.** Mission Control persists an
   immutable, transactional audit trail of Mission / Contract / WorkUnit /
   Artifact / Evidence / Decision events. This is structurally the same
   primitive Buzz uses as memory: one append-only event log plus indexes —
   "the relay is the workspace, the event log is the memory."
2. **ADR-0107 already removed the heavy memory layers** (L2 vector, L3
   global, semantic, procedural) and committed to a token-lean L0/L1 +
   flat-project-facts shape. Reintroducing a third-party memory plane would
   reverse that decision without a new consumer.
3. **The differentiating capability is evidence, not recall.** AgentHub's
   verifier separation (ADR-0004/0059/0060) means every remembered task
   carries proof. A memory architecture should expose that proof
   (Buzz-style "receipts": answers that cite the missions, artifacts, and
   verify verdicts they came from), not just fuzzy similarity.

## Decision

**The Mission/Evidence event stream is the memory backbone for multi-agent
collaboration. No third-party memory service (Mem0, Letta, Zep/Graphiti) is
introduced.**

Concretely:

1. **Session = event stream.** A collaboration session is an append-only log
   of member events (messages, mentions, mission references, verdict
   summaries). The log is L0 working memory and the only replayable source
   of truth; it is never duplicated into a parallel store.
2. **L1 stays incremental.** Session summaries remain change-only folds
   (ADR-0107: `build_compact_context`,
   `SessionMemoryManager.update_session_summary`).
3. **Project facts stay flat.** `.agenthub/memory.md` with key-supersede
   semantics; no graph, no embedding store.
4. **Retrieval = receipts over the event log.** Cross-session recall is
   served by keyword/FTS views over Mission and Evidence records
   (`agenthub search`), each hit returned with mission id, artifact
   references, and the verifier verdict. Answers cite evidence; they do not
   free-associate.
5. **Mention is the trigger, Mission is the unit of work.** `@agent`
   mentions are resolved against the session member model and routed to
   Mission creation; execution flows through the existing Runner/Harness/
   Verifier chain. No new execution runtime is created for chat.
6. **Agents are session members, first-class.** Internal agents register a
   capability card with permission tiers (suggest/edit/auto); external
   agents join through the existing A2A bidirectional-signature trust.
   Membership visibility is the access gate, following Buzz's
   channel-membership model.
7. **Vector retrieval stays opt-in.** An embedding path may be added only
   behind an explicit opt-in once a retrieval use case with acceptance
   criteria exists (unchanged from ADR-0107).

## Consequences

* Cross-session memory work concentrates on event-log views (FTS/keyword)
  and the receipts contract, not on memory pipelines.
* The chat rework must target Mission + v1 API; nothing new may be built
  on the orchestrator runtime (reinforces ADR-0107).
* Storage footprint stays flat: SQLite/Postgres tables already owned by
  Mission Control plus bounded summary files.
* Future multi-node fan-out (if ever needed) is an event-log distribution
  problem, addressable without changing the memory contract.
* This ADR sets direction, not implementation: each work item lands only
  with its own tests and acceptance criteria per the roadmap
  (`docs/roadmaps/multi-agent-memory-architecture.md` §8).

## Alternatives considered

* **Adopt Mem0 / Letta as the memory plane** — rejected: reintroduces the
  heavy external dependency ADR-0107 removed, duplicates the existing audit
  event stream, and none of their benchmark wins (LoCoMo-style recall)
  address AgentHub's differentiator — verifiable, evidence-cited execution.
* **Adopt Zep/Graphiti temporal knowledge graph** — rejected: already
  deferred by ADR-0106/0107; no cross-mission entity-query consumer exists
  on the roadmap.
* **Full Buzz/Nostr relay rewrite** — rejected: the relay's value for
  AgentHub is its memory *pattern* (event log + index + receipts), not its
  wire protocol. Reusing the pattern on the existing Mission event store
  captures the benefit at a fraction of the cost.

## Verification

* `agenthub search` receipts slice returns mission links and verifier
  verdicts (planned; acceptance criteria in the roadmap).
* Existing memory tests
  (`app/services/memory/test_session_incremental.py`,
  `tests/cli/`) continue to pass unchanged.
* `python benchmarks/gates.py check-docs` and `check-links` pass with the
  new architecture documents wired into navigation.

## Supersedes

None. Extends ADR-0107 (storage shape) and ADR-0001 (Mission Control as
source of truth); compatible with ADR-0106 (knowledge graph stays out).
