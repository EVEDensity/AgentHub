from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from app.services.tools.change_set import apply_change_set_handler
from app.services.tools.file_ops import file_edit_handler, file_patch_handler, file_write_handler
from app.services.workspace_context import workspace_root_override


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_change_set_writes_multiple_files_with_full_hashes(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    first.write_text("value = 1\n", encoding="utf-8")
    changes = [
        {"path": "first.py", "content": "value = 2\n", "expected_sha256": _sha(first)},
        {"path": "new.py", "content": "print('new')\n", "expected_sha256": ""},
    ]
    with workspace_root_override(tmp_path):
        result = asyncio.run(apply_change_set_handler(changes))
    assert result["success"] is True
    assert first.read_text(encoding="utf-8") == "value = 2\n"
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "print('new')\n"


def test_change_set_rejects_external_change_before_any_write(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("value = 1\n", encoding="utf-8")
    second.write_text("value = 2\n", encoding="utf-8")
    expected = _sha(first)
    first.write_text("external\n", encoding="utf-8")
    with workspace_root_override(tmp_path):
        result = asyncio.run(apply_change_set_handler([
            {"path": "first.py", "content": "agent\n", "expected_sha256": expected},
            {"path": "second.py", "content": "agent\n", "expected_sha256": _sha(second)},
        ]))
    assert result["success"] is False
    assert "外部修改" in result["error"]
    assert second.read_text(encoding="utf-8") == "value = 2\n"


def test_change_set_requires_hash_field(tmp_path: Path) -> None:
    with workspace_root_override(tmp_path):
        result = asyncio.run(apply_change_set_handler([
            {"path": "new.py", "content": "pass\n"},
        ]))
    assert result["success"] is False
    assert "expected_sha256" in result["error"]


def test_file_mutations_require_full_expected_hash_and_reject_conflict(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    with workspace_root_override(tmp_path):
        missing = asyncio.run(file_write_handler("module.py", "value = 2\n"))
        wrong = asyncio.run(file_edit_handler("module.py", "value = 1", "value = 2", expected_sha256="0" * 64))
    assert missing["success"] is False and "expected_sha256" in missing["error"]
    assert wrong["success"] is False and wrong["error_type"] == "conflict"
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_file_write_append_preserves_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    with workspace_root_override(tmp_path):
        result = asyncio.run(file_write_handler("notes.txt", "after", mode="append", expected_sha256=_sha(target)))
    assert result["success"] is True
    assert target.read_text(encoding="utf-8") == "before\nafter"


def test_file_patch_rejects_stale_context(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("one\ntwo\n", encoding="utf-8")
    with workspace_root_override(tmp_path):
        result = asyncio.run(file_patch_handler(
            "module.py",
            "@@ -1,2 +1,2 @@\n-one\n+changed\n two\n",
            expected_sha256=_sha(target),
        ))
    assert result["success"] is True
    target.write_text("external\ntwo\n", encoding="utf-8")
    with workspace_root_override(tmp_path):
        stale = asyncio.run(file_patch_handler(
            "module.py",
            "@@ -1,2 +1,2 @@\n-one\n+changed\n two\n",
            expected_sha256=_sha(target),
        ))
    assert stale["success"] is False
