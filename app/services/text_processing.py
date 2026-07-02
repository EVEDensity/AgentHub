"""Text post-processing: dedup, stripping, normalisation, filtering.

These are stateless (or session-scoped) transformations applied to agent
output BEFORE it is streamed to the frontend or persisted to the database.
"""

from __future__ import annotations

import json
import re

# ═══════════════════════════════════════════════════════════════════════════
# Streaming filter state (per-session)
# ═══════════════════════════════════════════════════════════════════════════

_STREAM_FILTER_STATE: dict[str, dict] = {}


def reset_stream_filter(session_id: str) -> None:
    """Clear per-session streaming filter state (call on stream end/interrupt)."""
    _STREAM_FILTER_STATE.pop(session_id, None)


# ═══════════════════════════════════════════════════════════════════════════
# LaTeX → Unicode
# ═══════════════════════════════════════════════════════════════════════════

def latex_to_unicode(text: str) -> str:
    """Convert common LaTeX math commands to Unicode symbols.

    Models (especially Kimi K2.6) often output LaTeX like ``\\div``, ``\\times``
    which the frontend cannot render.  Map them to proper Unicode glyphs.
    """
    replacements = [
        ("\\textdegree", "°"), ("\\Leftrightarrow", "⇔"), ("\\rightarrow", "→"),
        ("\\leftarrow", "←"), ("\\Rightarrow", "⇒"), ("\\subseteq", "⊆"),
        ("\\notin", "∉"), ("\\subset", "⊂"), ("\\approx", "≈"),
        ("\\equiv", "≡"), ("\\propto", "∝"), ("\\infty", "∞"),
        ("\\ldots", "…"), ("\\cdots", "⋯"), ("\\degree", "°"),
        ("\\angle", "∠"), ("\\triangle", "△"), ("\\forall", "∀"),
        ("\\exists", "∃"), ("\\emptyset", "∅"), ("\\times", "×"),
        ("\\cdot", "·"), ("\\leq", "≤"), ("\\geq", "≥"),
        ("\\neq", "≠"), ("\\sim", "∼"), ("\\sum", "∑"),
        ("\\prod", "∏"), ("\\int", "∫"), ("\\div", "÷"),
        ("\\pm", "±"), ("\\mp", "∓"), ("\\sqrt", "√"),
        ("\\alpha", "α"), ("\\beta", "β"), ("\\gamma", "γ"),
        ("\\delta", "δ"), ("\\epsilon", "ε"), ("\\theta", "θ"),
        ("\\lambda", "λ"), ("\\mu", "μ"), ("\\pi", "π"),
        ("\\sigma", "σ"), ("\\omega", "ω"), ("\\land", "∧"),
        ("\\lor", "∨"), ("\\neg", "¬"), ("\\cup", "∪"),
        ("\\cap", "∩"), ("\\to", "→"), ("\\in", "∈"),
        ("\\%", "%"), ("\\_", "_"), ("\\&", "&"),
        ("\\#", "#"),
    ]
    for latex, uni in replacements:
        text = text.replace(latex, uni)
    return text


# ═══════════════════════════════════════════════════════════════════════════
# Deduplication
# ═══════════════════════════════════════════════════════════════════════════

