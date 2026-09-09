from __future__ import annotations

from app.services.tools.result_storage import ContentBudgetConfig, ResultStorage


def test_result_storage_adds_summary_and_lazy_reference_for_context() -> None:
    storage = ResultStorage(ContentBudgetConfig(max_result_chars=20, max_total_results_chars=20, summary_chars=8))

    result = storage.process({"tool_name": "file_read", "result": "x" * 100})

    context = result["context"]
    assert context["class"] == "structured_summary"
    assert context["deferred"] is True
    assert context["rawChars"] == 100
    assert context["reference"].startswith("tool-result:")
    assert result["result_truncated"] is True

