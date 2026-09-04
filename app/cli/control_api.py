"""Narrow Mission Control API facades used by future CLI commands."""

from __future__ import annotations

from typing import Any, Protocol


class _Client(Protocol):
    def create_and_start_mission(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_mission(self, mission_id: str) -> dict[str, Any]: ...
    def work_units(self, mission_id: str) -> list[dict[str, Any]]: ...
    def artifacts(self, mission_id: str) -> list[dict[str, Any]]: ...
    def decisions(self, mission_id: str) -> list[dict[str, Any]]: ...
    def resolve_decision(self, mission_id: str, decision_id: str, **kwargs: Any) -> dict[str, Any]: ...


class MissionApi:
    def __init__(self, client: _Client) -> None:
        self._client = client

    def create(self, *, title: str, objective: str, time_seconds: int) -> dict[str, Any]:
        return self._client.create_and_start_mission(
            title=title, objective=objective, time_seconds=time_seconds
        )

    def get(self, mission_id: str) -> dict[str, Any]:
        return self._client.get_mission(mission_id)

    def work_units(self, mission_id: str) -> list[dict[str, Any]]:
        return self._client.work_units(mission_id)


class DecisionApi:
    def __init__(self, client: _Client) -> None:
        self._client = client

    def pending(self, mission_id: str) -> list[dict[str, Any]]:
        return self._client.decisions(mission_id)

    def resolve(self, mission_id: str, decision_id: str, *, allow: bool, expected_version: int = 1) -> dict[str, Any]:
        return self._client.resolve_decision(
            mission_id, decision_id, allow=allow, expected_version=expected_version
        )


class ArtifactApi:
    def __init__(self, client: _Client) -> None:
        self._client = client

    def list(self, mission_id: str) -> list[dict[str, Any]]:
        return self._client.artifacts(mission_id)


__all__ = ["ArtifactApi", "DecisionApi", "MissionApi"]
