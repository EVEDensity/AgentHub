from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Any

from app.db.session import afetch_all, afetch_one, aexecute
from app.services.secret_service import decrypt_secret
from app.services.text_processing import (
    filter_streaming_chunk,
    is_code_request,
    is_codegen_json_response,
    latex_to_unicode,
    remove_repeated_text,
    reset_stream_filter,
    strip_codegen_prefix,
    strip_kimi_thinking,
    strip_think_tags,
)

# Backward-compatible aliases for internal use
_filter_streaming_chunk = filter_streaming_chunk
_is_code_request = is_code_request
_is_codegen_json_response = is_codegen_json_response
_latex_to_unicode = latex_to_unicode
_remove_repeated_text = remove_repeated_text
_reset_stream_filter = reset_stream_filter
_strip_codegen_prefix = strip_codegen_prefix
_strip_kimi_thinking = strip_kimi_thinking
_strip_think_tags = strip_think_tags
from app.config import REQUEST_TIMEOUT_SECONDS


logger = logging.getLogger("agenthub.agent.routing")

AGENTS = {"Orchestrator", "Architect", "CodeGen", "Review", "Test", "Deploy", "Implement"}
_RUNTIME: dict[str, dict] = {}

_STREAMING_EXECUTOR = None

def _get_streaming_executor():
    """Return the application-level StreamingToolExecutor, if configured."""
    global _STREAMING_EXECUTOR
    if _STREAMING_EXECUTOR is not None:
        return _STREAMING_EXECUTOR
    try:
        from app.services.tools import get_streaming_executor as _gse
        _STREAMING_EXECUTOR = _gse()
        return _STREAMING_EXECUTOR
    except Exception:
        return None

async def resolve_all_agents(content: str, user_id: str | None = None) -> list[dict]:
    """Return ALL valid agents @mentioned in the content.

    If no valid mention is found, falls back to the default chat agent.
    When user_id is provided, only agents belonging to that user are
    considered (system agents serve as fallback only).
    """
    agents: list[dict] = []
    seen: set[str] = set()
    uid = user_id or ""
    for name in extract_mentions(content):
        if name in seen:
            continue
        seen.add(name)
        agent = await lookup_agent(name, uid)
        if agent:
            agents.append(agent)

    if agents:
        return agents

    # No valid mention — fall back to user-configured default, then Orchestrator
    default_row = await afetch_one("SELECT value FROM system_config WHERE key='default_chat_agent'")
    default_agent_id = default_row["value"] if default_row else "Orchestrator"
    agent = await lookup_agent(default_agent_id, uid)
    return [agent] if agent else [{"agent_id": "Orchestrator", "domain": "orchestrator", "adapter_type": "mock", "risk_level": "L2"}]

async def resolve_agent(content: str, user_id: str | None = None) -> dict:
    """Resolve a single agent from @mentions (kept for backward compatibility)."""
    return (await resolve_all_agents(content, user_id))[0]

async def get_direct_chat_agent(user_id: str | None = None) -> dict:
    """Resolve the agent config for direct/default-model chat mode.

    Resolution order:
    1. system_config key 'default_chat_agent' → agent_registry lookup
    2. First active model_config → synthetic agent
    3. Hard fallback to a minimal Orchestrator-like agent

    When user_id is provided, only agents belonging to that user (or system
    agents with user_id='') are considered.
    """
    uid = user_id or ""
    columns = "agent_id,domain,status,adapter_type,risk_level,base_model_name,base_url,api_key"

    # 1) User-configured default chat agent
    default_row = await afetch_one("SELECT value FROM system_config WHERE key='default_chat_agent'")
    if default_row:
        agent = await lookup_agent(default_row["value"], uid, columns=columns)
        if agent and agent.get("adapter_type") and agent.get("adapter_type") != "mock":
            logger.info("direct_chat_agent: using configured default agent=%s", agent["agent_id"])
            return agent

    # 2) Prefer Orchestrator — it can intelligently route to CodeGen when needed.
    #    Using CodeGen directly as the default agent means even non-code tasks
    #    (greetings, factual queries, architecture discussions) get a code-gen
    #    response, which confuses users.
    agent = await lookup_agent("Orchestrator", uid, columns=columns)
    if agent and agent.get("adapter_type") and agent.get("adapter_type") != "mock":
        logger.info("direct_chat_agent: using Orchestrator as default")
        return agent

    # 3) Fall back to first active model_config as synthetic agent
    mc = await afetch_one(
        "SELECT provider,model_name,api_key,base_url FROM model_configs WHERE is_active=1 ORDER BY id ASC LIMIT 1"
    )
    if mc:
        logger.info("direct_chat_agent: using first active model_config provider=%s model=%s",
                     mc.get("provider"), mc.get("model_name"))
        return {
            "agent_id": "__direct__",
            "domain": "general",
            "adapter_type": mc["provider"],
            "risk_level": "L1",
            "base_model_name": mc.get("model_name") or "",
            "base_url": mc.get("base_url") or "",
            "api_key": mc.get("api_key") or "",
        }

    # 4) Hard fallback to minimal Orchestrator
    agent = await lookup_agent("Orchestrator", uid, columns=columns)
    if agent:
        logger.info("direct_chat_agent: falling back to Orchestrator (mock)")
        return agent
    return {"agent_id": "Orchestrator", "domain": "orchestrator", "adapter_type": "mock", "risk_level": "L2"}

