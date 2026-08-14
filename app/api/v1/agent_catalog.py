from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.access import authorize_workspace
from app.schemas.agent_catalog import (
    AgentCatalogBindingPutRequest,
    AgentCatalogBindingSyncRequest,
)
from app.services.agent_binding_service import (
    AgentBindingUnavailableError,
    AgentCatalogVersionConflictError,
    DatabaseAgentCatalogWriter,
)
from app.services.agent_catalog_sync_service import (
    AgentCatalogSynchronizer,
    DatabaseRegistryAgentSource,
    RegistryAgentNotFoundError,
    RegistryAgentNotRunnableError,
)
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/agent-catalog", tags=["agent-catalog"])


def get_agent_catalog_writer() -> DatabaseAgentCatalogWriter:
    return DatabaseAgentCatalogWriter()


def get_agent_catalog_synchronizer() -> AgentCatalogSynchronizer:
    return AgentCatalogSynchronizer(
        DatabaseRegistryAgentSource(),
        DatabaseAgentCatalogWriter(),
    )


CurrentUser = Annotated[dict, Depends(get_current_user)]
AgentCatalogWriterDep = Annotated[
    DatabaseAgentCatalogWriter,
    Depends(get_agent_catalog_writer),
]
AgentCatalogSynchronizerDep = Annotated[
    AgentCatalogSynchronizer,
    Depends(get_agent_catalog_synchronizer),
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


@router.post("/workspaces/{scope_id}/bindings/{agent_id}/sync")
async def sync_agent_catalog_binding(
    scope_id: str,
    agent_id: str,
    request: AgentCatalogBindingSyncRequest,
    user: CurrentUser,
    synchronizer: AgentCatalogSynchronizerDep,
) -> dict[str, object]:
    authorize_workspace(user, scope_id)
    try:
        record = await synchronizer.sync(
            scope_id=scope_id,
            source_owner_id=str(user["id"]),
            agent_id=agent_id,
            expected_version=request.expected_version,
        )
    except RegistryAgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (AgentCatalogVersionConflictError, RegistryAgentNotRunnableError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AgentBindingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return record.to_public_dict()
