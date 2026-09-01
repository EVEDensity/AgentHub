# AgentHub Roadmap Checklist

> Tracking sheet for **deviations and pending items** surfaced by the 2026-09-01
> P0/P1 memory-slice delivery.  Each row is ticked when complete.  **Source of
> truth** for architectural direction remains the accepted roadmaps
> (`reconstruction-roadmap.md`, `multi-agent-memory-architecture.md`) and
> ADRs 0001–0108 — this checklist only captures what that body left open or
> what we now know regressed after the 2026-09-01 delivery wave.
>
> Last audit: 2026-09-01

***

## Tier 0 — Blocking Debt (do first; everything else stacks on these)

### T0-1 Split `mission_service.py` (new 3,165-line god module)

| #     | Item                                                                                                                      | Acceptance                                           | Status | Evidence |
| ----- | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ------ | -------- |
| T0-1a | Extract **lifecycle** module: `create_mission`, `start_mission`, `complete_mission`, `fail_mission`, `cancel_mission`     | < 250 lines under `services/mission/lifecycle.py`    | ☐      | <br />   |
| T0-1b | Extract **objective** module: objective validation, mission objective → work unit derivation, contract builder            | < 250 lines under `services/mission/objective.py`    | ☐      | <br />   |
| T0-1c | Extract **participants** module: actor/participant resolution, agent binding lookup, default participant fallback         | < 200 lines under `services/mission/participants.py` | ☐      | <br />   |
| T0-1d | Extract **events** module: mission event ledger append, event stream projection, SSE fan-out helpers                      | < 200 lines under `services/mission/events.py`       | ☐      | <br />   |
| T0-1e | Extract **execution** module: `create_work_unit`, `update_work_unit`, runner claim coordination, execution status machine | < 300 lines under `services/mission/execution.py`    | ☐      | <br />   |
| T0-1f | Reduce `mission_service.py` facade to < 200 lines (imports + thin pass-throughs) + full suite keeps 985 passed            | < 200 lines                                          | ☐      | <br />   |

### T0-2 Split v1 `missions.py` (1,421 lines)

| #     | Item                                                                      | Acceptance                                           | Status | Evidence |
| ----- | ------------------------------------------------------------------------- | ---------------------------------------------------- | ------ | -------- |
| T0-2a | Extract **crud** sub-router: list/get/create/fork operations              | < 300 lines under `api/v1/missions/crud.py`          | ☐      | <br />   |
| T0-2b | Extract **events\_stream** sub-router: SSE endpoint, event filter, replay | < 300 lines under `api/v1/missions/events_stream.py` | ☐      | <br />   |
| T0-2c | Extract **contracts** + **artifacts** sub-routers                         | each < 200 lines                                     | ☐      | <br />   |
| T0-2d | `missions.py` becomes router assembly < 100 lines                         | < 100 lines                                          | ☐      | <br />   |

### T0-3 Delete legacy `websocket_processor.py` (1,479 lines — dead production path)

| #     | Item                                                                                                            | Acceptance             | Status | Evidence |
| ----- | --------------------------------------------------------------------------------------------------------------- | ---------------------- | ------ | -------- |
| T0-3a | Search: no code imports `websocket_processor` outside compat shim                                               | zero imports           | ☐      | <br />   |
| T0-3b | Delete file + any stray `app/api/websocket.py` leftover functions                                               | file gone              | ☐      | <br />   |
| T0-3c | `route_message` in runner/controller.py no longer branches to legacy                                            | only v1 path reachable | ☐      | <br />   |
| T0-3d | Legacy WebSocket fallback kept only behind `AGENTHUB_ENABLE_LEGACY_LANGGRAPH=true` env flag (R2 stop condition) | gated                  | ☐      | <br />   |

### T0-4 Feature-flag hardening

| #     | Item                                                                            | Acceptance    | Status | Evidence |
| ----- | ------------------------------------------------------------------------------- | ------------- | ------ | -------- |
| T0-4a | `AGENTHUB_ENABLE_LEGACY_LANGGRAPH` defaults to false                            | default false | ☐      | <br />   |
| T0-4b | `USE_MISSION=true` hardcoded in page.tsx (remove toggle comment once T0-3 done) | hardcoded     | ☐      | <br />   |

***

## Tier 1 — Multi-Agent Collaboration P2/P3

### T1-1 Subscribe/Rule Trigger (confirm-first-execute)

