from __future__ import annotations

from app.services.protocols.base import SubprocessProtocol


class CodexCLIProtocol(SubprocessProtocol):
    """Protocol for OpenAI's Codex CLI.

    Codex CLI in ``exec`` mode is **one-shot only** — each invocation
    starts a new process, runs to completion, and exits.  There is no
    built-in mechanism for interactive tool feedback via stdin.

    When Codex CLI adds bidirectional JSON Lines support in the future,
    this protocol can be upgraded to override ``supports_interactive()``.
    """

    adapter_type = "local_codex"

    def supports_interactive(self) -> bool:
        return False

    # ── Message encoding ────────────────────────────────────────────
    # Codex CLI ``exec`` mode reads a plain-text prompt from stdin
    # (passed through CloudCodeAdapter.stream_prompt()), so the default
    # ``encode_user_message()`` implementation (raw text) is sufficient.
