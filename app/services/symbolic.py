from __future__ import annotations

import hashlib
import uuid
from typing import Any

# ── Symbolic Message Protocol (v3.1 §3.2) ──────────────────────────────
# Multi-agent communication via structured symbolic messages.
# Each message carries a fingerprint of the original content so downstream
# agents can verify provenance without re-reading the full text.

PROTOCOL_VERSION = "symbolic-v1"


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
        "fidelity_score": 0.0,  # retained for backward compatibility; no longer computed
        "distillation_model": "agenthub-symbolic-v2",
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
