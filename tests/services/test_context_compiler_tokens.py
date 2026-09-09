from __future__ import annotations

from pathlib import Path

from app.services.context_compiler import ContextBudget, ContextCompiler
from app.services.token_budget import count_tokens


def test_token_budget_auto_compresses_overflow_and_keeps_manifest_valid(tmp_path: Path) -> None:
    compiler = ContextCompiler(
        tmp_path,
        provider="openai",
        model="gpt-4o",
        budget=ContextBudget(total_chars=8_000, reserved_output_chars=0, total_tokens=800),
    )

    manifest = compiler.compile(
        current="修复登录测试失败。" * 20,
        conversation="历史对话与工具输出。" * 30,
        project="项目指令。" * 20,
        tool_results=[
            {"name": "file_read", "call_id": "call-1", "success": True, "result": "源码内容\n" * 300},
        ],
    )

    assert manifest.compression_triggered is True
    assert manifest.tokenizer_backend == "tiktoken"
    assert manifest.estimated_tokens <= manifest.token_budget
    assert count_tokens(manifest.render(), "openai", "gpt-4o") <= manifest.token_budget
    assert manifest.to_dict()["compressionTriggered"] is True
    assert any(source.kind == "tool_summary" for source in manifest.sources)


def test_context_compiler_can_force_compression_without_overflow(tmp_path: Path) -> None:
    compiler = ContextCompiler(
        tmp_path,
        provider="openai",
        model="gpt-4o",
        token_budget=800,
    )

    manifest = compiler.compile(current="short request", conversation="details " * 10, force_compress=True)

    assert manifest.compression_triggered is True
    assert manifest.estimated_tokens <= 800


def test_context_length_error_uses_the_same_compression_trigger(tmp_path: Path) -> None:
    compiler = ContextCompiler(tmp_path, provider="openai", model="gpt-4o", token_budget=800)

    manifest = compiler.compile(
        conversation="large history " * 100,
        context_error="provider returned context_length_exceeded",
    )

    assert manifest.compression_triggered is True
