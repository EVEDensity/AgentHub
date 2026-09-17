from __future__ import annotations

from pathlib import Path

from app.services.code_index import CodeIndexService, CodeIndexStore, IncrementalIndexer, extract_symbols


def test_python_ast_extraction_captures_symbols_imports_and_references(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text("import os\n\nclass Service:\n    def run(self, value):\n        return os.getcwd() + value\n", encoding="utf-8")

    result = extract_symbols(source)

    assert result.parse_error == ""
    assert [(item.name, item.kind) for item in result.symbols] == [("Service", "class"), ("run", "function")]
    assert result.imports[0].module == "os"
    assert any(item.name == "value" for item in result.references)


def test_incremental_indexer_updates_only_changed_files_and_removes_deleted(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def first():\n    return 1\n", encoding="utf-8")
    store = CodeIndexStore(tmp_path / ".agenthub" / "index.sqlite3")
    indexer = IncrementalIndexer(tmp_path, store)

    first = indexer.update(["module.py"])
    unchanged = indexer.update(["module.py"])
    source.write_text("def second():\n    return 2\n", encoding="utf-8")
    changed = indexer.update(["module.py"])

    assert first.indexed == 1
    assert unchanged.unchanged == 1
    assert changed.indexed == 1
    assert store.search_symbols("second")[0]["symbol"] == "second"
    assert store.find_symbol("second")[0]["symbol"] == "second"
    assert store.get_definition("second")[0]["line_start"] == 1

    source.unlink()
    deleted = indexer.update(["module.py"])
    assert deleted.removed == 1
    assert store.search_symbols("second") == []
    store.close()


def test_initial_index_builds_a_complete_baseline_before_using_git_diff(tmp_path: Path) -> None:
    (tmp_path / "first.py").write_text("class First:\n    pass\n", encoding="utf-8")
    (tmp_path / "second.ts").write_text("export function second() {}\n", encoding="utf-8")
    store = CodeIndexStore(tmp_path / ".agenthub" / "index.sqlite3")

    report = IncrementalIndexer(tmp_path, store).update()

    assert report.indexed == 2
    assert {item["symbol"] for item in store.search_symbols("s")} >= {"First", "second"}
    store.close()


def test_code_index_failure_returns_none_so_callers_can_fall_back(tmp_path: Path, monkeypatch) -> None:
    service = CodeIndexService(tmp_path, database_path=tmp_path / "blocked" / "index.sqlite3")

    def fail(*_args, **_kwargs):
        raise OSError("index unavailable")

    monkeypatch.setattr("app.services.code_index.service.CodeIndexStore", fail)

    assert service.update(["missing.py"]) is None
    assert service.search_symbols("Service") is None


def test_syntax_error_is_reported_without_blocking_indexer(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(:\n", encoding="utf-8")
    store = CodeIndexStore(tmp_path / ".agenthub" / "index.sqlite3")

    report = IncrementalIndexer(tmp_path, store).update(["broken.py"])

    assert report.failed == 1
    assert store.search_symbols("broken") == []
    store.close()
