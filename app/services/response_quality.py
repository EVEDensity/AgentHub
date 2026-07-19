from __future__ import annotations

import re


def estimate_response_quality(request: str, response: str) -> float:
    """Return a cheap operational quality proxy in the range 0..1.

    This is intentionally not presented as semantic evaluation. It detects
    empty/error responses, excessive repetition, and obviously incomplete
    answers so token reductions can be correlated with regressions.
    """
    clean = response.strip()
    if not clean:
        return 0.0
    error_markers = ("模型调用异常", "模型调用失败", "traceback", "internal server error")
    if any(marker in clean.lower() for marker in error_markers):
        return 0.15

    score = 0.45
    if len(clean) >= min(80, max(20, len(request) // 2)):
        score += 0.2
    lines = [re.sub(r"\s+", " ", line.strip()).lower() for line in clean.splitlines() if line.strip()]
    unique_ratio = len(set(lines)) / max(1, len(lines))
    score += 0.2 * unique_ratio
    if clean[-1] in ".!?。！？`}]）)":
        score += 0.15
    return round(min(1.0, score), 4)
