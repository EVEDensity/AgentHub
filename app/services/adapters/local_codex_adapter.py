from __future__ import annotations

from app.services.adapters.cloudcode_adapter import CloudCodeAdapter
from app.services.protocols.codex import CodexCLIProtocol


class CodexCLIAdapter(CloudCodeAdapter):
    """Adapter for OpenAI's Codex CLI (``codex``).

    Headless invocation::

        codex exec "<prompt>" --json

    One-shot execution via ``codex exec``.  The ``--json`` flag
    produces structured JSON output.  ``--full-auto`` can be appended
    via the *model* parameter for fully autonomous runs.
    """

    def __init__(
        self,
        binary: str = "codex",
        default_model: str = "local-codex",
    ) -> None:
        super().__init__(
            binary=binary,
            default_model=default_model,
            protocol=CodexCLIProtocol(),
        )

    def _build_cmd(self, model: str) -> list[str]:
        """Build command: ``codex exec <prompt> --json``.

        When *model* is not the default, its value is treated as
        additional CLI flags (e.g. ``--full-auto``).
        """
        extra_flags: list[str] = []
        if model and model not in (self.default_model, "local-codex", "ping"):
            extra_flags = model.split()
        return [self.binary, "exec"] + extra_flags + ["--json"]
