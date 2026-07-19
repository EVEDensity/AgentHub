# AgentHub Optimization Roadmap

## 0. Current Position

AgentHub is already past the "chat wrapper" stage. The current codebase has a usable multi-agent runtime, DAG tasking, WebSocket collaboration, memory/session persistence, and a multi-language service backbone.

What is now true in the repository:

- `frontend/app/page.tsx` is already thinner than before and now delegates message recovery, WebSocket URL building, and DAG state to helpers.
- `frontend/hooks/useSessionWebSocket.ts` and `frontend/hooks/useSessionRecovery.ts` now carry the connection lifecycle and reconnect / restore logic out of the page shell.
- `frontend/__tests__/components/chat/taskPreviewReplay.test.tsx` and `frontend/__tests__/components/chat/dagReplay.test.tsx` cover duplicate preview events, reconnect replay, and session switching.
- `frontend/components/admin/PermissionModule.tsx` now replaces the stale `权限` placeholder with a real permission-rule surface backed by `/api/admin/permissions/rules`.
- The admin shell now has explicit mobile, compact-laptop, and wide-desktop behavior; compact layouts collapse the sidebar and remove controls that cannot operate at that width.
- Permission rules now support validated create/edit/toggle/delete flows, request retry feedback, and focused interaction tests.
- Compact admin sidebars now expand to their persisted width without being clipped by the responsive grid.
- Recovery now advances the replay cursor for all identified events, merges final messages over streaming placeholders, and restores persisted DAG progress without overwriting newer live updates.
- Prompt/token control has started through `app/services/context_compaction.py`, `app/services/conversation_history.py`, `app/services/orchestrator_preprocessor.py`, `app/services/task_decomposer.py`, and `app/services/result_synthesizer.py`.
- `app/api/websocket_message_flow.py` and `app/api/websocket.py` now share compact task preview construction.
- The repo already carries the architecture needed for enterprise expansion, but it still has a few oversized hot modules.

The next step is not to add more features first. The next step is to make the core flow smaller, more explicit, and cheaper to run.

## 1. Scorecard

| Dimension | Score | Why it lost points |
|---|---:|---|
| Architecture | 82 | Strong platform shape, but a few modules still hold transport, orchestration, persistence, and formatting together. |
| Code quality | 72 | Hot-path functions are still too large and a lot of logic is repeated in slightly different forms. |
| Performance / concurrency | 78 | Caching exists, but prompt assembly, history replay, and summary synthesis still do too much repeated work. |
| Business completeness | 84 | Multi-agent, DAG, memory, IAM, observability, and deploy are present; editor, marketplace, SDK, and token management are still missing. |
| Operations / deployment | 79 | Compose and monitoring are present, but service startup, retries, and runtime health are not uniform enough. |
| Security | 71 | IAM and tool gating exist, but broad exception handling and in-process state remain weak points. |
| Extensibility | 76 | The stack is right, but the extension points are not yet shaped as clean platform APIs. |

Overall: **77/100**.

## 2. Problem List

### 2.1 Architecture coupling

| Problem | Scope | Risk | Root cause |
|---|---|---:|---|
| `app/services/agent_service.py` does too much | Prompt, history, memory, tool loop, persistence, synthesis | High | The runtime was expanded without a hard boundary between orchestration and formatting. |
| `app/api/websocket.py` still carries lifecycle + control + task preview + collaboration | All IM traffic and replay flows | High | The WebSocket layer still owns more than transport concerns. |
| `frontend/app/page.tsx` is still a large orchestration shell | Main IM UI, reconnect, preview, recovery, draft state | High | UI state and transport state were not separated early enough. |
| DAG preview generation is duplicated | `websocket.py`, `websocket_message_flow.py` | Medium | Item shaping lived in two places before a shared helper existed. |

### 2.2 Code quality

| Problem | Scope | Risk | Root cause |
|---|---|---:|---|
| Repeated string assembly for prompt payloads | Every LLM call | High | No shared context-compaction layer existed. |
| Broad `except Exception` blocks | Hot paths and fallback paths | High | Fail-open behavior was used too often to keep the UX moving. |
| Overloaded helpers | History, preprocessor, synthesis, preview | Medium | Helpers were kept as convenience wrappers instead of becoming true domain units. |

### 2.3 Performance and token use

| Problem | Scope | Risk | Root cause |
|---|---|---:|---|
| Conversation history was too long | Frequent turns and tool loops | High | History replay was treated as raw transcript replay. |
| Memory context was too large | Long sessions | High | Current-session memory and global summary were both injected too freely. |
| Preprocess output was too verbose | Orchestrator prompt | High | Structured analysis was formatted as markdown instead of compact prompt data. |
| Result synthesis repeated long node outputs | Multi-agent DAG runs | Medium | Each result was copied into the final synthesis with too much text. |

