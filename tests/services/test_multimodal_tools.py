from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.services.tools.multimodal.capability import (
    VisionUnsupportedError,
    assert_model_supports_images,
    clear_extra_rules_for_tests,
    register_vision_model,
    supports_vision,
    unregister_vision_model,
)
from app.services.tools.multimodal.content_parts import (
    IMAGE_TOKEN_COST,
    MAX_IMAGES_PER_TURN,
    MultimodalError,
    image_url_part,
    parse_data_uri,
    text_part,
    validate_image_bytes,
    validate_image_uri,
)


def _load_gates():  # reuse pattern for non-package modules if ever needed
    spec = importlib.util.spec_from_file_location("agenthub_gates", ROOT / "benchmarks" / "gates.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agenthub_gates"] = module
    spec.loader.exec_module(module)
    return module


# ── content parts ────────────────────────────────────────────────────────

def test_text_and_image_part_builders() -> None:
    assert text_part("hi") == {"type": "text", "text": "hi"}
    part = image_url_part("data:image/png;base64,aGk=", detail="low")
    assert part["type"] == "image_url"
    assert part["image_url"] == {"url": "data:image/png;base64,aGk=", "detail": "low"}


def test_validate_image_bytes_roundtrip_and_rejections() -> None:
    raw = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    part = validate_image_bytes("image/png", raw)
    mime, decoded = parse_data_uri(part.data_uri)
    assert (mime, decoded) == ("image/png", raw)
    with pytest.raises(MultimodalError):
        validate_image_bytes("image/svg+xml", b"<svg/>")
    with pytest.raises(MultimodalError):
        validate_image_uri("ftp://host/a.png")
    # http(s) URL passes through as an opaque source
    assert validate_image_uri("https://x/y.png").mime == ""


# ── vision capability registry ───────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_capability_rules():
    yield
    clear_extra_rules_for_tests()


def test_default_vision_patterns() -> None:
    assert supports_vision("kimi", "moonshot-v1-8k-vision-preview")
    assert supports_vision("openai", "gpt-4o-mini")
    assert not supports_vision("deepseek", "deepseek-chat")


def test_register_unregister_and_env_override(monkeypatch) -> None:
    assert not supports_vision("qwen", "qwen3-max")
    register_vision_model("dashscope", "qwen3-max")
    assert supports_vision("dashscope", "qwen3-max")
    unregister_vision_model("dashscope", "qwen3-max")
    assert not supports_vision("dashscope", "qwen3-max")

    monkeypatch.setenv("AGENTHUB_VISION_MODELS", "*:glm-4v-plus")
    assert supports_vision("zhipu", "GLM-4V-Plus")


def test_assert_raises_with_degrade_hint() -> None:
    with pytest.raises(VisionUnsupportedError) as err:
        assert_model_supports_images("kimi", "kimi-k2.5")
    assert "image_describe" in str(err.value)  # degrade path named in the error


# ── plugin bridge + modality registration ────────────────────────────────

def test_modality_plugin_registers_image_describe_into_registry() -> None:
    from app.services.tool_registry import ToolDefinition, tool_registry
    from app.services.tools.multimodal import multimodality_plugin
    from app.services.tools.plugin_manager import plugin_manager
    from app.services.tools.plugin_tools import register_hook_tools

    try:
        plugin_manager.pm.register(multimodality_plugin, name="builtin.multimodality-test")
        registered = register_hook_tools(plugin_manager)
        assert registered >= 1

        tool = tool_registry.get("image_describe")
        assert isinstance(tool, ToolDefinition)
        assert tool.category == "multimodal"
        assert tool.risk_level == "L1" and tool.is_concurrency_safe
        names = [parameter.name for parameter in tool.parameters]
        assert {"image_path", "image_url", "image_base64", "prompt", "detail"} <= set(names)
        assert callable(tool.handler)
    finally:
        try:
            plugin_manager.pm.unregister(multimodality_plugin)
        except Exception:  # noqa: BLE001,S110 — teardown best-effort
            pass


def test_import_path_handler_resolution(tmp_path) -> None:
    from app.services.tools.plugin_tools import tool_definition_from_dict

    definition = tool_definition_from_dict({
        "name": "plug_tool_x",
        "description": "d",
        "category": "plugin",
        "handler": "app.services.tools.multimodal.tools:image_describe_handler",
        "parameters": [],
    })
    assert callable(definition.handler)
    with pytest.raises(ValueError):
        tool_definition_from_dict({"name": "bad", "description": "", "parameters": [],
                                   "handler": "no.such.module:attr"})


# ── image_describe handler ───────────────────────────────────────────────

def test_image_describe_requires_a_source() -> None:
    import asyncio

    from app.services.tools.multimodal.tools import image_describe_handler

    result = asyncio.run(image_describe_handler())
    assert not result["success"] and "required" in result["error"]


def test_image_describe_rejects_bad_mime(tmp_path) -> None:
    import asyncio

    from app.services.tools.multimodal.tools import image_describe_handler

    bad = tmp_path / "vector.svg"
    bad.write_text("<svg/>", encoding="utf-8")
    result = asyncio.run(image_describe_handler(image_path=str(bad)))
    assert not result["success"]
    assert "extension" in result["error"] or "unsupported" in result["error"]


def test_image_describe_success_with_stubbed_endpoint(
        tmp_path, monkeypatch) -> None:
    import asyncio

    from app.services.tools.multimodal import tools as mmtools

    png = tmp_path / "tiny.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"pixel" * 16)

    captured: dict[str, object] = {}

    async def fake_call(url, api_key, model, image_part, prompt_text):
        captured.update({"url": url, "model": model})
        reply = ('```json\n{"description": "a tiny png", "objects": ["dot"], '
                 '"dominant_colors": ["white"], "table_present": false}\n```')
        return reply, {"prompt_tokens": 40, "completion_tokens": 12}

    monkeypatch.setenv("AGENTHUB_IMAGE_DESCRIBE_MODEL", "moonshot-v1-8k-vision-preview")
    monkeypatch.setattr(mmtools, "_call_vision_endpoint", fake_call)
    monkeypatch.setattr(mmtools, "_resolve_endpoint",
                        lambda: ("http://gateway/v1/chat/completions", "sk-test"))

    result = asyncio.run(mmtools.image_describe_handler(image_path=str(png)))
    assert result["success"], result
    body = result["result"]
    assert body["description"] == "a tiny png"
    assert body["objects"] == ["dot"]
    meta = result["metadata"]
    assert meta["estimated_image_tokens"] == IMAGE_TOKEN_COST > 0
    assert meta["vision_model"].endswith("vision-preview")
    assert "moonshot-v1-8k-vision-preview" in str(captured["model"])


def test_image_describe_honours_budget_metadata(tmp_path) -> None:
    """The per-turn cap constant is exported for MM-3 and stays sane."""
    assert 0 < MAX_IMAGES_PER_TURN <= 8