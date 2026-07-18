# AgentHub Optimization Roadmap

## Principles

- Keep orchestration, transport, state, and storage separate.
- Prefer pure helpers for transforms and formatting.
- Make per-session and per-user state explicit.
- Reduce prompt length by compressing context before the model sees it.

## Phase 1: Stabilize

Goal: fix user-visible correctness and security issues.

- Remove hardcoded login hints from the UI.
- Move session pinning to per-user preferences.
- Keep legacy fields only as compatibility fallbacks.
- Reduce broad `except Exception` usage in hot paths.

Deliverables:
- Clean login form defaults.
- User-scoped session pin state.
- Regression tests for pin parsing and sorting.

## Phase 2: Decouple

Goal: shrink high-coupling modules.

- Split `agent_service.py` into:
  - agent runtime
  - prompt/context builder
  - memory manager
  - tool execution adapter
  - streaming/event emitter
- Split `websocket.py` into:
  - connection lifecycle
  - collaboration events
  - task preview events
  - message streaming
- Move shared session preference logic to a small service module.

## Phase 3: Token Economy

Goal: cut model input size without losing task quality.

- Add layered context compression:
  - L0 recent turn cache
  - L1 short-term conversation summary
  - L2 retrieval snippets
  - L3 knowledge graph references
- Truncate auto-name and preview prompts aggressively.
- Cache static system prompts by role and provider.
- Reuse structured summaries instead of replaying raw histories.

Target outcomes:
- 30-60% less prompt payload in routine chat turns.
- Lower repeated token spend on long sessions.

## Phase 4: Platform Hardening

Goal: make the architecture enterprise-grade.

- Replace in-process session state with Redis-backed coordination.
- Make task execution idempotent and restart-safe.
- Add explicit retry, timeout, and cancellation semantics.
- Standardize telemetry for Go, Rust, and Python services.

## Phase 5: Productization

Goal: improve adoption and extensibility.

- DAG visual editor.
- Agent template and tool marketplace.
- SDKs for TypeScript, Python, and Go.
- Better docs for enterprise deployment and self-hosting.