### 2.4 Business flow gaps

| Problem | Scope | Risk | Root cause |
|---|---|---:|---|
| Message recovery and DAG replay still need hardening | Refresh, reconnect, cross-tab continuity | High | Recovery is partly client-side and partly session-scoped, not a single source of truth. |
| Task preview payload is still larger than needed | PM confirmation flow | Medium | Preview text includes extra labels that do not improve decision quality. |
| Route / agent planning is not cached as a reusable artifact | DAG generation and synthesis | Medium | Pre-summaries are not yet first-class data. |

### 2.5 Operations and security

| Problem | Scope | Risk | Root cause |
|---|---|---:|---|
| Hidden runtime failures are still possible | All service layers | High | Fallbacks absorb too many errors without enough structured telemetry. |
| Session state lives too much in-process | WebSocket and task state | High | Restart safety and multi-instance coordination are not fully externalized yet. |
| Audit and trace correlation are inconsistent | Python / Go / Rust | Medium | Logging and tracing conventions are not yet standardized end-to-end. |

## 3. Detailed Fix Plan

### 3.1 Fast fixes

| Issue | Fast fix | File paths | Validation |
|---|---|---|---|
| Prompt bloat | Keep compact prompt helpers and use them everywhere | `app/services/context_compaction.py`, `app/services/conversation_history.py`, `app/services/orchestrator_preprocessor.py`, `app/services/task_decomposer.py`, `app/services/result_synthesizer.py` | Compare prompt length before/after and keep routine turns materially shorter. |
| WebSocket preview duplication | Use one compact task preview builder | `app/api/websocket_message_flow.py`, `app/api/websocket.py` | Task preview output should be identical across both paths and smaller. |
| History replay noise | Truncate and dedupe history more aggressively | `app/services/conversation_history.py` | History should stay readable while dropping repeated lines and extra whitespace. |
| Result preview inflation | Shrink partial summaries | `app/services/tools/agent_tools.py` | Partial summaries must stay short and still preserve enough context for synthesis. |

### 3.2 Structural refactor

| Issue | Refactor | File paths | Validation |
|---|---|---|---|
| `agent_service.py` overload | Split into prompt builder, memory context, tool loop, persistence adapter | `app/services/agent_service.py` plus new helpers under `app/services/` | `build_prompt` remains orchestration-only and helper boundaries become testable. |
| WebSocket overload | Split into lifecycle, control events, preview events, collaboration stream | `app/api/websocket.py`, `app/api/websocket_dispatch.py`, `app/api/websocket_lifecycle.py`, `app/api/websocket_message_flow.py` | Each file should own one responsibility and tests should target each lane independently. |
| Frontend page overload | Move IM orchestration into feature hooks and stores | `frontend/app/page.tsx`, `frontend/lib/*`, `frontend/components/chat/*` | Page file should only compose state and pass callbacks. |
| Recovery / replay complexity | Centralize recovery state and replay rules | `frontend/lib/messageRecovery.ts`, `frontend/lib/dagStore.ts`, `frontend/lib/sessionStore.ts` | Reload, refresh, and reconnect should preserve messages and DAG state. |

### 3.3 Long-term refactor

| Issue | Long-term direction | File / area |
|---|---|---|
| Session state | Move to Redis-backed and event-backed coordination | `app/services/websocket_manager.py`, `app/api/websocket_state.py`, Go session services |
| Prompt economy | Introduce layered ContextOS | `app/services/context_compaction.py` and memory stack |
| Replay / audit | Make server-side event log the source of truth | WebSocket events, task state machine, storage layer |
| Product extensibility | Add DAG editor, template market, SDKs, and token management | `frontend/components/admin/*`, `app/api/tasks.py`, `app/api/agent.py`, docs |

## 4. Business Closure Plan

### 4.1 End-to-end flow

The target closed loop should be:

1. User message enters IM.
2. Preprocessor classifies intent and compresses route / solution / task hints.
3. Router decides whether to use direct response, collaborative DAG, or orchestration.
4. WebSocket sends a compact task preview.
5. User confirms or modifies the plan.
6. DAG execution runs with per-node updates and compact result previews.
7. Synthesizer combines node results into a final answer.
8. Message, DAG, audit, and memory state are persisted.
9. Frontend can replay the full state after refresh or reconnect.
10. Observability and audit logs show the full chain.

### 4.2 Short term

Focus:

- Continue thinning `frontend/app/page.tsx`.
- Finish message recovery and DAG replay hardening.
- Keep prompt payloads small by default.

Success criteria:

- Refresh does not lose messages.
- DAG preview and replay remain stable.
- Common prompts are shorter and less repetitive.

### 4.3 Mid term