| #     | Item                                                                                                        | Acceptance                              | Status | Evidence |
| ----- | ----------------------------------------------------------------------------------------------------------- | --------------------------------------- | ------ | -------- |
| T1-1a | `AGENT_RULES.yaml` schema: pattern → agent\_id → action\_type (ask / auto)                                  | schema doc + example                    | ☐      | <br />   |
| T1-1b | Rule engine: session event → pattern match → emit confirmation event                                        | unit tests cover hit/miss/no-permission | ☐      | <br />   |
| T1-1c | User confirmation gate: only explicit `ask` action surfaces confirm event; `auto` still gated by member ACL | confirm-first                           | ☐      | <br />   |
| T1-1d | Confirmation accepted → Mission created + `mission.created`回写会话流                                            | end-to-end path                         | ☐      | <br />   |

### T1-2 Receipts view inside chat session

| #     | Item                                                                                          | Acceptance         | Status | Evidence |
| ----- | --------------------------------------------------------------------------------------------- | ------------------ | ------ | -------- |
| T1-2a | `@archivist <query>` in Web chat → Agent calls `memory_recall` → replies with receipts result | end-to-end in chat | ☐      | <br />   |
| T1-2b | Each answer bullet carries mission\_id link + VERIFY verdict + artifact summary               | evidence-linked    | ☐      | <br />   |
| T1-2c | CLI `agenthub search` → same backend used by session receipts (zero duplication)              | same impl          | ☐      | <br />   |

### T1-3 Session event stream model

| #     | Item                                                                                                              | Acceptance                                | Status | Evidence |
| ----- | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------- | ------ | -------- |
| T1-3a | Domain model: `message.created`, `mention.detected`, `mission.created`, `mission.completed`, `member.joined/left` | models + migration                        | ☐      | <br />   |
| T1-3b | SSE subscription endpoint for session events (parallel to mission events)                                         | `GET /api/v1/sessions/{id}/events/stream` | ☐      | <br />   |
| T1-3c | Mission → session milestone bridge: `mission.completed` emits condensed summary event (not full event dump)       | summary-only                              | ☐      | <br />   |

### T1-4 Unified cross-domain search (session + mission)

| #     | Item                                                           | Acceptance                                | Status | Evidence |
| ----- | -------------------------------------------------------------- | ----------------------------------------- | ------ | -------- |
| T1-4a | `agenthub search` extends to session messages                  | CLI flag `--scope {mission,session,both}` | ☐      | <br />   |
| T1-4b | FTS index on both tables (no vector; default FTS per ADR-0108) | FTS only                                  | ☐      | <br />   |

***

## Tier 2 — Runner/Harness/Execution Hardening

### T2-1 Chat Mission end-to-end verification

| #     | Item                                                                                                                                          | Acceptance   | Status | Evidence |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------------ | ------ | -------- |
| T2-1a | Integration test: `POST /api/v1/chat/mission` (no @mention) → 202 → SSE `work_unit.started` within derived tick                               | green CI     | ☐      | <br />   |
| T2-1b | Integration test: with @mention → resolved participants injected → SSE `work_unit.started` for named agent                                    | green CI     | ☐      | <br />   |
| T2-1c | Integration test: default participant fallback when catalog empty                                                                             | green CI     | ☐      | <br />   |
| T2-1d | Clean up `_inline_derive_work_units` in `chat_mission.py` once desktop runner loop reliably picks up chat Missions (T0-3 done → no more race) | rely on loop | ☐      | <br />   |

### T2-2 A2A outbound production cutover (ADR-0053)

| #     | Item                                                                                | Acceptance      | Status | Evidence |
| ----- | ----------------------------------------------------------------------------------- | --------------- | ------ | -------- |
| T2-2a | `A2A_DISPATCH_MODE=runner` default in all deployment profiles                       | default runner  | ☐      | <br />   |
| T2-2b | Gateway direct dispatch disabled behind hard remove (not just env flag)             | remove          | ☐      | <br />   |
| T2-2c | Runner `build_runner_runtime` composes outbound candidate with strict peer manifest | strict manifest | ☐      | <br />   |

### T2-3 Runner concurrency + claim fencing stress test

