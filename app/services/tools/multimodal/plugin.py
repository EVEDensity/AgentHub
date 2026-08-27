"""Pluggable modality tool plugin — base class + built-in registration.

Every multimodal tool ships as a :class:`ModalityToolPlugin` subclass. A
plugin declares its tools via :meth:`tool_definitions` (returning
``ToolDefinition`` objects); the shared pluggy ``register_tools()`` hook
serialises them, and the generic bridge in
:mod:`app.services.tools.plugin_tools` feeds them into the global
``tool_registry`` at startup.

Why a plugin rather than appending to definitions.py:
* the multimodal layer is independently versioned/removable — deleting this
  package removes every modality tool without touching core files;
* third parties add their own modality (audio, video) by subclassing and
  registering through the same three channels (builtin / entry-point /
  PLUGINS_PATH) with no core edits.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.tool_registry import ToolDefinition
from app.services.tools.plugin_spec import hookimpl

logger = logging.getLogger("agenthub.tools.multimodal")


class ModalityToolPlugin:
    """Base class for multimodal tool plugins.

    Subclasses override :meth:`tool_definitions`. The pluggy decorator is
    applied here once so subclasses only supply data.
    """

    name: str = "modality"

    def tool_definitions(self) -> list[ToolDefinition]:
        """Return the ToolDefinition objects owned by this plugin."""
        return []

    @hookimpl
    def register_tools(self) -> list[dict[str, Any]] | None:
        """Serialise owned definitions for the pluggy tool-registration hook."""
        definitions = self.tool_definitions()
        if not definitions:
            return None
        payload = []
        for definition in definitions:
            item: dict[str, Any] = {
                "name": definition.name,
                "description": definition.description,
                "category": definition.category,
                "parameters": [
                    {
                        "name": parameter.name,
                        "type": parameter.type,
                        "required": parameter.required,
                        "description": parameter.description,
                        **({"default": parameter.default} if parameter.default not in (None, "") else {}),
                    }
                    for parameter in definition.parameters
                ],
                "risk_level": definition.risk_level,
                "is_concurrency_safe": definition.is_concurrency_safe,
                # callables travel in-process; import-path strings survive
                # entry-point/PLUGINS_PATH loading where pickling is not used.
                "handler": definition.handler,
            }
            payload.append(item)
        logger.info("modality plugin %s provides %d tool(s): %s",
                    self.name, len(payload), [item["name"] for item in payload])
        return payload


class MultimodalityPlugin(ModalityToolPlugin):
    """Built-in image-modality plugin (currently: image_describe)."""

    name = "multimodality"

    def tool_definitions(self) -> list[ToolDefinition]:
        from app.services.tool_registry import ToolExample, ToolParameter

        from .tools import IMAGE_DESCRIBE_PARAMETERS, image_describe_handler

        return [ToolDefinition(
            name="image_describe",
            description=(
                "看图工具：把工作区路径 / URL / base64 的图片交给视觉子模型，"
                "返回结构化文字描述（对象、图内文字、主色调、是否含表格）。"
                "即使当前对话模型不支持视觉也可使用；图片原文不会进入对话上下文。"
            ),
            category="multimodal",
            parameters=[
                ToolParameter(name=parameter["name"], type=parameter["type"],
                              required=parameter["required"],
                              description=parameter["description"])
                for parameter in IMAGE_DESCRIBE_PARAMETERS
            ],
            return_type=(
                '{"success": bool, "result": {"description": "str", '
                '"objects": ["str"], "text_in_image": "str", '
                '"dominant_colors": ["str"], "table_present": bool}, '
                '"metadata": {"vision_model": "str", "estimated_image_tokens": int}}'
            ),
            examples=[
                ToolExample(
                    user_question="这张 logo 里有什么？",
                    parameters={"image_path": "frontend/public/logo.png"},
                ),
                ToolExample(
                    user_question="分析这张截图里的表格内容",
                    parameters={"image_url": "https://example.com/report.png",
                                "prompt": "重点提取表格数据"},
                ),
            ],
            risk_level="L1",
            handler=image_describe_handler,
            is_concurrency_safe=True,
        )]


# ── Module-level singletons ──────────────────────────────────────────────

multimodality_plugin = MultimodalityPlugin()
