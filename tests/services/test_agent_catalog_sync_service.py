from __future__ import annotations

import unittest
from collections.abc import Mapping
from unittest.mock import AsyncMock, patch

from app.services.agent_binding_service import (
    AgentBinding,
    AgentCatalogMutation,
    AgentCatalogVersionConflictError,
    DatabaseAgentCatalogWriter,
)
from app.services.agent_catalog_sync_service import (
    AgentCatalogSynchronizer,
    DatabaseRegistryAgentSource,
    RegistryAgentNotFoundError,
    RegistryAgentNotRunnableError,
    RegistryAgentProjection,
)


class StaticRegistrySource:
    def __init__(self, projection: RegistryAgentProjection | None) -> None:
        self.projection = projection
        self.calls: list[tuple[str, str]] = []

    async def resolve(
        self,
        *,
        owner_id: str,
        agent_id: str,
    ) -> RegistryAgentProjection | None:
        self.calls.append((owner_id, agent_id))
        return self.projection


def registry_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "agent_id": "reviewer",
        "adapter_type": "local_codex",
        "capability_tags": '["repository.read"]',
        "status": "sleeping",
    }
    row.update(overrides)
    return row


class AgentCatalogSyncServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_source_returns_safe_projection(self) -> None:
        calls: list[tuple[str, str]] = []

        async def lookup(owner_id: str, agent_id: str) -> Mapping[str, object]:
            calls.append((owner_id, agent_id))
            return registry_row()

        projection = await DatabaseRegistryAgentSource(lookup).resolve(
            owner_id="user-1",
            agent_id="reviewer",
        )

        self.assertEqual(calls, [("user-1", "reviewer")])
        self.assertEqual(projection.binding.adapter_type, "local_codex")
        self.assertEqual(projection.binding.capabilities, ("repository.read",))
        self.assertTrue(projection.enabled)

    async def test_default_registry_query_excludes_sensitive_columns(self) -> None:
        lookup = AsyncMock(return_value=registry_row())
        with patch("app.db.session.afetch_one", new=lookup):
            await DatabaseRegistryAgentSource().resolve(
                owner_id="user-1",
                agent_id="reviewer",
            )

        sql = lookup.await_args.args[0].lower()
        selected_columns = sql.split("from agent_registry", maxsplit=1)[0]
        self.assertIn("agent_id, adapter_type, capability_tags, status", sql)
        self.assertIn("case when user_id = $2 then 0 else 1 end", sql)
        for forbidden in ("api_key", "base_url", "config", "avatar", "model"):
            self.assertNotIn(forbidden, selected_columns)
        self.assertEqual(lookup.await_args.args[1:], ("reviewer", "user-1"))

    async def test_registry_status_controls_catalog_enabled_state(self) -> None:
        for status, enabled in (
            ("online", True),
            ("sleeping", True),
            ("offline", False),
        ):
            with self.subTest(status=status):
                async def lookup(
                    _owner_id: str,
                    _agent_id: str,
                    status: str = status,
                ) -> Mapping[str, object]:
                    return registry_row(status=status)

                projection = await DatabaseRegistryAgentSource(lookup).resolve(
                    owner_id="user-1",
                    agent_id="reviewer",
                )
                self.assertIs(projection.enabled, enabled)

    async def test_registry_source_rejects_mock_adapter(self) -> None:
        async def lookup(_owner_id: str, _agent_id: str) -> Mapping[str, object]:
            return registry_row(adapter_type="mock")

        with self.assertRaisesRegex(
            RegistryAgentNotRunnableError,
            "no executable adapter",
        ):
            await DatabaseRegistryAgentSource(lookup).resolve(
                owner_id="user-1",
                agent_id="reviewer",
            )

    async def test_synchronizer_forwards_safe_projection_and_version(self) -> None:
        source = StaticRegistrySource(
            RegistryAgentProjection(
                binding=AgentBinding(
                    agent_id="reviewer",
                    adapter_type="local_codex",
                    capabilities=("repository.read",),
                ),
                status="sleeping",
            )
        )
        mutations: list[AgentCatalogMutation] = []

        async def write(mutation: AgentCatalogMutation) -> Mapping[str, object]:
            mutations.append(mutation)
            return {
                "scope_id": mutation.scope_id,
                "agent_id": mutation.binding.agent_id,
                "adapter_type": mutation.binding.adapter_type,
                "capabilities": list(mutation.binding.capabilities),
                "enabled": mutation.enabled,
                "source_version": 4,
                "updated_at": "2026-08-14T10:00:00+00:00",
            }

        record = await AgentCatalogSynchronizer(
            source,
            DatabaseAgentCatalogWriter(write),
        ).sync(
            scope_id="workspace-1",
            source_owner_id="user-1",
            agent_id="reviewer",
            expected_version=3,
        )

        self.assertEqual(source.calls, [("user-1", "reviewer")])
        self.assertEqual(mutations[0].scope_id, "workspace-1")
        self.assertEqual(mutations[0].expected_version, 3)
        self.assertTrue(mutations[0].enabled)
        self.assertEqual(record.source_version, 4)

    async def test_synchronizer_does_not_write_missing_registry_agent(self) -> None:
        source = StaticRegistrySource(None)
        writer = AsyncMock()
        synchronizer = AgentCatalogSynchronizer(source, writer)

        with self.assertRaisesRegex(RegistryAgentNotFoundError, "not found"):
            await synchronizer.sync(
                scope_id="workspace-1",
                source_owner_id="user-1",
                agent_id="missing",
                expected_version=0,
            )

        writer.put.assert_not_awaited()

    async def test_synchronizer_preserves_catalog_version_conflict(self) -> None:
        source = StaticRegistrySource(
            RegistryAgentProjection(
                binding=AgentBinding(
                    agent_id="reviewer",
                    adapter_type="local_codex",
                    capabilities=(),
                ),
                status="offline",
            )
        )

        async def write(_mutation: AgentCatalogMutation) -> None:
            return None

        with self.assertRaises(AgentCatalogVersionConflictError):
            await AgentCatalogSynchronizer(
                source,
                DatabaseAgentCatalogWriter(write),
            ).sync(
                scope_id="workspace-1",
                source_owner_id="user-1",
                agent_id="reviewer",
                expected_version=2,
            )


if __name__ == "__main__":
    unittest.main()