| #     | Item                                                                                                    | Acceptance         | Status | Evidence |
| ----- | ------------------------------------------------------------------------------------------------------- | ------------------ | ------ | -------- |
| T2-3a | Stress test: N runners claim same Mission simultaneously → 1 winner, N-1 rejected                       | deterministically  | ☐      | <br />   |
| T2-3b | Lease expiry + heartbeat gap test: runner dies mid-execution → next runner picks up within fence window | fence passes       | ☐      | <br />   |
| T2-3c | A2A inbound claim fencing: multiple inbound peers race for same work unit                               | ADR-0043 compliant | ☐      | <br />   |

***

## Tier 3 — Perf/Docs/Quality Gates

### T3-1 Benchmark gates wired into CI

| #     | Item                                                                      | Acceptance  | Status          | Evidence       |
| ----- | ------------------------------------------------------------------------- | ----------- | --------------- | -------------- |
| T3-1a | streaming\_ttft P95 < 2s gate blocks PR                                   | CI fail     | ✅ already wired | docs-gates job |
| T3-1b | cn\_tokenizer\_precision gate (SKIP if no native CN tokenizer configured) | honest SKIP | ✅ already wired | docs-gates job |
| T3-1c | Add first P0/P1 mission creation latency gate (< 500ms P95 on mock)       | green gate  | ☐               | <br />         |

### T3-2 Documentation ↔ code alignment

| #     | Item                                                                                                                                 | Acceptance                    | Status | Evidence |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------- | ------ | -------- |
| T3-2a | `multi-agent-collaboration.md` §3 table: Web 聊天 🔵 → ✅ 已交付                                                                           | status bumped                 | ☐      | <br />   |
| T3-2b | `multi-agent-collaboration.md` §3 table: mention 触发 🔵 → ✅ 已交付                                                                       | status bumped                 | ☐      | <br />   |
| T3-2c | `multi-agent-collaboration.md` §10 table: MCP 记忆工具 🔵/⚪ → ✅ 已交付 + 移至 P1                                                              | status bumped + reprioritized | ☐      | <br />   |
| T3-2d | Update `docs/roadmaps/multi-agent-memory-architecture.md` §8 mid-2026-09-01 slice entries (some marked ✅ in prose, not formal table) | sync                          | ☐      | <br />   |
| T3-2e | `memory.md` remove L2 vector / L3 / consolidation remnants (ADR-0107 consequence)                                                    | document consistent           | ☐      | <br />   |

### T3-3 Code-quality ratchet refresh (after R3 + memory slices)

| #     | Item                                                                                                                  | Acceptance    | Status | Evidence |
| ----- | --------------------------------------------------------------------------------------------------------------------- | ------------- | ------ | -------- |
| T3-3a | Refresh code-quality-standard.md size gates against new baseline (mission\_service split will shift mean module size) | gates updated | ☐      | <br />   |
| T3-3b | Zero **new** modules > 800 lines added since 2026-09-01 (this session's exception list may only shrink)               | ratchet       | ☐      | <br />   |

***

## Tier 4 — Product Surface (Q3–Q4 2027, after T0–T3 all green)

| #    | Item                                                                       | Depends       | Status                                |
| ---- | -------------------------------------------------------------------------- | ------------- | ------------------------------------- |
| T4-1 | Desktop single-entry GA (Windows installer)                                | T0 + T2       | 🔵 architecture ready (ADR-0080–0103) |
| T4-2 | Workflow template marketplace                                              | T2-3          | ⚪                                     |
| T4-3 | TS/Python SDKs                                                             | v1 API stable | ⚪                                     |
| T4-4 | Verifier evaluator extensions (test/build/security beyond artifact-set.v1) | T2-3          | 🔵 artifact-set.v1 exists             |

***

## Reference: source-of-truth docs & ADRs

| Doc                                                | Purpose                                                |
| -------------------------------------------------- | ------------------------------------------------------ |
| `docs/roadmaps/reconstruction-roadmap.md`          | R1–R4 debt reduction; R5 product surface               |
| `docs/roadmaps/multi-agent-memory-architecture.md` | P0/P1/P2/P3 memory & collaboration slices              |
| `docs/architecture/multi-agent-collaboration.md`   | Architecture §3 status table + §10 iteration priority  |
| `docs/architecture/decisions/0107-*.md`            | Memory slimming; L2/L3 disabled; web chat decommission |
| `docs/architecture/decisions/0108-*.md`            | Event log as memory; Buzz model adopted                |
| `docs/operations/r2-a2a-langgraph-cutover.md`      | R2 runbook; A2A + LangGraph cutover                    |

