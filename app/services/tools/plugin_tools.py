"""Generic bridge: pluggy ``register_tools()`` hook → global tool_registry.

Closes the pre-existing gap where ``ToolHookSpecs.register_tools()`` was
declared but never consumed by production code. Now ANY plugin (builtin,
entry-point, or PLUGINS_PATH) can ship tools that land in the same registry
as the built-ins — the multimodal package is the first consumer.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

logger = logging.getLogger("agenthub.tools.plugins")


def _resolve_handler(handler: Any) -> Any:
    """Accept a callable directly, or an ``module:attr`` / dotted import path.

    Dotted tails are walked attribute-by-attribute so instance methods like
    ``app.services.adapter_manager:adapter_manager.ping`` resolve correctly.
    """
    if callable(handler):
        return handler
    if isinstance(handler, str) and handler.strip():
        path = handler.strip()
        module_name, _, attr = path.partition(":")
        if not attr:
            module_name, _, attr = path.rpartition(".")
        try:
            module = importlib.import_module(module_name)
            resolved: Any = module
            for part in [segment for segment in attr.split(".") if segment]:
                resolved = getattr(resolved, part)
        except (ImportError, AttributeError, ValueError) as exc:
            raise ValueError(f"cannot resolve handler {path!r}: {exc}") from exc
        if not callable(resolved):
            raise ValueError(f"handler {path!r} resolved to a non-callable")
        return resolved
    raise ValueError("tool definition carries no usable handler")


def _parameter_from_dict(item: dict[str, Any]) -> Any:
    from app.services.tool_registry import ToolParameter

    return ToolParameter(
        name=str(item["name"]),
        type=str(item.get("type", "string")),
        required=bool(item.get("required", False)),
        description=str(item.get("description", "")),
        default=item.get("default"),
    )


def tool_definition_from_dict(item: dict[str, Any]) -> Any:
    """Convert a hook-provided tool dict into a real ToolDefinition."""
    from app.services.tool_registry import ToolDefinition

    name = str(item.get("name") or "").strip()
    if not name:
        raise ValueError("plugin tool definition is missing 'name'")
    parameters = item.get("parameters")
    converted: list[Any] = []
    if isinstance(parameters, list):
        for raw in parameters:
            if isinstance(raw, dict) and "name" in raw:
                converted.append(_parameter_from_dict(raw))
            else:
                # JSON-Schema style {"type":"object","properties":{...}}:
                from app.services.tool_registry import ToolParameter

                properties = (raw or {}).get("properties") or {}
                required = set((raw or {}).get("required") or [])
                for prop_name, prop in properties.items():
                    converted.append(ToolParameter(
                        name=prop_name,
                        type=str(prop.get("type", "string")),
                        required=prop_name in required,
                        description=str(prop.get("description", "")),
                        default=prop.get("default"),
                    ))
    return ToolDefinition(
        name=name,
        description=str(item.get("description", "")),
        category=str(item.get("category", "plugin")),
        parameters=converted,
        return_type=str(item.get("return_type", "")),
        examples=[],
        risk_level=str(item.get("risk_level", "L1")),
        handler=_resolve_handler(item.get("handler")),
        is_concurrency_safe=bool(item.get("is_concurrency_safe", True)),
    )


def register_hook_tools(plugin_manager: Any) -> int:
    """Drain every plugin's ``register_tools()`` results into tool_registry.

    Idempotent at the registry level: re-registering an existing name merely
    overwrites it (matching registry semantics) and logs a warning.

    Returns the number of tools registered via plugins.
    """
    from app.services.tool_registry import tool_registry

    registered = 0
    try:
        results = plugin_manager.hook.register_tools()
    except Exception as exc:  # noqa: BLE001 — plugin failure must not block startup
        logger.warning("register_hook_tools: hook invocation failed: %s", exc)
        return 0
    for payload in results or []:
        if not payload:
            continue
        for item in payload:
            if not isinstance(item, dict):
                logger.warning("register_hook_tools: skipping non-dict entry %r", item)
                continue
            try:
                tool_registry.register(tool_definition_from_dict(item))
                registered += 1
            except Exception as exc:  # noqa: BLE001 — one bad plugin tool must not block others
                logger.warning("register_hook_tools: failed to register %r: %s",
                               item.get("name"), exc)
    if registered:
        logger.info("register_hook_tools: %d plugin tool(s) registered", registered)
    return registered