"""Project durable native Session bindings without inventing chat transcript text."""
from app.domain import Session
from app.repositories import SessionRepository
from app.repositories.session_mission_history import SessionMissionHistoryRepository
from app.schemas.session_history import (
    SessionDirectory, SessionDirectoryItem, SessionMissionDirectory, SessionMissionItem,
)


def session_directory_item(session: Session) -> SessionDirectoryItem:
    return SessionDirectoryItem(
        id=session.id, workspace_id=session.workspace_id, title=session.title,
        status=session.status, created_at=session.created_at, updated_at=session.updated_at,
    )


async def list_native_sessions(repository: SessionRepository, workspace_id: str, *,
                               limit: int, offset: int) -> SessionDirectory:
    values = await repository.list_sessions(workspace_id, limit=limit + 1, offset=offset)
    has_more = len(values) > limit
    return SessionDirectory(
        workspace_id=workspace_id, sessions=[session_directory_item(value) for value in values[:limit]],
        has_more=has_more, next_offset=offset + limit if has_more else None,
    )


async def list_native_session_missions(
    repository: SessionMissionHistoryRepository, session: Session, *,
    limit: int, before_id: str | None,
) -> SessionMissionDirectory:
    records = await repository.list_for_session(session.workspace_id, session.id,
                                                limit=limit + 1, before_id=before_id)
    has_more = len(records) > limit
    visible = records[:limit]
    return SessionMissionDirectory(
        workspace_id=session.workspace_id, session_id=session.id,
        missions=[SessionMissionItem(
            id=record.id, workspace_id=record.workspace_id, session_id=record.session_id,
            title=record.title, objective_preview=record.objective[:1000],
            objective_truncated=len(record.objective) > 1000,
            status=record.status, created_at=record.created_at,
        ) for record in visible],
        has_more=has_more, next_before_id=visible[-1].id if has_more else None,
    )
