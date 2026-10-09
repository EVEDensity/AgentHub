"""Desktop private recovery binding; durable lifecycle remains Mission Control's."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from app.services.model_port import build_function_tool_schemas
from app.services.recovery_image import ResumeImageError
from app.services.recovery_journal import RecoveryCheckpointJournal, restore_resume
from app.services.recovery_lock import RecoveryExecutionLock
from app.services.recovery_store import ResumeImageStore, runner_state_directory
from app.services.tools.receipts import SQLiteToolReceiptStore
from app.services.workspace_fingerprint import workspace_revision


class DesktopRecoveryBinding:
    def __init__(self, workspace: Path, model_manifest: Mapping[str, Any], *, tools: list[Any],
                 policy: Any, budgets: Mapping[str, Any], state_root: Path | None = None,
                 execution: Any = None, feedback_policy: Any = None, guidance_model: Any = None) -> None:
        self.workspace = workspace.resolve()
        directory = runner_state_directory(self.workspace, state_root)
        self.execution = execution
        self.feedback_policy = feedback_policy
        self.guidance_model = guidance_model
        self.lock = None
        if execution is not None:
            scope = f"{execution.mission_id}/{execution.work_unit_id}/{execution.attempt}"
            self.lock = RecoveryExecutionLock(directory, scope)
        try:
            self._initialize(directory, model_manifest, tools, policy, budgets, feedback_policy, guidance_model)
        except BaseException:
            self.close()
            raise

    def _initialize(self, directory: Path, model_manifest: Mapping[str, Any], tools: list[Any],
                     policy: Any, budgets: Mapping[str, Any], feedback_policy: Any, guidance_model: Any) -> None:
        self.store = ResumeImageStore(directory / "resume-images.sqlite3")
        self.receipts = SQLiteToolReceiptStore(directory / "tool-receipts.sqlite3",
            workspace_revision_provider=lambda: workspace_revision(self.workspace))
        self.tools = {tool.name: tool for tool in tools}
        self.material = {"model": dict(model_manifest), "tools": list(build_function_tool_schemas(tools)),
                         "permissionMode": str(policy.mode) if policy else "denied",
                         "budgets": dict(budgets)}
        if feedback_policy is not None:
            self.material["toolFeedbackPolicy"] = asdict(feedback_policy)
        if guidance_model is not None:
            from app.services.guidance_recovery import MAX_GUIDANCE_EVENTS, MAX_GUIDANCE_STATE_BYTES
            self.material["guidance"] = {"version": 1, "delivery": "mission-shared-once",
                "maxEvents": MAX_GUIDANCE_EVENTS, "maxStateBytes": MAX_GUIDANCE_STATE_BYTES}

    def journal(self, port: Any, checkpoint: Any) -> RecoveryCheckpointJournal:
        sequence = int(checkpoint.get("sequence", 0)) if isinstance(checkpoint, Mapping) else 0
        return RecoveryCheckpointJournal(port, self.store, workspace=self.workspace,
                                         context_material=self.material, base_sequence=sequence, tools=self.tools,
                                         guidance_model=self.guidance_model)

    def restore(self, checkpoint: Mapping[str, Any], *, code: str, timeout: float | None = None, language: str | None = None):
        if self.execution is not None and any(checkpoint.get(key) != value for key, value in {
            "missionId": self.execution.mission_id, "workUnitId": self.execution.work_unit_id,
            "attempt": self.execution.attempt,
        }.items()):
            raise ResumeImageError("checkpoint belongs to another claimed execution")
        return restore_resume(self.store, checkpoint, code=code, workspace=self.workspace,
                              context_material=self.material, receipt_store=self.receipts, tools=self.tools,
                              timeout=timeout, language=language, feedback_policy=self.feedback_policy,
                              guidance_model=self.guidance_model)

    def close(self) -> None:
        if self.lock is not None:
            self.lock.close()
