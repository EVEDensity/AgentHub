"""Agent service package (R3 split).

Five single-responsibility modules behind the legacy `agent_service` facade:

- ``routing``: agent resolution, model selection, model racing, runtime health.
- ``context``: conversation/memory projection, setting loading, prompt assembly.
- ``tooling``: bounded tool-call loop, CloudCode/subprocess adapters, CLI tools.
- ``persistence``: message/task persistence, PM state machine, degradation.
- ``orchestrator``: top-level call/stream entry points and collaboration.

Import via the facade (``app.services.agent_service``) for compatibility, or
import directly from this package.
"""

from app.services.agent.context import (  # noqa: F401
    _build_attachment_context,
    _build_conversation_history,
    _build_memory_context,
    _build_quote_context,
    _build_reasoning_instruction,
    _build_reply_lang_instruction,
    _build_tool_section,
    _estimate_token_usage,
    _format_conversation,
    _get_agent_tools,
    _invalidate_memory_cache,
    _intent_from_domain,
    _load_settings,
    build_prompt,
)
from app.services.agent.orchestrator import (  # noqa: F401
    CollaborationContext,
    _collab_expectations,
    call_agent,
    load_skill_prompt,
    stream_agent_response,
)
from app.services.agent.persistence import (  # noqa: F401
    _check_degradation_recovery,
    _enter_degradation,
    _get_pm_state,
    _get_session_mgr_singleton,
    _schedule_recovery_check,
    _set_pm_state,
    list_messages,
    save_message,
    seed_default_agents_for_user,
)
from app.services.agent.routing import (  # noqa: F401
    _get_streaming_executor,
    _race_models,
    _race_models_streaming,
    _score,
    _update_runtime,
    candidate_models_for_role,
    choose_models,
    extract_mentions,
    extract_skill_calls,
    get_direct_chat_agent,
    lookup_agent,
    record_task_execution,
    resolve_agent,
    resolve_all_agents,
)
from app.services.agent.tooling import (  # noqa: F401
    _execute_cli_tool,
    _log_tool_call,
    _parse_changed_files_from_diff,
    _read_file_content,
    _run_cloudcode_post_hooks,
    _run_tool_call_loop,
    _stream_cloudcode_response,
)