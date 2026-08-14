from __future__ import annotations

import unittest

from app.services.agent_binding_service import (
    AgentBinding,
    AgentBindingUnavailableError,
    DatabaseAgentBindingResolver,
    StaticAgentBindingResolver,
    UnavailableAgentBindingResolver,
)


class AgentBindingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_static_resolver_is_scope_isolated(self) -> None:
        resolver = StaticAgentBindingResolver(
            {
                ("workspace-1", "reviewer"): AgentBinding(
                    agent_id="reviewer",
                    adapter_type="local_codex",
                    capabilities=("repository.read", "repository.write"),
                )
            }
        )

        binding = await resolver.resolve(scope_id="workspace-1", agent_id="reviewer")

        self.assertEqual(binding.adapter_type, "local_codex")
        self.assertIsNone(
            await resolver.resolve(scope_id="workspace-2", agent_id="reviewer")
        )

    def test_mapping_parser_discards_sensitive_fields(self) -> None:
        binding = AgentBinding.from_mapping(
            {
                "agentId": "reviewer",
                "adapterType": "local_codex",
                "capabilities": ["repository.write", "repository.write"],
                "apiKey": "must-not-be-stored",
                "baseUrl": "https://provider.invalid",
            }
        )

        self.assertEqual(binding.capabilities, ("repository.write",))
        self.assertFalse(hasattr(binding, "api_key"))
        self.assertFalse(hasattr(binding, "base_url"))

    async def test_unavailable_resolver_fails_closed(self) -> None:
        with self.assertRaises(AgentBindingUnavailableError):
            await UnavailableAgentBindingResolver().resolve(
                scope_id="workspace-1",
                agent_id="reviewer",
            )

    async def test_database_resolver_reads_scoped_safe_projection(self) -> None:
        calls: list[tuple[str, str]] = []

        async def lookup(scope_id: str, agent_id: str) -> dict[str, object]:
            calls.append((scope_id, agent_id))
            return {
                "agent_id": "reviewer",
                "adapter_type": "local_codex",
                "capabilities": '["repository.write", "repository.read"]',
                "api_key": "must-not-be-returned",
            }

        binding = await DatabaseAgentBindingResolver(lookup).resolve(
            scope_id="workspace-1",
            agent_id="reviewer",
        )

        self.assertEqual(calls, [("workspace-1", "reviewer")])
        self.assertEqual(
            binding,
            AgentBinding(
                agent_id="reviewer",
                adapter_type="local_codex",
                capabilities=("repository.read", "repository.write"),
            ),
        )

    async def test_database_resolver_returns_none_for_unknown_binding(self) -> None:
        async def lookup(_scope_id: str, _agent_id: str) -> None:
            return None

        binding = await DatabaseAgentBindingResolver(lookup).resolve(
            scope_id="workspace-1",
            agent_id="missing",
        )

        self.assertIsNone(binding)

    async def test_database_resolver_fails_closed_on_catalog_error(self) -> None:
        async def lookup(_scope_id: str, _agent_id: str) -> None:
            raise RuntimeError("database unavailable")

        with self.assertRaisesRegex(
            AgentBindingUnavailableError,
            "Agent catalog lookup failed",
        ):
            await DatabaseAgentBindingResolver(lookup).resolve(
                scope_id="workspace-1",
                agent_id="reviewer",
            )

    async def test_database_resolver_rejects_mismatched_catalog_row(self) -> None:
        async def lookup(_scope_id: str, _agent_id: str) -> dict[str, object]:
            return {
                "agent_id": "other-agent",
                "adapter_type": "local_codex",
                "capabilities": [],
            }

        with self.assertRaisesRegex(
            AgentBindingUnavailableError,
            "Agent catalog returned invalid binding",
        ):
            await DatabaseAgentBindingResolver(lookup).resolve(
                scope_id="workspace-1",
                agent_id="reviewer",
            )

    async def test_database_resolver_rejects_malformed_capabilities(self) -> None:
        async def lookup(_scope_id: str, _agent_id: str) -> dict[str, object]:
            return {
                "agent_id": "reviewer",
                "adapter_type": "local_codex",
                "capabilities": "not-json",
            }

        with self.assertRaisesRegex(
            AgentBindingUnavailableError,
            "Agent catalog lookup failed",
        ):
            await DatabaseAgentBindingResolver(lookup).resolve(
                scope_id="workspace-1",
                agent_id="reviewer",
            )
