from __future__ import annotations

from app.services.protocols.base import SubprocessProtocol


class OpenClawProtocol(SubprocessProtocol):
    """Protocol for OpenClaw CLI.

    OpenClaw in ``--json`` mode is **one-shot only** — each invocation
    starts a new process, runs to completion, and exits.  There is no
    built-in mechanism for interactive tool feedback via stdin.

    When OpenClaw adds bidirectional JSON Lines support in the future,
    this protocol can be upgraded to override ``supports_interactive()``.
    """

    adapter_type = "local_openclaw"

    def supports_interactive(self) -> bool:
        return False

    # ── Message encoding ────────────────────────────────────────────
    # OpenClaw ``--json`` mode reads a plain-text prompt from stdin
    # (passed through CloudCodeAdapter.stream_prompt()), so the default
    # ``encode_user_message()`` implementation (raw text) is sufficient.
