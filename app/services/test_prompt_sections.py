from __future__ import annotations

from datetime import datetime

from app.services.prompt_sections import (
    build_collab_section,
    build_date_context,
    build_shared_context,
    build_workspace_context,
)


def test_build_shared_context_is_compact() -> None:
    section = build_shared_context("user: hello")

    assert "共享会话上下文" in section
    assert "user: hello" in section
    assert len(section) < 120


def test_build_collab_section_preserves_empty_state() -> None:
    assert build_collab_section("") == ""
    assert build_collab_section("team notes") == "\n\nteam notes"


def test_build_date_context_is_deterministic_with_now() -> None:
    section = build_date_context(datetime(2026, 7, 18))

    assert "2026年07月18日" in section
    assert "星期六" in section


def test_build_workspace_context_lists_files(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "src").mkdir()

    section = build_workspace_context(tmp_path, max_items=5)

    assert "工作区文件系统" in section
    assert "file_write_batch" in section
    assert "a.txt" in section
    assert "src" in section
