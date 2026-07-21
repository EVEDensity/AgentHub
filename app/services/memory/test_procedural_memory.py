from __future__ import annotations

from app.services.memory.models import CognitiveMemoryType, MemoryScope
from app.services.memory.procedural_memory import records_from_tools
from app.services.tool_registry import ToolDefinition, ToolExample, ToolParameter


def test_tool_definition_projects_to_procedural_memory() -> None:
    tool = ToolDefinition(
        name="deploy_preview",
        description="Create a deployment preview",
        category="integration",
        parameters=[ToolParameter("environment", "string", True, "Target environment")],
        return_type="object",
        examples=[ToolExample("preview it", {"environment": "staging"})],
        risk_level="L2",
    )
    record = records_from_tools([tool])[0]
    assert record.memory_type == CognitiveMemoryType.PROCEDURAL.value
    assert record.scope == MemoryScope.GLOBAL.value
    assert record.kind == "tool"
    assert record.source == "tool-registry:deploy_preview"
    assert record.risk_level == "L2"
    assert len(record.content_hash) == 24
