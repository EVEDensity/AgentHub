from __future__ import annotations

import os
import unittest
import uuid

import asyncpg

from app.db.migrations.mission_control_plane import (
    CONTRACT_LINEAGE_OWNERSHIP_UPGRADE,
    CONTRACT_REVISION_BINDING_UPGRADE,
    MISSION_CONTROL_PLANE_UPGRADE,
)

_POSTGRES_DSN = os.getenv("AGENTHUB_TEST_POSTGRES_DSN")


@unittest.skipUnless(
    _POSTGRES_DSN,
    "AGENTHUB_TEST_POSTGRES_DSN is required for Contract lineage migration tests",
)
class ContractLineageMigrationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        assert _POSTGRES_DSN is not None
        self._schema = f"agenthub_lineage_test_{uuid.uuid4().hex}"
        self._connection = await asyncpg.connect(
            _POSTGRES_DSN,
            server_settings={"search_path": self._schema},
        )
        setup_connection = await asyncpg.connect(_POSTGRES_DSN)
        try:
            await setup_connection.execute(f'CREATE SCHEMA "{self._schema}"')
        finally:
            await setup_connection.close()
        self.addAsyncCleanup(self._cleanup)

        for statement in MISSION_CONTROL_PLANE_UPGRADE:
            await self._connection.execute(statement)

    async def _cleanup(self) -> None:
        assert _POSTGRES_DSN is not None
        await self._connection.close()
        connection = await asyncpg.connect(_POSTGRES_DSN)
        try:
            await connection.execute(f'DROP SCHEMA IF EXISTS "{self._schema}" CASCADE')
        finally:
            await connection.close()

    async def _seed_contract(self, contract_id: str = "contract-1") -> None:
        await self._connection.execute(
            """INSERT INTO mission_contracts(id, version, document)
               VALUES($1::text, 1, jsonb_build_object('id', $1::text, 'version', 1))""",
            contract_id,
        )

    async def _seed_mission(
        self,
        mission_id: str,
        workspace_id: str,
        contract_id: str = "contract-1",
    ) -> None:
        await self._connection.execute(
            """INSERT INTO missions(
                   id, workspace_id, title, objective, source, contract_id,
                   status, plan_version, created_by, created_at, updated_at
               ) VALUES(
                   $1, $2, 'Migration test', 'Verify lineage ownership.',
                   '{"type":"manual"}'::jsonb, $3, 'READY', 0,
                   '{"type":"human","id":"migration-test"}'::jsonb,
                   CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
               )""",
            mission_id,
            workspace_id,
            contract_id,
        )

    async def _apply_contract_binding(self) -> None:
        for statement in CONTRACT_REVISION_BINDING_UPGRADE:
            await self._connection.execute(statement)

    async def _apply_lineage_migration(self) -> None:
        async with self._connection.transaction():
            for statement in CONTRACT_LINEAGE_OWNERSHIP_UPGRADE:
                await self._connection.execute(statement)

    async def test_single_workspace_lineage_is_backfilled_and_enforced(self) -> None:
        await self._seed_contract()
        await self._seed_mission("mission-1", "workspace-1")
        await self._apply_contract_binding()

        await self._apply_lineage_migration()

        workspace_id = await self._connection.fetchval(
            """SELECT workspace_id FROM mission_contract_lineages
               WHERE contract_id='contract-1'"""
        )
        self.assertEqual(workspace_id, "workspace-1")
        with self.assertRaises(asyncpg.ForeignKeyViolationError):
            await self._connection.execute(
                """UPDATE missions SET workspace_id='workspace-2'
                   WHERE id='mission-1'"""
            )
        with self.assertRaises(asyncpg.ForeignKeyViolationError):
            await self._connection.execute(
                """INSERT INTO mission_contracts(id, version, document)
                   VALUES(
                       'orphan-contract', 1,
                       '{"id":"orphan-contract","version":1}'::jsonb
                   )"""
            )

    async def test_cross_workspace_lineage_fails_without_partial_table(self) -> None:
        await self._seed_contract()
        await self._seed_mission("mission-1", "workspace-1")
        await self._seed_mission("mission-2", "workspace-2")
        await self._apply_contract_binding()

        with self.assertRaisesRegex(
            asyncpg.RaiseError,
            "shared across workspaces",
        ):
            await self._apply_lineage_migration()

        self.assertIsNone(
            await self._connection.fetchval(
                "SELECT to_regclass('mission_contract_lineages')"
            )
        )

    async def test_orphan_lineage_fails_without_guessing_workspace(self) -> None:
        await self._seed_contract()
        await self._apply_contract_binding()

        with self.assertRaisesRegex(asyncpg.RaiseError, "orphan Contract lineage"):
            await self._apply_lineage_migration()

        self.assertIsNone(
            await self._connection.fetchval(
                "SELECT to_regclass('mission_contract_lineages')"
            )
        )
