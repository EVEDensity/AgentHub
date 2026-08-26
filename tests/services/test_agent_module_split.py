from __future__ import annotations

import asyncio

from app.services import agent_service as facade
from app.services.agent import context, orchestrator, persistence, routing, tooling


def test_facade_reexports_public_api():
    """Facade exposes every public entry point and shared state."""
    for name in (
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
        "extract_mentions",
        "extract_skill_calls",
        "record_task_execution",
        "candidate_models_for_role",
        "choose_models",
        "_RUNTIME",
        "_MEMORY_CONTEXT_CACHE",
        "_SESSION_MGRS",
        "_PM_STATES",
        "AGENTS",
        "DEFAULT_AGENTS",
        "_ROLE_LABELS",
        "_intent_from_domain",
    ):
        assert hasattr(facade, name), f"facade missing {name}"


def test_facade_and_modules_share_state_identity():
    """Shared mutable state must be the same object across the package."""
    assert facade._RUNTIME is routing._RUNTIME
    assert facade.DEFAULT_AGENTS == persistence.DEFAULT_AGENTS
    assert facade._ROLE_LABELS == orchestrator._ROLE_LABELS
    # Cross-module callable resolves through the package graph.
    assert context._intent_from_domain is facade._intent_from_domain


def test_extract_mentions_and_skills_unchanged():
    assert routing.extract_mentions("@Orchestrator help") == ["Orchestrator"]
    assert routing.extract_skill_calls("run /codegen please") == ["codegen"]


def test_intent_from_domain_mapping():
    assert context._intent_from_domain("codegen") == "code_generation"
    assert context._intent_from_domain("unknown") == "general"


def test_collaboration_context_records_and_projects():
    ctx = orchestrator.CollaborationContext("build auth module")
    ctx.register({"agent_id": "Architect", "domain": "architect"})
    ctx.register({"agent_id": "CodeGen", "domain": "codegen"})
    ctx.record("Architect", "architect", "采用 FastAPI + PostgreSQL。建议分三层。")
    text = ctx.context_for("CodeGen")
    assert "Architect" in text
    assert "多智能体协作上下文" in text
    summary = ctx.summary
    assert "协作摘要" in summary


def test_shared_memory_cache_invalidation():
    from app.services.agent.context import _invalidate_memory_cache

    context._MEMORY_CONTEXT_CACHE["k"] = (0.0, "v")
    assert "k" in context._MEMORY_CONTEXT_CACHE
    _invalidate_memory_cache()
    assert context._MEMORY_CONTEXT_CACHE == {}
    _invalidate_memory_cache()


def test_routing_helpers_without_db():
    """Pure routing helpers remain pure after the split."""
    assert routing._score({"latency": 50.0}) >= 0
    chosen = routing.choose_models([])
    assert chosen == []