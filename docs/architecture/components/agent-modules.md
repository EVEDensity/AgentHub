# Agent Service Modules (R3 split)

> Status: implemented
> Owner: execution maintainers
> Last reviewed: 2026-08-26
> Scope: `app/services/agent/`, facade `app/services/agent_service.py`

## Why the split

`app/services/agent_service.py` had grown to 2650 lines mixing five
responsibilities: agent resolution, prompt assembly, the tool-call loop,
persistence, and top-level orchestration. R3 de-commissions that god module
into a single-responsibility package behind a compatibility facade.

## Module map

| Module | Responsibility | Key symbols | ~LOC (2026-08-26) |
|---|---|---|---|
| `agent/routing.py` | Agent roster resolution, model selection, model racing, runtime health | `resolve_agent`, `resolve_all_agents`, `get_direct_chat_agent`, `lookup_agent`, `candidate_models_for_role`, `_score`, `choose_models`, `_update_runtime`, `_race_models`, `_race_models_streaming`, `_get_streaming_executor`, `record_task_execution`, `extract_mentions`, `extract_skill_calls` | ~540 |
| `agent/context.py` | Conversation/memory projection, settings, prompt assembly | `_build_conversation_history`, `_build_memory_context`, `_invalidate_memory_cache`, `_load_settings`, `_build_reply_lang_instruction`, `_build_reasoning_instruction`, `_get_agent_tools`, `_build_tool_section`, `build_prompt`, `_estimate_token_usage`, `_format_conversation`, `_build_attachment_context`, `_build_quote_context`, `_intent_from_domain` | ~570 |
| `agent/tooling.py` | Bounded tool-call loop, CloudCode/subprocess adapters, CLI tools | `_run_tool_call_loop`, `_log_tool_call`, `_stream_cloudcode_response`, `_execute_cli_tool`, `_run_cloudcode_post_hooks`, `_parse_changed_files_from_diff`, `_read_file_content` | ~970 |
| `agent/persistence.py` | Message/task persistence, PM state machine, degradation tracking | `save_message`, `list_messages`, `seed_default_agents_for_user`, `_get_session_mgr_singleton`, `_set_pm_state`, `_get_pm_state`, `_check_degradation_recovery`, `_enter_degradation`, `_schedule_recovery_check`, `DEFAULT_AGENTS` | ~300 |
| `agent/orchestrator.py` | Top-level call/stream entry points, collaboration context | `call_agent`, `stream_agent_response`, `CollaborationContext`, `_collab_expectations`, `load_skill_prompt`, `_ROLE_LABELS` | ~560 |
| `agent/__init__.py` | Package re-exports for direct imports | all public symbols | ~70 |
| `agent_service.py` | **Compatibility facade** (re-exports; no business logic) | all of the above | ~105 |

## Dependency graph

```text
routing ──┐
context ──┤
          ├──>  orchestrator (call_agent / stream_agent_response)
persistence ┘
tooling: context + routing + persistence (no cycle)
orchestrator: tooling + context + routing + persistence
```

`_intent_from_domain` lives in `context` so `tooling` and `orchestrator` can
both consume it without an import cycle. Cross-module imports are confined to
the package; the facade only re-exports.

## Contract and invariants

- `agent_service.py` keeps its module path and every symbol external callers
  imported from it (verified against 24 call sites in `app/`).
- Shared mutable state is a single object per concern across the package:
  `_RUNTIME`, `_MEMORY_CONTEXT_CACHE`, `_SESSION_MGRS`, `_PM_STATES`.
- No new business state is introduced; DAG/LangGraph compatibility is
  unchanged by this refactor.
- Tests: `tests/services/test_agent_module_split.py` covers facade completeness,
  state identity, helpers, and collaboration projection.

## Change checklist

1. New agent behavior goes into the matching module, not the facade.
2. Keep cross-module imports local to the package (`# noqa: E402`) and do not
   reintroduce cycles.
3. Run `pytest tests/services/test_agent_module_split.py` plus the full suite
   before merging agent changes.

## Related: WebSocket lanes and session state (R3/R4 hot-module thinning)

The IM transport is split into single-purpose lanes; each lane is thin and
delegates business handlers to the page orchestrator via callbacks:

- `app/api/websocket_state.py`: **sole owner** of session-scoped mutable state
  (exec permission, permission requests, auto-name throttle, task-preview
  waits, solution selection, PM question/warning/todo state, memory-task
  throttle, resolved interactions, streaming chunks).
- `app/api/websocket_lifecycle.py`: connect/disconnect + heartbeat.
- `app/api/websocket_dispatch.py`: control-event routing (pong, presence,
  typing, sync, permission responses, PM interactions, diff decisions).
- `app/api/websocket_message_flow.py`: message-flow shaping (greeting detection,
  DAG task items, follow-up todos, task preview).
- `app/api/websocket.py`: orchestration shell — composes the lanes and the
  `_process_and_stream` / `_invoke_agent` business pipeline.

**Rule:** do not reintroduce per-module state dictionaries in `websocket.py`;
all session state must be read/written through `websocket_state` so the
dispatch, flow, and page orchestrator share one source of truth (verified:
no duplicate state remains after the R3/R4 pass).