from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.access import authorize_workspace
from app.schemas.agent_catalog import AgentCatalogBindingPutRequest
from app.services.agent_binding_service import (
    AgentBindingUnavailableError,
    AgentCatalogVersionConflictError,
    DatabaseAgentCatalogWriter,
)
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/agent-catalog", tags=["agent-catalog"])


def get_agent_catalog_writer() -> DatabaseAgentCatalogWriter:
    return DatabaseAgentCatalogWriter()


CurrentUser = Annotated[dict, Depends(get_current_user)]
AgentCatalogWriterDep = Annotated[
    DatabaseAgentCatalogWriter,
    Depends(get_agent_catalog_writer),
]


@router.put("/workspaces/{scope_id}/bindings/{agent_id}")
async def put_agent_catalog_binding(
    scope_id: str,
    agent_id: str,
    request: AgentCatalogBindingPutRequest,
    user: CurrentUser,
    writer: AgentCatalogWriterDep,
) -> dict[str, object]:
    authorize_workspace(user, scope_id)
    try:
        record = await writer.put(
            scope_id=scope_id,
            agent_id=agent_id,
            adapter_type=request.adapter_type,
            capabilities=request.capabilities,
            enabled=request.enabled,
            expected_version=request.expected_version,
        )
    except AgentCatalogVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AgentBindingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return record.to_public_dict()
