# AgentHub Reconstruction Roadmap

> Status: accepted
> Owner: architecture maintainers
> Last reviewed: 2026-08-26
> Scope: code-debt reduction, architecture convergence, documentation-to-code alignment, delivery sequencing
> Replaces: `docs/roadmaps/optimization-roadmap.md` (superseded)

## 1. Why this roadmap exists

AgentHub finished a large Mission-centric migration wave (ADRs 0001-0103) and a
desktop single-entry foundation commit. Three kinds of debt now block the next
product step:

1. **Oversized hot modules** — `agent_service.py` (2650 lines, 58 functions),
   `websocket.py` (2062 lines), `page.tsx` (1641 lines) mix orchestration with
   formatting and slow every feature change.
2. **Documentation-to-code drift** — the public capability table in
   `docs/zh/guide/what-is-agenthub.md` claims "Rust P95 < 80ms", "4-layer
   ContextOS", "MCP STDIO+SSE" as shipped facts, while `memory.md` and
   `performance.md` describe those as partial work or target values. This
   dissolves trust exactly when we want to build an evaluation-led brand.
3. **Suspended architecture convergence** — legacy LangGraph/DAG remain on the
   production request path, and outbound A2A production wiring is disabled
   until the Gateway cutover is atomic. Directional work is parked.

This roadmap prioritizes the debt that changes project direction first. It
follows the repository rule: *do not expand feature surface until the core loop
stays smaller, replayable, observable, and cheap to run repeatedly.*

## 2. Technical debt inventory (verified against code on 2026-08-26)

| ID | Debt | Evidence | Impact | Direction |
|---|---|---|---|---|
| D1 | `agent_service.py` is a god module: prompt building, memory context, tool loop, persistence, degradation | `app/services/agent_service.py` (2650 lines / 58 defs / 138 KB) | Every agent feature change risks regressions; hard to test in isolation | Split into `prompt_builder`, `memory_context`, `tool_loop`, `persistence_adapter` use-case boundaries |
| D2 | `websocket.py` owns lifecycle + control + preview + collaboration | `app/api/websocket.py` (2062 lines) | WebSocket state is partially in-process; multi-instance replay is limited | Split into lanes; externalize state behind the existing `websocket_state`/`websocket_lifecycle` modules |
| D3 | `page.tsx` is an orchestration shell, not a composition shell | `frontend/app/page.tsx` (1641 lines) | UI state and transport state are entangled | Move IM orchestration into hooks/stores (`lib/*`, `components/chat/*`) |
| D4 | Capability table over-claims unverified performance | [what-is-agenthub.md](../zh/guide/what-is-agenthub.md) "P95 < 80ms", "ContextOS ✅"; [performance.md](../zh/advanced/performance.md) lists these as target values without benchmarks | Public claims do not match measured reality | Add benchmark gates; reword table to "target/prototype" wording with links to tests |
| D5 | Legacy LangGraph/DAG still on production request path; new work banned but old path active | `app/services/langgraph_workflow.py`, `app/compat/legacy_tasks.py` | Two sources of execution truth; maintenance tax on both | Deprecate legacy route selection; migrate remaining flows to Mission/WorkUnit |
| D6 | Outbound A2A production wiring disabled | [runner.md](../architecture/components/runner.md) (composed and gated, wiring off) | Feature exists but cannot run in production | Atomic cutover per ADR-0053; remove Gateway direct dispatch in same change |
| D7 | Performance claims lack benchmark gates | No CI benchmark; `app/services/performance_monitor.py` data unused for gating | Regressions invisible until users report them | Add `benchmarks/` with latency/token/P95 gates on CI |
| D8 | Tests provisioned outside `tests/` | `app/db/test_sqlite_pool.py`, `app/api/test_websocket_*.py` under `app/` | Discovery by scripts/packaging may miss them; inconsistent placement | Normalize structural tests under `tests/`; keep focused unit tests beside modules where they are packaging-neutral |
| D9 | Token economy partial: native CN tokenizers, distributed cache versions, L2/L3 memory incomplete | [memory.md](../architecture/components/memory.md) | Cost visibility is good; exact billing parity and recall-quality are not yet proven | Complete L2 lifecycle + CN tokenizers; add offline eval set |
| D10 | Document map incomplete: `docs/index.md` omits roadmaps/governance/operations/development links | [docs/index.md](../index.md) | New contributors cannot find the governance and navigation tree | Fix map; define "start here" for each audience |
| D11 | Dead internal reference: `optimization-roadmap.md` claims replacement by `docs/internal/roadmaps/12-week-refactor.md` which does not exist | [optimization-roadmap.md](./optimization-roadmap.md) | Dangling superseded pointer; readers cannot find the current plan | This new roadmap becomes the replacement; mark old one superseded |

## 3. Target module layout

Principle: request-scoped execution composes small, testable units; durable
truth stays in Mission Control aggregates.

