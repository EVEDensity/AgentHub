"""Desktop private recovery binding; durable lifecycle remains Mission Control's."""
from __future__ import annotations

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
                 execution: Any = None) -> None:
        self.workspace = workspace.resolve()
        directory = runner_state_directory(self.workspace, state_root)
        self.execution = execution
        self.lock = None
        if execution is not None:
            scope = f"{execution.mission_id}/{execution.work_unit_id}/{execution.attempt}"
            self.lock = RecoveryExecutionLock(directory, scope)
        self.store = ResumeImageStore(directory / "resume-images.sqlite3")
        self.receipts = SQLiteToolReceiptStore(directory / "tool-receipts.sqlite3",
            workspace_revision_provider=lambda: workspace_revision(self.workspace))
        self.tools = {tool.name: tool for tool in tools}
        self.material = {"model": dict(model_manifest), "tools": list(build_function_tool_schemas(tools)),
                         "permissionMode": str(policy.mode) if policy else "denied",
                         "budgets": dict(budgets)}

    def journal(self, port: Any, checkpoint: Any) -> RecoveryCheckpointJournal:
        sequence = int(checkpoint.get("sequence", 0)) if isinstance(checkpoint, Mapping) else 0
        return RecoveryCheckpointJournal(port, self.store, workspace=self.workspace,
                                         context_material=self.material, base_sequence=sequence, tools=self.tools)

    def restore(self, checkpoint: Mapping[str, Any], *, code: str, timeout: float | None = None, language: str | None = None):
        if self.execution is not None and any(checkpoint.get(key) != value for key, value in {
            "missionId": self.execution.mission_id, "workUnitId": self.execution.work_unit_id,
            "attempt": self.execution.attempt,
        }.items()):
            raise ResumeImageError("checkpoint belongs to another claimed execution")
        return restore_resume(self.store, checkpoint, code=code, workspace=self.workspace,
                              context_material=self.material, receipt_store=self.receipts, tools=self.tools,
                              timeout=timeout, language=language)

    def close(self) -> None:
        if self.lock is not None:
            self.lock.close()