async def candidate_models_for_role(role: str, user_id: str | None = None) -> list[dict]:
    uid = user_id or ""
    # 1) Explicit role bindings (role_bindings JOIN model_configs)
    rows = await afetch_all(
        "SELECT mc.id,mc.provider,mc.model_name AS model_name,mc.api_key,mc.base_url,rb.prompt FROM role_bindings rb JOIN model_configs mc ON rb.model_config_id=mc.id WHERE rb.role=$1 AND mc.is_active=1 ORDER BY mc.id DESC",
        role,
    )
    if rows:
        logger.info("model_for agent=%s source=role_binding count=%d", role, len(rows))
        return rows
    # 2) Agent's own config in agent_registry (adapter_type + base_model_name + base_url + api_key)
    agent_row = await lookup_agent(role, uid, columns="adapter_type,base_model_name,base_url,api_key")
    if agent_row and agent_row.get("adapter_type") and agent_row.get("adapter_type") != "mock":
        result = [{
            "id": 0,
            "provider": agent_row["adapter_type"],
            "model_name": agent_row.get("base_model_name") or "ping",  # "ping" → adapter uses its default_model
            "api_key": agent_row.get("api_key") or "",
            "base_url": agent_row.get("base_url") or "",
            "prompt": "",
        }]
        logger.info("model_for agent=%s source=agent_registry provider=%s model=%s", role, agent_row["adapter_type"], agent_row.get("base_model_name"))
        return result
    # 3) Fallback: any active model_config, rotated per-role so agents
    #    don't all crowd onto the same first model.
    rows = await afetch_all("SELECT id,provider,model_name,api_key,base_url,'' AS prompt FROM model_configs WHERE is_active=1 ORDER BY id DESC")
    if rows and len(rows) > 1:
        # Deterministic rotation: each role gets a different primary model
        offset = hash(role) % len(rows)
        rows = rows[offset:] + rows[:offset]
        logger.info("model_for agent=%s source=model_configs_rotated count=%d offset=%d primary=%s/%s",
                     role, len(rows), offset, rows[0].get("provider"), rows[0].get("model_name"))
    elif rows:
        logger.info("model_for agent=%s source=model_configs_single provider=%s model=%s",
                     role, rows[0].get("provider"), rows[0].get("model_name"))
    return rows or [{"id": 0, "provider": "mock", "model_name": "mock", "api_key": "", "base_url": "", "prompt": ""}]

def _score(model: dict) -> float:
    key = f"{model.get('provider')}:{model.get('model_name')}:{model.get('base_url','')}"
    s = _RUNTIME.get(key, {"ok": 0, "fail": 0, "latency": 1200.0})
    total = max(1, s["ok"] + s["fail"])
    success = s["ok"] / total
    latency_score = max(0.05, min(1.0, 1000.0 / max(80.0, s["latency"])))
    return 0.65 * success + 0.35 * latency_score + random.uniform(0.0, 0.05)

def choose_models(models: list[dict]) -> list[dict]:
    ranked = sorted(models, key=_score, reverse=True)
    return ranked