```
app/
  services/agent/            # D1 split (new)
    prompt_builder.py        #   prompt assembly from history/memory/context
    memory_context.py        #   working/episodic/semantic/procedural projection
    tool_loop.py             #   bounded tool-call loop + checkpoint events
    persistence_adapter.py   #   session/message/artifact persistence ports
    agent_router.py          #   agent selection, model racing, degradation
  api/websocket/             # D2 split (new)
    lifecycle.py             #   connection lifecycle, reconnect/recovery
    control.py               #   command events (cancel, plan confirm)
    preview.py               #   compact task preview events
    collaboration.py         #   presence + shared DAG stream
    state.py                 #   externalized session state adapter (Redis/NATS)
frontend/app/page.tsx        # D3 shrink to composition shell
frontend/lib/agent/          #   IM orchestration hooks + stores
benchmarks/                  # D7 (new) latency/token/P95 gates
```

## 4. Phased execution plan

### Phase R1: Align documentation to code  (weeks 0-2)

| Task | Debt | Acceptance criteria |
|---|---|---|
| Rewrite capability table in `what-is-agenthub.md` with "implemented / prototype / target" wording + links to tests | D4 | Every public claim links to a test or implementation file |
| Fix `docs/index.md` map; add roadmaps/governance/operations/development entries | D10 | Each directory linked with one-line audience description |
| Mark `optimization-roadmap.md` superseded, pointing here | D11 | No dangling replacement pointers in `docs/` |
| Add benchmark scaffolding + first gates (P95 API, token compaction ratio) | D7 | Failed regression blocks CI for missing threshold |

**Stop condition:** all public claims in `docs/zh/guide/what-is-agenthub.md`
pass the documentation-standard review (claim → test link), or the claim is
demoted to target/prototype.

### Phase R2: Converge execution paths  (weeks 2-8)

> Status: implemented (2026-08-26). Runbook:
> `docs/operations/r2-a2a-langgraph-cutover.md`.

| Task | Debt | Acceptance criteria |
|---|---|---|
| Remove Gateway direct dispatch; enable Runner-supervised outbound A2A (ADR-0053 cutover) | D6 | `A2A_DISPATCH_MODE=runner` disables Gateway forwarding (tested); Runner `build_runner_runtime` composes outbound candidate with strict peer manifest; no double dispatch |
| Deprecate legacy LangGraph route selection; migrate remaining chat/DAG flows to Mission/WorkUnit paths | D5 | `route_message` bypasses LangGraph by default; `AGENTHUB_ENABLE_LEGACY_LANGGRAPH` flag keeps migration window; no new writes to legacy task tables by default |
| Normalize test placement | D8 | `app/` tests moved under `tests/` (`tests/persistence/`, `tests/api/`); `pytest tests/` collects 600+ tests green |

**Stop condition:** no new business state written through legacy Task/DAG unless
a documented compat shim retains it, and the A2A outbound gate is the only
production dispatch path.

### Phase R3: Split hot modules  (weeks 8-18)

| Task | Debt | Acceptance criteria |
|---|---|---|
| Split `agent_service.py` into the `services/agent/` layout; keep public call signatures stable behind facade while consumers migrate | D1 | Each new module < 500 lines; per-module unit tests; existing API/ws tests green |
| Split `websocket.py` into lanes; externalize session state behind `state.py` adapter | D2 | WebSocket reconnect across two backend replicas preserves session; lane tests independent |
| Thin `page.tsx` to composition; move orchestration to `lib/agent` hooks/stores | D3 | `page.tsx` < 400 lines; e2e `frontend/e2e/*` green |

**Stop condition:** all three modules below size gates with per-module tests;
no new logic added to the legacy files.

### Phase R4: Prove performance and quality (weeks 18-26)

| Task | Debt | Acceptance criteria |
|---|---|---|
| Complete memory L2 vector lifecycle + CN provider tokenizers; add offline eval set | D9 | Recall > 85% on internal set; token estimation error < 5% for listed CN providers |
| Extend benchmark suite to streaming TTFT and memory/retrieval | D7, D9 | P95 gates enforced in CI for the claims in `performance.md` |
| Apply code-quality standard (see `docs/governance/code-quality-standard.md`) to all new PRs | all | CI enforces size/complexity/coverage gates |

**Stop condition:** CI benchmark gates exist for every near-term claim in
`what-is-agenthub.md` and `performance.md`, or the claim is removed.

### Phase R5: Product surface  (Q3-Q4 2027)

Only after R1-R4 gates are green:

- Desktop single-entry GA on Windows, then macOS/Linux (ADR-0103 implementation
  gates 3-6).
- Workflow template marketplace, scoped API tokens, TypeScript/Python SDKs.
- Stronger verifier evaluators beyond `artifact-set.v1` (test/build/security).
- Security baseline whitepaper (sandbox, credential store, signed agent cards).

**Stop condition:** no marketplace/SDK work until R3 stop conditions hold.

## 5. Cross-cutting rules

- Every ADR remains the source of truth for boundaries; this roadmap does not
  change ownership, it repays drift.
- Roadmap items are re-prioritized only with the architecture maintainers;
  stop conditions are hard gates, not advisory targets.
- All public capability claims must follow the documentation standard: link to
  tests or implementation, or be demoted to target/prototype.