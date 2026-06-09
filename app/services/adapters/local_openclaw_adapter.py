from __future__ import annotations

from app.services.adapters.cloudcode_adapter import CloudCodeAdapter
from app.services.protocols.openclaw import OpenClawProtocol


class OpenClawAdapter(CloudCodeAdapter):
    """Adapter for the OpenClaw CLI (``openclaw-cli``).

    Headless invocation::

        openclaw-cli --json "<prompt>"

    OpenClaw is a TypeScript-based open-source AI agent with MCP
    server support for agent-to-agent communication and multiple
    backend switching.
    """

    def __init__(
        self,
        binary: str = "openclaw-cli",
        default_model: str = "local-openclaw",
    ) -> None:
        super().__init__(
            binary=binary,
            default_model=default_model,
            protocol=OpenClawProtocol(),
        )

    def _build_cmd(self, model: str) -> list[str]:
        """Build command: ``openclaw-cli --json <prompt>``.

        When *model* is not the default, its value is treated as
        additional CLI options (e.g. ``--backend claude``).
        """
        extra_flags: list[str] = []
        if model and model not in (self.default_model, "local-openclaw", "ping"):
            extra_flags = model.split()
        return [self.binary] + extra_flags + ["--json"]
