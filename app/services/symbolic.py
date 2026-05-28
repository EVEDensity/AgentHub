from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from typing import Any

# ── Fidelity closed-loop thresholds (v3.1 §3.3) ──────────────────────
FIDELITY_HIGH = 0.85       # ≥ 0.85: normal pass-through
FIDELITY_WARN = 0.70       # 0.70–0.85: continue but warn
FIDELITY_LOW = 0.55        # 0.55–0.70: auto-pull extended context
                            # < 0.55: BLOCK — re-distill or human confirm

PROTOCOL_VERSION = "symbolic-v1"


def fidelity_grade(score: float) -> str:
    """Return the fidelity grade label for a given score."""
    if score >= FIDELITY_HIGH:
        return "high"
    if score >= FIDELITY_WARN:
        return "warn"
    if score >= FIDELITY_LOW:
        return "low"
    return "block"


def fidelity_action(score: float) -> dict[str, Any]:
    """Return the prescribed system action for a fidelity score (§3.3)."""
    if score >= FIDELITY_HIGH:
        return {"action": "pass", "block": False, "warn": False, "enrich": False}
    if score >= FIDELITY_WARN:
        return {"action": "warn", "block": False, "warn": True, "enrich": False}
    if score >= FIDELITY_LOW:
        return {"action": "enrich", "block": False, "warn": True, "enrich": True}
    return {"action": "block", "block": True, "warn": True, "enrich": True}


def compute_fidelity(
    original_text: str,
    summary_text: str,
    *,
    intent_type: str = "general",
    has_code: bool = False,
) -> float:
    """Compute a multi-dimensional fidelity score.

    Unlike the old compression-ratio heuristic, this evaluates:
      - Semantic coverage: how much of the original intent is captured
      - Structural preservation: code blocks, lists, key entities retained
      - Information density: non-trivial content ratio
      - Length proportionality: appropriate compression for the intent type

    Returns a float in [0.0, 1.0].
    """
    if not original_text or not summary_text:
        return 0.0

    orig = original_text.strip()
    summ = summary_text.strip()

    # ── 1. Length proportionality (30%) ───────────────────────────────
    # Different intent types expect different compression ratios.
    # Code generation should preserve more; general chat can compress more.
    ratio = min(len(summ) / max(len(orig), 1), 1.0)
    if intent_type in ("code_generation", "code_review"):
        target = 0.55  # code needs substantial preservation
    elif intent_type in ("architecture", "deployment"):
        target = 0.35
    else:
        target = 0.30  # general chat can be heavily compressed
    length_score = 1.0 - min(abs(ratio - target) / target, 1.0) * 0.5
    length_score = max(0.0, length_score)

    # ── 2. Key entity preservation (25%) ─────────────────────────────
    # Extract identifiers, paths, numbers, and capitalized terms from both
    def _entities(text: str) -> set[str]:
        ent: set[str] = set()
        # Code identifiers
        for m in re.finditer(r"\b[a-zA-Z_]\w{2,}\b", text):
            ent.add(m.group().lower())
        # File paths
        for m in re.finditer(r"[\w./-]+\.\w{2,6}", text):
            ent.add(m.group().lower())
        # Numbers (quantities matter)
        for m in re.finditer(r"\b\d+\b", text):
            ent.add(m.group())
        return ent

    orig_ent = _entities(orig)
    summ_ent = _entities(summ)
    if orig_ent:
        entity_score = len(orig_ent & summ_ent) / len(orig_ent)
    else:
        entity_score = 0.85  # no entities to lose — acceptable

    # ── 3. Structural integrity (25%) ─────────────────────────────────
    # Code blocks, lists, headings should survive distillation
    def _structural_markers(text: str) -> int:
        markers = 0
        markers += len(re.findall(r"```", text)) // 2  # code fences
        markers += len(re.findall(r"^[-*•]\s", text, re.MULTILINE))  # list items
        markers += len(re.findall(r"^#+\s", text, re.MULTILINE))  # headings
        markers += len(re.findall(r"^\d+[.)]\s", text, re.MULTILINE))  # numbered lists
        return markers

    orig_struct = _structural_markers(orig)
    summ_struct = _structural_markers(summ)
    if orig_struct > 0:
        struct_score = min(summ_struct / orig_struct, 1.0)
    else:
        struct_score = 0.90

    # ── 4. Information density (20%) ─────────────────────────────────
    # Penalise boilerplate, reward concrete actionable content
    boilerplate_patterns = [
        r"好的[，,]我来",
        r"让我.{0,20}分析",
        r"作为.{0,30}专家",
        r"我将.{0,30}(处理|回答|解决)",
        r"以下是.{0,20}(分析|建议|方案)",
    ]
    bp_count = sum(1 for p in boilerplate_patterns if re.search(p, summ))
    density_score = max(0.3, 1.0 - bp_count * 0.18)

    # ── Weighted aggregate ────────────────────────────────────────────
    raw = (
        0.30 * length_score
        + 0.25 * entity_score
        + 0.25 * struct_score
        + 0.20 * density_score
    )
    # Add small bonus for code-bearing responses (harder to distill well)
    if has_code:
        raw = raw * 0.92 + 0.04

    return round(max(0.05, min(0.99, raw)), 3)


