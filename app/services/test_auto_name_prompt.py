from __future__ import annotations

from app.services.auto_name_prompt import build_auto_name_prompt, extract_local_title


def test_build_auto_name_prompt_is_compact() -> None:
    prompt = build_auto_name_prompt([
        {"sender": "user", "content": "请帮我实现一个飞书消息同步功能，支持多租户和增量更新。"},
        {"sender": "agent", "content": "可以，下面我先拆一下模块和数据流。"},
    ])

    assert "标题：" in prompt
    assert "飞书消息同步功能" in prompt
    assert len(prompt) < 260


def test_extract_local_title_handles_common_intents() -> None:
    assert extract_local_title("请帮我实现一个订单导出页面") == "订单导出页面"
    assert extract_local_title("Generate a FastAPI health route") == "生成健康检查路由"
