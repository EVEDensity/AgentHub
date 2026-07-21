from __future__ import annotations

from app.services.response_quality import estimate_response_quality


def test_response_quality_penalizes_errors_and_repetition() -> None:
    good = estimate_response_quality("implement auth", "Implemented authentication and added tests.")
    error = estimate_response_quality("implement auth", "模型调用异常：timeout")
    repeated = estimate_response_quality("implement auth", "same\nsame\nsame")
    assert good > error
    assert good > repeated