def generate_symbolic_message(
    original_text: str,
    task_type: str,
    session_id: str,
    *,
    sender_role: str = "",
    receiver_role: str = "",
    intent_type: str = "",
    risk_level: str = "L1",
    write_scope: list[str] | None = None,
    requires_human_confirm: bool = False,
    ttl_minutes: int = 60,
) -> dict[str, Any]:
    """Generate a v3.1 protocol-compliant symbolic message (§3.2).

    Includes all engineering fields: protocol_version, task_id, sender_role,
    receiver_role, intent_type, risk_level, write_scope, requires_human_confirm,
    and expires_at — plus the core symbolic distillation fields from v3.0.
    """
    clean = " ".join(original_text.split())
    limit = 100 if task_type == "code" else 60 if task_type == "document" else 42
    core = clean[:limit] + ("..." if len(clean) > limit else "")
    digest = hashlib.sha256(original_text.encode("utf-8")).hexdigest()

    # Multi-dimensional fidelity instead of compression-ratio heuristic
    fidelity = compute_fidelity(
        original_text,
        core,
        intent_type=intent_type or task_type,
        has_code=("```" in original_text),
    )

    # Confidence decays for very short inputs (insufficient signal)
    confidence = round(min(0.98, 0.70 + 0.006 * len(clean)), 2)

    return {
        # ── v3.1 protocol fields (§3.2) ─────────────────────────────
        "protocol_version": PROTOCOL_VERSION,
        "session_id": session_id,
        "task_id": str(uuid.uuid4()),
        "sender_role": sender_role,
        "receiver_role": receiver_role,
        "intent_type": intent_type or task_type,
        "risk_level": risk_level,
        "write_scope": write_scope or [],
        "requires_human_confirm": requires_human_confirm,
        "expires_at": _format_expiry(ttl_minutes),
        # ── v3.0 core distillation fields (§3.1) ────────────────────
        "task_fingerprint_id": str(uuid.uuid4()),
        "core_summary": core,
        "extended_summaries": [
            {
                "id": "ext_1",
                "text": original_text[:200],
                "vector_idx": f"vec_{digest[:10]}",
            }
        ],
        "key_params": {"task_type": task_type, "intent": intent_type or task_type},
        "knowledge_vector_idx": [f"vec_main_{digest[:12]}"],
        "confidence": confidence,
        "fidelity_score": fidelity,
        "distillation_model": "agenthub-fidelity-v1",
        "source_trace": {
            "original_vector_idx": f"vec_original_{digest[:12]}",
            "audit_hash": digest,
        },
    }


def _format_expiry(ttl_minutes: int) -> str:
    """ISO-8601 expiry timestamp for the symbolic message."""
    import datetime as _dt
    exp = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=ttl_minutes)
    return exp.isoformat()


def public_symbolic(symbolic: dict[str, Any]) -> dict[str, Any]:
    """Return the public-facing subset (strip internal trace data)."""
    return {key: value for key, value in symbolic.items() if key != "source_trace"}


# ── Re-distillation engine (§3.3 < 0.55 path) ──────────────────────────

def requires_redistill(score: float) -> bool:
    """True when fidelity is too low and the message must be re-distilled."""
    return score < FIDELITY_LOW


def build_redistill_prompt(original_text: str, failed_summary: str, failed_score: float) -> str:
    """Construct a prompt that asks the Orchestrator to re-distill a low-fidelity summary.

    The prompt explains what was insufficient and requests a structured re-summary
    with higher information density and entity preservation.
    """
    return (
        "【符号蒸馏保真度不足 — 需要重新提炼】\n\n"
        f"原始文本（{len(original_text)}字符）：\n{original_text[:3000]}\n\n"
        f"上次提炼结果（保真度 {failed_score:.2f}，低于阈值 {FIDELITY_LOW}）：\n{failed_summary[:1000]}\n\n"
        "## 重新提炼要求\n"
        "1. 保留原始文本中的所有关键实体（文件名、函数名、参数、数字）\n"
        "2. 保留代码块结构和编号列表\n"
        "3. 去除寒暄和冗余修饰，但保留技术细节\n"
        "4. 提炼后的文本应具备独立可读性，不需要回看原文\n"
        "5. 如果原文包含可执行指令或配置，必须完整保留\n\n"
        "请输出重新提炼的结构化摘要："
    )


# ── Extended context enrichment (§3.3 0.55–0.70 path) ─────────────────

def build_enrichment_prompt(original_text: str, current_summary: str, score: float) -> str:
    """Build a prompt to pull extended context when fidelity is marginal (0.55–0.70).

    Instead of blocking, we enrich the summary with additional vector-indexed
    context and re-evaluate.
    """
    return (
        "【符号蒸馏上下文补充 — 保真度不足需要扩展】\n\n"
        f"当前摘要保真度：{score:.2f}（阈值 {FIDELITY_HIGH}）\n"
        f"当前摘要：{current_summary[:500]}\n\n"
        "请从以下原始上下文中提取被遗漏的关键信息，补充到摘要中：\n"
        f"{original_text[:3000]}\n\n"
        "补充规则：\n"
        "1. 只补充技术事实和可操作信息，不加评论\n"
        "2. 优先补充：文件路径、API端点、配置参数、错误码、版本号\n"
        "3. 保持原有摘要结构，追加「补充上下文」小节\n"
    )


# ── Fidelity evaluation for CollaborationContext contributions ─────────

def evaluate_contribution(
    user_content: str,
    agent_response: str,
    agent_id: str,
    domain: str,
) -> dict[str, Any]:
    """Evaluate an agent's contribution fidelity in a multi-agent context.

    Returns a dict with fidelity score, grade, prescribed action, and
    whether the contribution is acceptable for downstream consumption.
    """
    score = compute_fidelity(
        user_content,
        agent_response,
        intent_type=domain,
        has_code=("```" in agent_response),
    )
    grade = fidelity_grade(score)
    action = fidelity_action(score)
    return {
        "agent_id": agent_id,
        "domain": domain,
        "fidelity_score": score,
        "grade": grade,
        "action": action,
        "acceptable": score >= FIDELITY_LOW,
    }