def remove_repeated_text(text: str) -> str:
    """Remove duplicate content from model output.

    Handles: full-text mirroring, consecutive duplicate lines,
    adjacent repeated phrases, and non-adjacent repeated paragraphs.
    """
    if not text:
        return text

    n = len(text)
    if n >= 60:
        best_ratio = 0.0
        best_left = text
        start = max(n // 3, 30)
        end = min(n * 2 // 3, n - 30)
        for mid in range(start, end, 4):
            left = text[:mid].strip()
            right = text[mid:].strip()
            if not left or not right:
                continue
            if left == right:
                return left
            if len(left) < len(right) and len(left) >= len(right) * 0.8 and left in right:
                return right
            if len(right) < len(left) and len(right) >= len(left) * 0.8 and right in left:
                return left
            min_len = min(len(left), len(right))
            match_len = 0
            for j in range(min_len):
                if left[j] == right[j]:
                    match_len += 1
                else:
                    break
            ratio = match_len / max(len(left), len(right))
            if ratio > best_ratio:
                best_ratio = ratio
                best_left = left
        if best_ratio > 0.95:
            text = best_left

    # 1. Remove consecutive duplicate lines
    lines = text.split('\n')
    unique_lines = []
    prev_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped and stripped != prev_line:
            unique_lines.append(line)
            prev_line = stripped
        elif not stripped:
            unique_lines.append(line)
    text = '\n'.join(unique_lines)

    # 2. Remove adjacent repeated phrases (12-80 char windows)
    text = _remove_repeated_phrases(text)

    # 3. Remove non-adjacent repeated paragraphs (30+ chars)
    paragraphs = re.split(r'\n\n+', text)
    seen: set[str] = set()
    unique_paras: list[str] = []
    for p in paragraphs:
        stripped = p.strip()
        if len(stripped) >= 30:
            if stripped in seen:
                continue
            seen.add(stripped)
        unique_paras.append(p)
    text = '\n\n'.join(unique_paras)

    return text


def _remove_repeated_phrases(text: str) -> str:
    """Detect and remove consecutively repeated phrases (12+ chars)."""
    n = len(text)
    if n < 24:
        return text
    for window in range(min(n // 2, 80), 11, -1):
        i = 0
        while i + window * 2 <= n:
            phrase = text[i:i + window]
            if text[i + window:i + window * 2] == phrase:
                text = text[:i + window] + text[i + window * 2:]
                n = len(text)
                continue
            i += 1
    return text


# ═══════════════════════════════════════════════════════════════════════════
# Model-specific stripping
# ═══════════════════════════════════════════════════════════════════════════

def strip_kimi_thinking(text: str) -> str:
    """Clean up Kimi K2.6 thinking markers that leak into the reply."""
    parts = re.split(r"【正式回复】\s*", text)
    if len(parts) > 1 and parts[-1].strip():
        return parts[-1].strip()
    text = re.sub(r"💭\s*", "", text)
    text = re.sub(r"【思考分析】已完成\s*\(\d+字\)", "", text)
    text = re.sub(
        r"^(回复策略：.*|思考内容：.*|注意：用户消息.*|核心需求是.*)\n?",
        "", text, flags=re.MULTILINE,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> tags injected by adapters for reasoning_content.

    These tags drive the frontend ThinkingPanel but pollute saved messages.
    """
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def strip_codegen_prefix(text: str) -> str:
    """Remove decorative prefixes models sometimes add before JSON."""
    return re.sub(r"^(【[^】]*】\s*)+", "", text.strip())


# ═══════════════════════════════════════════════════════════════════════════
# Detection helpers
# ═══════════════════════════════════════════════════════════════════════════

def is_codegen_json_response(text: str) -> bool:
    """Check if text is a CodeGen-style JSON file manifest."""
    try:
        data = json.loads(text)
        return isinstance(data, dict) and isinstance(data.get("files"), list)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False


def is_code_request(text: str) -> bool:
    """Check whether the user is asking for code generation."""
    keywords = [
        "生成", "创建", "实现", "写", "编写", "修改", "添加", "改", "开发",
        "code", "fastapi", "react", "api", "页面", "组件", "路由", "接口",
        "帮我做", "帮我写", "做一个", "写一个", "改一下", "加一个",
    ]
    return any(w in text.lower() for w in keywords)


# ═══════════════════════════════════════════════════════════════════════════
# Streaming chunk filter
# ═══════════════════════════════════════════════════════════════════════════

def filter_streaming_chunk(session_id: str, chunk: str) -> str:
    """Apply safe incremental filters to a streaming chunk.

    MUST NOT destroy <think>...</think> blocks — the frontend ThinkingPanel
    relies on them.
    """
    if not chunk:
        return chunk

    chunk = chunk.replace("\\`", "`").replace('\\"', '"').replace("\\'", "'")
    chunk = latex_to_unicode(chunk)
    chunk = chunk.replace("【正式回复】\n", "").replace("【正式回复】", "")
    chunk = re.sub(r"【思考分析】已完成\s*\(\d+字\)", "", chunk)
    chunk = re.sub(r"【思考分析】", "", chunk)

    return chunk


# ═══════════════════════════════════════════════════════════════════════════
# Top-level normalisation entry point
# ═══════════════════════════════════════════════════════════════════════════

def normalize_agent_output(agent_id: str, model_output: str, original: str) -> str:
    """Normalize agent model output before streaming to the frontend."""
    if agent_id == "CodeGen":
        is_codegen_mock = (
            not model_output
            or model_output.startswith("本地 Mock 模型响应")
        )
        is_codegen_failure = model_output.startswith("模型调用失败")

        if not is_codegen_mock and not is_codegen_failure:
            cleaned = strip_codegen_prefix(model_output)
            if is_codegen_json_response(cleaned) and cleaned != model_output:
                model_output = cleaned

    # Generic normalisation for all agents
    model_output = strip_think_tags(model_output)
    model_output = strip_kimi_thinking(model_output)
    model_output = remove_repeated_text(model_output)

    return model_output
