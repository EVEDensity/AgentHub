from __future__ import annotations

import logging
from collections.abc import Sequence

from app.services.capability_tools import CapabilityToolBinding
from app.services.harness_checkpoint import HarnessExecutionContext
from app.services.mcp_tool_adapter import (
    MCPAuditPort,
    MCPToolAuditEvent,
    StatelessMCPClient,
    build_mcp_capability_binding,
)

from .config import MCPBindingManifest

logger = logging.getLogger(__name__)


class LoggingMCPAuditPort(MCPAuditPort):
    """Emit content-free MCP call metadata through the process logger."""

    async def record(self, event: MCPToolAuditEvent) -> None:
        logger.info(
            "mcp_call mission_id=%s work_unit_id=%s attempt=%d capability=%s "
            "tool=%s success=%s duration_ms=%d error_type=%s",
            event.context.execution.mission_id,
            event.context.execution.work_unit_id,
            event.context.execution.attempt,
            event.context.capability,
            event.tool_name,
            event.success,
            event.duration_ms,
            event.error_type or "",
        )


class PerAttemptMCPBindingFactory:
    """Bind a credential-free manifest to one fenced execution context."""

    def __init__(
        self,
        client: StatelessMCPClient,
        manifest: MCPBindingManifest,
    ) -> None:
        self._client = client
        self._manifest = manifest

    def build(
        self,
        execution: HarnessExecutionContext,
    ) -> Sequence[CapabilityToolBinding]:
        return tuple(
            build_mcp_capability_binding(
                self._client,
                capability=binding.capability,
                function_name=binding.function_name,
                execution=execution,
                description=binding.description,
                parameters=binding.parameters,
            )
            for binding in self._manifest.bindings
        )


__all__ = ["LoggingMCPAuditPort", "PerAttemptMCPBindingFactory"]
