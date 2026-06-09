from __future__ import annotations

from app.services.adapters.cloudcode_adapter import CloudCodeAdapter
from app.services.protocols.claude_code import ClaudeCodeProtocol


class ClaudeCodeAdapter(CloudCodeAdapter):
    """Adapter for Anthropic's Claude Code CLI (``claude``).

    Headless (one-shot) invocation::

        claude -p "<prompt>" --output-format stream-json

    Interactive (bidirectional) invocation::

        claude --output-format stream-json --input-format stream-json

    Output is NDJSON (one JSON object per line) with event types:
    ``assistant``, ``tool_use``, ``tool_result``, ``end``.

    Session continuity is available via ``--resume SESSION_ID`` /
    ``--continue``, passed through the *model* parameter as extra
    CLI flags.
    """

    def __init__(
        self,
        binary: str = "claude",
        default_model: str = "local-claude",
    ) -> None:
        super().__init__(
            binary=binary,
            default_model=default_model,
            protocol=ClaudeCodeProtocol(),
        )

    def _build_cmd(self, model: str) -> list[str]:
        """Build command: ``claude -p <prompt> --output-format stream-json``.

        The *model* parameter carries the prompt text when called via
        ``stream_prompt()`` / ``execute_prompt()``, but those methods
        pass it through stdin — not on the command line.  The *model*
        string is used here only for ping / version checks.
        """
        if model and model not in (self.default_model, "local-claude", "ping"):
            # Allow model string to carry extra CLI arguments (--resume, etc.)
            return [self.binary] + model.split()
        return [self.binary]
