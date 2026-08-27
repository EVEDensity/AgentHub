"""Multimodal tool layer — pluggable image/vision capability for agents.

Public surface:
* content-parts model + policy (:mod:`content_parts`)
* vision capability registry (:mod:`capability`)
* pluggable tools (:mod:`tools` — image_describe)
* :class:`ModalityToolPlugin` base + built-in :data:`multimodality_plugin`
  (:mod:`plugin`) — installed via plugin_manager, registered into
  tool_registry through the generic ``register_tools()`` bridge.
"""

from app.services.tools.multimodal.capability import (
    VisionUnsupportedError,
    assert_model_supports_images,
    register_vision_model,
    supports_vision,
    unregister_vision_model,
)
from app.services.tools.multimodal.content_parts import (
    IMAGE_TOKEN_COST,
    MAX_IMAGES_PER_TURN,
    MultimodalError,
    assert_turn_limits,
    image_url_part,
    text_part,
    validate_image_uri,
)
from app.services.tools.multimodal.plugin import (
    ModalityToolPlugin,
    multimodality_plugin,
)

__all__ = [
    "IMAGE_TOKEN_COST",
    "MAX_IMAGES_PER_TURN",
    "ModalityToolPlugin",
    "MultimodalError",
    "VisionUnsupportedError",
    "assert_model_supports_images",
    "assert_turn_limits",
    "image_url_part",
    "multimodality_plugin",
    "register_vision_model",
    "supports_vision",
    "text_part",
    "unregister_vision_model",
    "validate_image_uri",
]