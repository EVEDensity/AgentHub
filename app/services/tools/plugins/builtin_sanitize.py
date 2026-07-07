# ─────────────────────────────────────────────────────────────────────
# SanitizePlugin — redact secrets from tool output (builtin)
# ─────────────────────────────────────────────────────────────────────
# Hooks: post_tool_use
# Behavior: after a tool executes, scans its result for sensitive
# information (AWS keys, GitHub tokens, JWTs, private keys, IPs in
# strict mode) and redacts them. Reuses OutputSanitizer from
# sandbox_executor.py so the redaction patterns stay in one place.
#
# Sanitize level comes from context["sanitize_level"], defaulting to
# "basic". Levels: "off" | "basic" | "strict".
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import Any

from ..plugin_spec import hookimpl

logger = logging.getLogger("agenthub.plugins.sanitize")

# Fields in a tool result that may contain user-visible text output.
# We scan and redact each of these if present.
_OUTPUT_FIELDS = ("stdout", "output", "content", "stderr", "message", "result_text")


class SanitizePlugin:
    """Builtin sanitize plugin — redact secrets from tool output.

    Registered as ``builtin.sanitize``. Implements ``post_tool_use`` to
    return a ``modified_result`` dict with sensitive patterns replaced.
    Returns None when sanitization is off or no fields need redaction.
    """

    def __init__(self) -> None:
        # Lazily import the sanitizer so this module doesn't hard-depend
        # on httpx (which sandbox_executor imports at module level).
        self._sanitizer = None

    @hookimpl
    def post_tool_use(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        level = str(context.get("sanitize_level", "basic") or "basic")
        if level == "off":
            return None

        sanitizer = self._get_sanitizer()
        if sanitizer is None:
            return None  # OutputSanitizer unavailable — skip silently

        modified = False
        new_result = dict(result)
        for field in _OUTPUT_FIELDS:
            val = new_result.get(field)
            if isinstance(val, str) and val:
                redacted = sanitizer.sanitize(val, level=level)
                if redacted != val:
                    new_result[field] = redacted
                    modified = True

        if not modified:
            return None
        logger.debug("sanitize_plugin: redacted output for tool '%s'", tool_name)
        return {"modified_result": new_result}

    @hookimpl
    def tool_categories(self) -> list[str] | None:
        """Sanitize cares about every tool category."""
        return None

    def _get_sanitizer(self):
        if self._sanitizer is None:
            try:
                from ..sandbox_executor import OutputSanitizer  # type: ignore
                self._sanitizer = OutputSanitizer()
            except Exception as exc:  # noqa: BLE001
                logger.warning("sanitize_plugin: OutputSanitizer unavailable: %s", exc)
                self._sanitizer = False  # sentinel: tried and failed
        return self._sanitizer if self._sanitizer is not False else None
