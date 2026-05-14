from __future__ import annotations

import hashlib
import uuid


def generate_symbolic_message(original_text: str, task_type: str, session_id: str) -> dict:
    clean = " ".join(original_text.split())
    limit = 100 if task_type == "code" else 60 if task_type == "document" else 42
    core = clean[:limit] + ("..." if len(clean) > limit else "")
    digest = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
    fidelity = round(min(0.99, max(0.72, len(core) / max(len(original_text), 1) + 0.35)), 2)
    return {
        "task_fingerprint_id": str(uuid.uuid4()),
        "session_id": session_id,
        "core_summary": core,
        "extended_summaries": [{"id": "ext_1", "text": original_text[:30], "vector_idx": f"vec_{digest[:10]}"}],
        "key_params": {"task_type": task_type},
        "knowledge_vector_idx": [f"vec_main_{digest[:12]}"],
        "confidence": 0.95,
        "fidelity_score": fidelity,
        "distillation_model": "local-rule-summarizer-v1",
        "source_trace": {"original_vector_idx": f"vec_original_{digest[:12]}", "audit_hash": digest},
    }


def public_symbolic(symbolic: dict) -> dict:
    return {key: value for key, value in symbolic.items() if key != "source_trace"}
