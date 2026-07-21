from __future__ import annotations

import json
from typing import Any

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute
from app.schemas.workflow import WorkflowDraftRequest
from app.services.workflow_contract import MAX_WORKFLOW_EDGES, MAX_WORKFLOW_NODES, validate_workflow_contract
from app.services.workflow_errors import WorkflowVersionConflict


MAX_DRAFT_BYTES = 1_000_000


class WorkflowDraftService:
    async def list_drafts(self, user_id: str) -> list[dict[str, Any]]:
        rows = await afetch_all(
            "SELECT draft_key,workflow_id,name,base_version,version,created_at,updated_at "
            "FROM workflow_drafts WHERE user_id=$1 ORDER BY updated_at DESC",
            user_id,
        )
        return [self._metadata(row) for row in rows]

    async def get_draft(self, user_id: str, draft_key: str) -> dict[str, Any] | None:
        row = await afetch_one(
            "SELECT draft_key,workflow_id,name,payload_json,base_version,version,created_at,updated_at "
            "FROM workflow_drafts WHERE user_id=$1 AND draft_key=$2",
            user_id,
            draft_key,
        )
        return self._deserialize(row) if row else None

    async def save_draft(self, user_id: str, draft_key: str, data: WorkflowDraftRequest) -> dict[str, Any]:
        if len(data.nodes) > MAX_WORKFLOW_NODES or len(data.edges) > MAX_WORKFLOW_EDGES:
            raise ValueError("Draft exceeds workflow node or edge limits")
        payload = data.model_dump(mode="json", exclude={"draftVersion"})
        payload_json = json.dumps(payload, ensure_ascii=False)
        if len(payload_json.encode("utf-8")) > MAX_DRAFT_BYTES:
            raise ValueError("Draft payload exceeds 1 MB")

        timestamp = now()
        if data.draftVersion == 0:
            saved = await afetch_one(
                "INSERT INTO workflow_drafts(user_id,workflow_id,draft_key,name,payload_json,base_version,"
                "version,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,1,$7,$8) "
                "ON CONFLICT(user_id,draft_key) DO NOTHING RETURNING draft_key,version",
                user_id,
                data.workflowId,
                draft_key,
                data.name,
                payload_json,
                data.baseVersion,
                timestamp,
                timestamp,
            )
        else:
            saved = await afetch_one(
                "UPDATE workflow_drafts SET workflow_id=$1,name=$2,payload_json=$3,base_version=$4,"
                "version=version+1,updated_at=$5 WHERE user_id=$6 AND draft_key=$7 AND version=$8 "
                "RETURNING draft_key,version",
                data.workflowId,
                data.name,
                payload_json,
                data.baseVersion,
                timestamp,
                user_id,
                draft_key,
                data.draftVersion,
            )
        if not saved:
            current = await afetch_one(
                "SELECT version FROM workflow_drafts WHERE user_id=$1 AND draft_key=$2", user_id, draft_key,
            )
            raise WorkflowVersionConflict(data.draftVersion, int(current["version"]) if current else 0)
        result = await self.get_draft(user_id, draft_key)
        if result is None:
            raise ValueError("Draft save failed")
        return result

    async def delete_draft(self, user_id: str, draft_key: str) -> bool:
        existing = await self.get_draft(user_id, draft_key)
        if not existing:
            return False
        await aexecute("DELETE FROM workflow_drafts WHERE user_id=$1 AND draft_key=$2", user_id, draft_key)
        return True

    @staticmethod
    def _metadata(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "draftKey": row["draft_key"],
            "workflowId": row.get("workflow_id"),
            "name": row["name"],
            "baseVersion": int(row["base_version"]),
            "draftVersion": int(row["version"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _deserialize(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(row["payload_json"])
        validation = validate_workflow_contract(
            payload.get("nodes", []), payload.get("edges", []), schema_version=payload.get("schemaVersion", 1),
        )
        return {
            **self._metadata(row),
            "payload": payload,
            "validation": validation.model_dump(mode="json"),
        }


workflow_draft_service = WorkflowDraftService()
