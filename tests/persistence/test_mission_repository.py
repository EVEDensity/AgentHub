from __future__ import annotations

import json
import unittest
from typing import Any

from app.domain import Mission
from app.repositories import MissionRepository
from tests.domain.factories import build_contract, build_mission


class FakeDatabase:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.one: dict[str, Any] | None = None
        self.all: list[dict[str, Any]] = []

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, args))

    async def fetch_one(self, _sql: str, *_args: Any) -> dict[str, Any] | None:
        return self.one

    async def fetch_all(self, _sql: str, *_args: Any) -> list[dict[str, Any]]:
        return self.all


class MissionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.database = FakeDatabase()
        self.repository = MissionRepository(
            execute=self.database.execute,
            fetch_one=self.database.fetch_one,
            fetch_all=self.database.fetch_all,
        )

    async def test_contract_round_trip_accepts_json_text(self) -> None:
        contract = build_contract()
        await self.repository.add_contract(contract)

        _sql, args = self.database.executed[-1]
        self.assertEqual(args[0:2], (contract.id, contract.version))
        self.assertEqual(json.loads(args[2]), contract.to_public_dict())

        self.database.one = {"document": args[2]}
        restored = await self.repository.get_contract(contract.id)
        self.assertEqual(restored, contract)

    async def test_mission_round_trip_accepts_decoded_json(self) -> None:
        mission = build_mission()
        await self.repository.add_mission(mission)

        _sql, args = self.database.executed[-1]
        self.assertEqual(args[0], mission.id)
        self.assertEqual(args[6], mission.status.value)
        self.database.one = self.build_mission_row(mission)
        restored = await self.repository.get_mission(mission.id)
        self.assertEqual(restored, mission)

    async def test_missing_records_return_none(self) -> None:
        self.assertIsNone(await self.repository.get_contract("missing"))
        self.assertIsNone(await self.repository.get_mission("missing"))

    async def test_list_is_workspace_scoped_and_bounded(self) -> None:
        mission = build_mission()
        self.database.all = [self.build_mission_row(mission)]

        restored = await self.repository.list_missions(
            "workspace-1", limit=20, offset=5
        )

        self.assertEqual(restored, [mission])
        with self.assertRaises(ValueError):
            await self.repository.list_missions("workspace-1", limit=0)
        with self.assertRaises(ValueError):
            await self.repository.list_missions("workspace-1", offset=-1)

    @staticmethod
    def build_mission_row(mission: Mission) -> dict[str, Any]:
        return {
            "id": mission.id,
            "workspace_id": mission.workspace_id,
            "title": mission.title,
            "objective": mission.objective,
            "source": mission.source.to_public_dict(),
            "contract_id": mission.contract_id,
            "status": mission.status.value,
            "plan_version": mission.plan_version,
            "created_by": mission.created_by.to_public_dict(),
            "created_at": mission.created_at,
            "updated_at": mission.updated_at,
        }


if __name__ == "__main__":
    unittest.main()
