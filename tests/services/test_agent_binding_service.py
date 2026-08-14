from __future__ import annotations

import unittest

from app.services.agent_binding_service import (
    AgentBinding,
    AgentBindingUnavailableError,
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
