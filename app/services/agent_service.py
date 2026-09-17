"""Agent service facade (R3 re-export).

The implementation moved to the ``app.services.agent`` package as five
single-responsibility modules. This module exists as a thin compatibility
facade: every symbol that external callers imported from here is re-exported,
so neither call sites nor private state references change.

Module mapping (R3 split):

- ``agent.routing``: agent resolution, model selection/racing, runtime health.
- ``agent.context``: conversation/memory projection, settings, prompt assembly.
- ``agent.tooling``: bounded tool-call loop, CloudCode/subprocess adapters.
- ``agent.persistence``: message/task persistence, PM state, degradation.
- ``agent.orchestrator``: call/stream entry points and collaboration context.

Prefer importing from the concrete modules going forward; this facade remains
for backward compatibility only.
"""

from __future__ import annotations

# Public API and shared state are re-exported from the five module split.
# `# noqa: F401` keeps these intentional re-exports from being pruned.
from app.services.agent.context import (
    _MEMORY_CONTEXT_CACHE,  # noqa: F401
    _invalidate_memory_cache,  # noqa: F401
    _intent_from_domain,  # noqa: F401
)
from app.services.agent.orchestrator import (
    _ROLE_LABELS,  # noqa: F401
)
from app.services.agent.persistence import (
    DEFAULT_AGENTS,  # noqa: F401
    _PM_STATES,  # noqa: F401
    _SESSION_MGRS,  # noqa: F401
)
from app.services.agent.routing import (
    AGENTS,  # noqa: F401
    _RUNTIME,  # noqa: F401
)

from app.services.agent import (  # noqa: F401  (public symbols via package __init__)
    CollaborationContext,
    call_agent,
    candidate_models_for_role,
    choose_models,
    extract_mentions,
    extract_skill_calls,
    get_direct_chat_agent,
    list_messages,
    load_skill_prompt,
    lookup_agent,
    record_task_execution,
    resolve_agent,
    resolve_all_agents,
    save_message,
    seed_default_agents_for_user,
    stream_agent_response,
)


def _mem_context_cache_get(key: str):
    return _MEMORY_CONTEXT_CACHE.get(key)


__all__ = [
    "call_agent",
    "stream_agent_response",
    "save_message",
    "seed_default_agents_for_user",
    "list_messages",
    "lookup_agent",
    "resolve_agent",
    "resolve_all_agents",
    "get_direct_chat_agent",
    "CollaborationContext",
    "load_skill_prompt",
    "candidate_models_for_role",
    "choose_models",
    "extract_mentions",
    "extract_skill_calls",
    "record_task_execution",
    "_RUNTIME",
    "_MEMORY_CONTEXT_CACHE",
    "_SESSION_MGRS",
    "_PM_STATES",
    "AGENTS",
    "DEFAULT_AGENTS",
    "_ROLE_LABELS",
    "_invalidate_memory_cache",
    "_intent_from_domain",
]