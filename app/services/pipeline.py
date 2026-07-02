from __future__ import annotations

import json
import logging

from app.db.session import afetch_one
from app.services.git_service import git_service

logger = logging.getLogger("agenthub.pipeline")


async def run_post_agent_pipeline(session_id: str, agent_id: str) -> dict | None:
    """Run post-agent hooks after an agent completes.

    Checks ``system_config`` for enabled hooks:

    * ``auto_commit`` — if ``true``, commit any changed files via git.
    * ``auto_review`` — if ``true``, capture the diff for downstream review.

    Returns a dict with keys describing what ran, or ``None`` if pipeline
    is not configured.
    """
    # ── Load pipeline config from system_config ──────────────────────
    cfg = await afetch_one(
        "SELECT value FROM system_config WHERE key='pipeline_config'"
    )
    if not cfg:
        return None

    try:
        options = json.loads(cfg["value"])
    except (json.JSONDecodeError, TypeError):
        logger.warning("pipeline_config is not valid JSON, skipping")
        return None

    if not isinstance(options, dict):
        return None

    result: dict = {}

    # ── Auto-commit ──────────────────────────────────────────────────
    if options.get("auto_commit"):
        try:
            git_service.ensure_repo()
            commit_result = git_service.commit(
                f"AgentHub: auto commit after {agent_id} (session {session_id})",
            )
            if commit_result.get("commit_hash"):
                result["commit"] = commit_result
                logger.info(
                    "pipeline auto_commit: session=%s agent=%s hash=%s",
                    session_id, agent_id, commit_result["commit_hash"],
                )
        except Exception as exc:
            logger.warning("pipeline auto_commit failed: %s", exc)

    # ── Auto-review (capture diff) ───────────────────────────────────
    if options.get("auto_review"):
        try:
            diff_output = git_service.diff()
            diff_text = diff_output.get("diff", "")
            if diff_text.strip():
                result["review_triggered"] = True
                result["diff_preview"] = diff_text[:5000]  # truncated for context
                logger.info(
                    "pipeline auto_review: session=%s agent=%s diff_len=%d",
                    session_id, agent_id, len(diff_text),
                )
        except Exception as exc:
            logger.warning("pipeline auto_review failed: %s", exc)

    return result if result else None