Focus:

- DAG visual editor.
- Agent template market.
- Tool market.
- TypeScript / Python / Go SDKs.
- Token and API key management.

Success criteria:

- Users can create, save, reuse, and share workflows.
- Teams can manage tokens and permissions from the platform.
- External developers can integrate without reading internal code first.

### 4.4 Near-term execution order

1. Finish permission-rule CRUD and validation in the new admin module.
2. Keep thinning the last frontend coupling in message recovery and DAG replay.
3. Add route / agent pre-summary caches and shrink preview payloads one more layer.
4. Only after those are stable, move to DAG editor, template market, SDKs, and token management.

### 4.5 Long term

Focus:

- CRDT multi-user editing.
- K8s elasticity with canary and chaos testing.
- SOC2 / compliance work.
- AgentNet decentralized communication.

Success criteria:

- Platform can support many tenants and many teams without collapsing into one-process assumptions.

## 5. Execution Phases

### Phase A: Stabilize

Current status: complete. Admin responsiveness, permission rules, message recovery, replay cursors, duplicate-event handling, session isolation, and persisted DAG replay are covered by focused tests.

Priority:

1. Keep transport and state recovery correct.
2. Remove duplicated preview and recovery logic.
3. Keep user-visible flows from breaking.

Deliverables:

- Message recovery helpers in `frontend/lib/messageRecovery.ts`.
- DAG session state helpers in `frontend/lib/dagStore.ts`.
- Compact WebSocket URL builder in `frontend/lib/websocketUrl.ts`.
- Confirmed task preview and solution proposal flows.

### Phase B: Decouple

Priority:

1. Thin `agent_service.py`.
2. Thin `websocket.py`.
3. Keep `page.tsx` as a composition shell.

Deliverables:

- Shared context compaction helpers.
- Short prompt templates.
- Smaller transport/event handlers.

### Phase C: Token Economy

Current status: in progress. Route / agent versioned caches, tokenizer-aware
budgets, memory deduplication, prompt prefix de-duplication, and the
Rust-to-Python-to-online summary loop are landed. Native provider tokenizers,
distributed cache versions, and end-to-end quality evaluation remain.

Cognitive memory migration status:

- Landed: orthogonal `memory_type/scope/source/version` metadata with legacy
  Markdown compatibility.
- Landed: session conversations, summaries, and task execution history are
  classified as Episodic Memory.
- Landed: structured Semantic extraction with provenance, confidence, version,
  conflict supersession, query retrieval, and prompt projection.
- Next: classify Skills, DAG, SOP, and tool policies as Procedural Memory.

Priority:

1. Cache route / agent pre-summaries.
2. Shorten preview payloads.
3. Reduce repeated context assembly.

Deliverables:

- `app/services/context_compaction.py`.
- Shorter history and memory context.
- Shorter synthesis inputs.
- `app/services/token_budget.py` as the shared model budget authority.
- `app/services/context_summary_cache.py` with explicit version invalidation.
- `app/services/memory_context.py` for layered projection and overlap removal.
- `app/services/memory_summary_consumer.py` for durable summary write-back.
- Memory architecture and maturity assessment in `docs/memory-architecture.md`.

### Phase D: Platform Hardening

Priority:

1. Externalize session state.
2. Make execution restart-safe.
3. Standardize telemetry.

Deliverables:

- Redis/event-backed coordination.
- Structured logs and trace IDs.
- Retry / timeout / cancellation semantics.

### Phase E: Productization

Priority:

1. DAG editor.
2. Template market.
3. SDKs and token management.

Deliverables:

- Workflow authoring UI.
- Reusable templates.
- Public integration surface.

## 6. Already Landed

These are the concrete foundation pieces now in the repo:

- `app/services/context_compaction.py`
- `app/services/conversation_history.py`
- `app/services/orchestrator_preprocessor.py`
- `app/services/task_decomposer.py`
- `app/services/result_synthesizer.py`
- `app/services/tools/agent_tools.py`
- `app/api/websocket_message_flow.py`
- `app/api/websocket.py`
- `frontend/lib/dagStore.ts`
- `frontend/lib/messageRecovery.ts`
- `frontend/lib/websocketUrl.ts`
- `frontend/lib/outgoingMessageDraft.ts`
- `frontend/hooks/useSessionWebSocket.ts`
- `frontend/hooks/useSessionRecovery.ts`
- `frontend/components/admin/PermissionModule.tsx`
- `frontend/__tests__/components/chat/taskPreviewReplay.test.tsx`
- `frontend/__tests__/components/chat/dagReplay.test.tsx`

## 7. Rule of Thumb

Do not expand feature surface until the core loop stays:

- smaller,
- replayable,
- observable,
- and cheap enough to run repeatedly.