def _update_runtime(model: dict, ok: bool, latency_ms: float) -> None:
    key = f"{model.get('provider')}:{model.get('model_name')}:{model.get('base_url','')}"
    state = _RUNTIME.setdefault(key, {"ok": 0, "fail": 0, "latency": latency_ms})
    if ok:
        state["ok"] += 1
    else:
        state["fail"] += 1
    state["latency"] = state["latency"] * 0.7 + latency_ms * 0.3
    # ── Record to performance monitor ──────────────────────────────
    try:
        from app.services.performance_monitor import monitor
        provider = str(model.get("provider", "unknown"))
        model_name = str(model.get("model_name", "unknown"))
        monitor.record_llm_call(provider, model_name, ok=ok, latency_ms=latency_ms)
    except Exception:
        pass

async def record_task_execution(
    task_type: str,
    agent_id: str,
    success: bool,
    duration_ms: int,
    tool_calls_count: int = 0,
    retry_count: int = 0,
    error_type: str | None = None,
    session_id: str | None = None,
) -> None:
    """Record a task execution for the learning/optimization feedback loop.

    Called after each agent invocation completes (success or failure).
    The data feeds into AgentSelector for future agent selection decisions.
    """
    try:
        from app.db.init_db import now as db_now
        await aexecute(
            """INSERT INTO task_execution_history
               (task_type, assigned_agent, success, duration_ms, tool_calls_count,
                retry_count, error_type, session_id, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            task_type, agent_id, success, duration_ms, tool_calls_count,
            retry_count, error_type, session_id, db_now(),
        )
        logger.debug(
            "record_task_execution: agent=%s type=%s success=%s duration=%dms",
            agent_id, task_type, success, duration_ms,
        )
    except Exception:
        logger.debug("record_task_execution: failed to write (table may not exist yet)")

async def _race_models(
    prompt: str,
    models: list[dict],
    iteration: int,
    native_tools: list[dict] | None,
    token: Any,
    system_prompt: str = "",
) -> tuple[str, dict, Any, list[str]]:
    """Race the top N models concurrently — first successful response wins.

    This turns the serial ``for model in models: try...`` cascade into a
    concurrent fan-out for iteration 0 where we only need ONE model to
    produce tool calls or a text response.  The slowest model no longer
    dictates wall-clock latency.

    Only 2 models are raced per batch to balance latency reduction against
    wasted API credit burn.  If all racers fail, we continue to the next
    batch.

    Returns ``(result, model_dict, adapter, errors)`` where *result* is
    non-empty on success.
    """
    from app.services.adapter_manager import adapter_manager as _am

    BATCH = 2  # race 2 models at a time

    errors: list[str] = []
    for batch_start in range(0, len(models), BATCH):
        batch = models[batch_start:batch_start + BATCH]
        if len(batch) == 1:
            # Only one left — no point racing, just call serially
            model = batch[0]
            if token and token.cancelled:
                return ("", model, None, errors)
            adapter = _am.get_adapter(model.get("provider", "mock"))
            started = time.perf_counter()
            try:
                result = await adapter.execute_prompt(
                    prompt,
                    model.get("model_name", "mock"),
                    decrypt_secret(model.get("api_key", "")),
                    model.get("base_url", ""),
                    tools=native_tools if native_tools else None,
                    system_prompt=system_prompt,
                )
                elapsed = (time.perf_counter() - started) * 1000
                _update_runtime(model, True, elapsed)
                logger.info(
                    "race batch=%d solo win: provider=%s model=%s elapsed=%.0fms",
                    batch_start, model.get("provider"), model.get("model_name"), elapsed,
                )
                return (result, model, adapter, errors)
            except Exception as exc:
                elapsed = (time.perf_counter() - started) * 1000
                _update_runtime(model, False, elapsed)
                errors.append(f"{model.get('provider')}/{model.get('model_name')}: {exc}")
                continue

        # ── Race 2 models concurrently ──────────────────────────────
        async def _call_one(model: dict) -> tuple[dict, str | None, Any | None, Exception | None]:
            if token and token.cancelled:
                return (model, None, None, None)
            adapter = _am.get_adapter(model.get("provider", "mock"))
            started = time.perf_counter()
            try:
                result = await adapter.execute_prompt(
                    prompt,
                    model.get("model_name", "mock"),
                    decrypt_secret(model.get("api_key", "")),
                    model.get("base_url", ""),
                    tools=native_tools if native_tools else None,
                    system_prompt=system_prompt,
                )
                elapsed = (time.perf_counter() - started) * 1000
                _update_runtime(model, True, elapsed)
                return (model, result, adapter, None)
            except Exception as exc:
                elapsed = (time.perf_counter() - started) * 1000
                _update_runtime(model, False, elapsed)
                return (model, None, None, exc)

        tasks = [asyncio.create_task(_call_one(m)) for m in batch]

        # Wait for the FIRST successful response
        winner_result: str | None = None
        winner_model: dict | None = None
        winner_adapter: Any | None = None
        pending = set(tasks)

        while pending and winner_result is None:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                model, result, adapter, exc = t.result()
                if result is not None:
                    winner_result = result
                    winner_model = model
                    winner_adapter = adapter
                    logger.info(
                        "race batch=%d winner: provider=%s model=%s",
                        batch_start, model.get("provider"), model.get("model_name"),
                    )
                    # Cancel remaining tasks
                    for pt in pending:
                        pt.cancel()
                    break
                elif exc is not None:
                    errors.append(
                        f"{model.get('provider')}/{model.get('model_name')}: {exc}"
                    )
                    logger.warning(
                        "race batch=%d loser: provider=%s model=%s error=%s",
                        batch_start, model.get("provider"), model.get("model_name"), exc,
                    )

        # Clean up any remaining pending tasks
        for pt in pending:
            pt.cancel()

        if winner_result is not None:
            return (winner_result, winner_model, winner_adapter, errors)

        # Both racers in this batch failed — continue to next batch

    return ("", models[0] if models else {}, None, errors)

async def _race_models_streaming(
    prompt: str,
    models: list[dict],
    token: Any,
    stream_callback,
    native_tools: list[dict] | None = None,
    system_prompt: str = "",
) -> tuple[str, dict, Any, list[str]]:
    """Race models concurrently using real streaming — first byte wins.

    Unlike ``_race_models`` which waits for the FULL response from each
    model before returning, this function starts ``stream_prompt()`` for
    each candidate in parallel.  The first model that produces a real
    text token wins; its entire stream is then fed through
    *stream_callback* token-by-token so the user sees text immediately.

    Tool-call detection is still performed on the accumulated full text
    after the stream completes (returned as *result* to the caller).

    Returns ``(full_text, winning_model, adapter, errors)``.
    """
    import asyncio as _asyncio

    from app.services.adapter_manager import adapter_manager as _am

    BATCH = 2  # race 2 models at a time
    errors: list[str] = []

    for batch_start in range(0, len(models), BATCH):
        batch = models[batch_start:batch_start + BATCH]

        if len(batch) == 1:
            # Single model — no racing needed, just stream it
            model = batch[0]
            if token and token.cancelled:
                return ("", model, None, errors)
            adapter = _am.get_adapter(model.get("provider", "mock"))
            started = time.perf_counter()
            gathered: list[str] = []
            try:
                async for chunk in adapter.stream_prompt(
                    prompt,
                    model.get("model_name", "mock"),
                    decrypt_secret(model.get("api_key", "")),
                    model.get("base_url", ""),
                    system_prompt=system_prompt,
                ):
                    if chunk:
                        gathered.append(chunk)
                        await stream_callback(chunk)
                elapsed = (time.perf_counter() - started) * 1000
                _update_runtime(model, True, elapsed)
                logger.info(
                    "race_stream batch=%d solo: provider=%s model=%s elapsed=%.0fms",
                    batch_start, model.get("provider"), model.get("model_name"), elapsed,
                )
                return ("".join(gathered), model, adapter, errors)
            except Exception as exc:
                elapsed = (time.perf_counter() - started) * 1000
                _update_runtime(model, False, elapsed)
                err_msg = f"{model.get('provider')}/{model.get('model_name')}: {type(exc).__name__}"
                if str(exc):
                    err_msg += f": {exc}"
                errors.append(err_msg)
                continue

        # ── Race 2 models concurrently via streaming ──────────────
        winner_model: dict | None = None
        winner_adapter: Any | None = None
        winner_chunks: list[str] = []
        first_chunk_queue: _asyncio.Queue = _asyncio.Queue()
        winner_set = False

        async def _stream_one(model: dict):
            nonlocal winner_set
            if token and token.cancelled:
                return
            adapter = _am.get_adapter(model.get("provider", "mock"))
            local_chunks: list[str] = []
            try:
                async for chunk in adapter.stream_prompt(
                    prompt,
                    model.get("model_name", "mock"),
                    decrypt_secret(model.get("api_key", "")),
                    model.get("base_url", ""),
                    system_prompt=system_prompt,
                ):
                    if token and token.cancelled:
                        return
                    if not chunk:
                        continue
                    local_chunks.append(chunk)
                    if not winner_set:
                        # Signal first-chunk to the racer logic
                        await first_chunk_queue.put((model, adapter, chunk))
                        winner_set = True
                    # After winner is chosen, only the winner keeps streaming
                    if winner_model is model:
                        await stream_callback(chunk)
                # Done streaming — signal completion
                if winner_model is model:
                    await first_chunk_queue.put(("_done_", "".join(local_chunks)))
            except Exception as exc:
                if not winner_set:
                    await first_chunk_queue.put(("_error_", model, exc))

        # Map task → model so we can cancel only losers
        task_model_map: dict[_asyncio.Task, dict] = {}
        tasks: list[_asyncio.Task] = []
        for m in batch:
            t = _asyncio.create_task(_stream_one(m))
            tasks.append(t)
            task_model_map[t] = m

        # Wait for the first model to produce a chunk
        try:
            msg = await _asyncio.wait_for(first_chunk_queue.get(), timeout=REQUEST_TIMEOUT_SECONDS)
        except _asyncio.TimeoutError:
            errors.append("race_stream batch %d timeout" % batch_start)
            for t in tasks:
                t.cancel()
            continue

        if isinstance(msg, tuple) and len(msg) == 3:
            kind = msg[0]
            if kind == "_error_":
                _, err_model, exc = msg
                err_detail = f"{err_model.get('provider')}/{err_model.get('model_name')}: {type(exc).__name__}"
                if str(exc):
                    err_detail += f": {exc}"
                errors.append(err_detail)
                logger.warning(
                    "race_stream batch=%d first-model-error: provider=%s model=%s error=%s",
                    batch_start, err_model.get("provider"), err_model.get("model_name"), exc,
                )
                for t in tasks:
                    t.cancel()
                continue  # try next batch

            # First chunk from a model — this is our winner
            winner_model, winner_adapter, first_chunk = msg
            # Push the first chunk through the callback
            await stream_callback(first_chunk)
            winner_chunks.append(first_chunk)
            logger.info(
                "race_stream batch=%d winner: provider=%s model=%s",
                batch_start, winner_model.get("provider"), winner_model.get("model_name"),
            )

            # Cancel only the LOSER tasks — the winner keeps streaming
            for t in tasks:
                if not t.done() and task_model_map[t] is not winner_model:
                    t.cancel()

            # Drain the winner's remaining chunks and done signal
            try:
                while True:
                    finish_msg = await _asyncio.wait_for(
                        first_chunk_queue.get(), timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                    if isinstance(finish_msg, tuple) and finish_msg[0] == "_done_":
                        full_text = finish_msg[1]
                        elapsed = (time.perf_counter() - time.perf_counter()) * 1000  # approx
                        return (full_text, winner_model, winner_adapter, errors)
            except _asyncio.TimeoutError:
                pass

            # If we got here, done signal didn't arrive — use what we have
            full_text = "".join(winner_chunks)
            return (full_text, winner_model, winner_adapter, errors)

        # Shouldn't get here — try next batch
        for t in tasks:
            t.cancel()

    return ("", models[0] if models else {}, None, errors)

async def lookup_agent(
    agent_id: str,
    user_id: str,
    columns: str = "agent_id,domain,status,adapter_type,risk_level",
) -> dict | None:
    """Look up an agent, **prioritizing user-owned over system agents**.

    Two users can each have an agent with the same ``agent_id`` (e.g.
    "Orchestrator").  This helper always returns the row belonging to
    *user_id* first; only if none exists does it fall back to the
    system agent (``user_id=''``).
    """
    uid = user_id or ""
    return await afetch_one(
        f"SELECT {columns} FROM agent_registry "
        "WHERE agent_id=$1 AND (user_id=$2 OR user_id='') "
        "ORDER BY CASE WHEN user_id='' THEN 1 ELSE 0 END "
        "LIMIT 1",
        agent_id, uid,
    )

def extract_mentions(content: str) -> list[str]:
    return re.findall(r"@([\w-]+)", content)

def extract_skill_calls(content: str) -> list[str]:
    """Extract skill invocations like ``/skill-name`` from message content."""
    return re.findall(r"(?:^|\s)/(\w[\w-]*)", content)
