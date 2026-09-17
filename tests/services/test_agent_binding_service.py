from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.services.agent_binding_service import (
    AgentBinding,
    AgentBindingUnavailableError,
    AgentCatalogMutation,
    AgentCatalogVersionConflictError,
    DatabaseAgentBindingResolver,
    DatabaseAgentBindingSelector,
    DatabaseAgentCatalogWriter,
    StaticAgentBindingResolver,
    StaticAgentBindingSelector,
    UnavailableAgentBindingResolver,
    UnavailableAgentBindingSelector,
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

    def test_mapping_parser_accepts_namespaced_adapter_type(self) -> None:
        binding = AgentBinding.from_mapping(
            {
                "agentId": "outbound-runner",
                "adapterType": "a2a.outbound",
                "capabilities": ["a2a.send"],
            }
        )

        self.assertEqual(binding.adapter_type, "a2a.outbound")

    def test_mapping_parser_rejects_empty_adapter_namespace_segment(self) -> None:
        for adapter_type in ("a2a..outbound", "a2a."):
            with (
                self.subTest(adapter_type=adapter_type),
                self.assertRaisesRegex(ValueError, "adapter_type is invalid"),
            ):
                AgentBinding.from_mapping(
                    {
                        "agentId": "outbound-runner",
                        "adapterType": adapter_type,
                        "capabilities": ["a2a.send"],
                    }
                )

    async def test_unavailable_resolver_fails_closed(self) -> None:
        with self.assertRaises(AgentBindingUnavailableError):
            await UnavailableAgentBindingResolver().resolve(
                scope_id="workspace-1",
                agent_id="reviewer",
            )

    async def test_unavailable_selector_fails_closed(self) -> None:
        with self.assertRaises(AgentBindingUnavailableError):
            await UnavailableAgentBindingSelector().select(
                scope_id="workspace-1",
                required_capabilities=["a2a.receive"],
            )

    async def test_static_selector_is_scope_isolated_and_deterministic(self) -> None:
        selector = StaticAgentBindingSelector(
            {
                "workspace-1": [
                    AgentBinding(
                        agent_id="z-reviewer",
                        adapter_type="local_codex",
                        capabilities=("a2a.receive", "repository.read"),
                    ),
                    AgentBinding(
                        agent_id="a-reviewer",
                        adapter_type="local_claude",
                        capabilities=("a2a.receive", "repository.read"),
                    ),
                ],
                "workspace-2": [
                    AgentBinding(
                        agent_id="other",
                        adapter_type="local_codex",
                        capabilities=("a2a.receive", "repository.read"),
                    )
                ],
            }
        )

        binding = await selector.select(
            scope_id="workspace-1",
            required_capabilities=["repository.read", "a2a.receive"],
        )

        self.assertEqual(binding.agent_id, "a-reviewer")
        self.assertIsNone(
            await selector.select(
                scope_id="missing-workspace",
                required_capabilities=["a2a.receive"],
            )
        )

    async def test_database_selector_requires_complete_capability_match(self) -> None:
        calls: list[tuple[str, tuple[str, ...], str | None]] = []

        async def select(
            scope_id: str,
            capabilities: tuple[str, ...],
            adapter_type: str | None,
        ) -> dict[str, object]:
            calls.append((scope_id, capabilities, adapter_type))
            return {
                "agent_id": "reviewer",
                "adapter_type": "local_codex",
                "capabilities": ["repository.read", "a2a.receive"],
            }

        binding = await DatabaseAgentBindingSelector(select).select(
            scope_id=" workspace-1 ",
            required_capabilities=["repository.read", "a2a.receive"],
        )

        self.assertEqual(
            calls,
            [("workspace-1", ("a2a.receive", "repository.read"), None)],
        )
        self.assertEqual(binding.agent_id, "reviewer")

    async def test_database_selector_rejects_incomplete_catalog_row(self) -> None:
        async def select(
            _scope_id: str,
            _capabilities: tuple[str, ...],
            _adapter_type: str | None,
        ) -> dict[str, object]:
            return {
                "agent_id": "reviewer",
                "adapter_type": "local_codex",
                "capabilities": ["a2a.receive"],
            }

        with self.assertRaisesRegex(
            AgentBindingUnavailableError,
            "capability-incomplete",
        ):
            await DatabaseAgentBindingSelector(select).select(
                scope_id="workspace-1",
                required_capabilities=["a2a.receive", "repository.read"],
            )

    async def test_database_selector_rejects_wrong_adapter_row(self) -> None:
        async def select(
            _scope_id: str,
            _capabilities: tuple[str, ...],
            _adapter_type: str | None,
        ) -> dict[str, object]:
            return {
                "agent_id": "local-agent",
                "adapter_type": "local_codex",
                "capabilities": ["a2a.send"],
            }

        with self.assertRaisesRegex(
            AgentBindingUnavailableError,
            "another adapter",
        ):
            await DatabaseAgentBindingSelector(select).select(
                scope_id="workspace-1",
                required_capabilities=["a2a.send"],
                adapter_type="a2a.outbound",
            )

    async def test_database_selector_wraps_malformed_catalog_row(self) -> None:
        async def select(
            _scope_id: str,
            _capabilities: tuple[str, ...],
            _adapter_type: str | None,
        ) -> dict[str, object]:
            return {
                "agent_id": "reviewer",
                "adapter_type": "https://provider.invalid",
                "capabilities": ["a2a.receive"],
            }

        with self.assertRaisesRegex(
            AgentBindingUnavailableError,
            "selection failed",
        ):
            await DatabaseAgentBindingSelector(select).select(
                scope_id="workspace-1",
                required_capabilities=["a2a.receive"],
            )

    async def test_database_selector_query_is_scoped_enabled_and_stable(self) -> None:
        captured: dict[str, object] = {}

        async def fetch_one(query: str, *args: object) -> dict[str, object]:
            captured["query"] = query
            captured["args"] = args
            return {
                "agent_id": "reviewer",
                "adapter_type": "local_codex",
                "capabilities": ["a2a.receive", "repository.read"],
            }

        with patch("app.db.session.afetch_one", new=fetch_one):
            await DatabaseAgentBindingSelector().select(
                scope_id="workspace-1",
                required_capabilities=["repository.read", "a2a.receive"],
                adapter_type="local_codex",
            )

        query = str(captured["query"])
        self.assertIn("WHERE scope_id = $1", query)
        self.assertIn("enabled = TRUE", query)
        self.assertIn("capabilities @> $2::jsonb", query)
        self.assertIn("adapter_type = $3", query)
        self.assertIn("ORDER BY agent_id ASC, adapter_type ASC", query)
        self.assertEqual(
            captured["args"],
            (
                "workspace-1",
                json.dumps(["a2a.receive", "repository.read"]),
                "local_codex",
            ),
        )

    async def test_static_selector_filters_exact_adapter(self) -> None:
        selector = StaticAgentBindingSelector(
            {
                "workspace-1": [
                    AgentBinding(
                        agent_id="local-agent",
                        adapter_type="local_codex",
                        capabilities=("a2a.send",),
                    ),
                    AgentBinding(
                        agent_id="outbound-agent",
                        adapter_type="a2a.outbound",
                        capabilities=("a2a.send",),
                    ),
                ]
            }
        )

        binding = await selector.select(
            scope_id="workspace-1",
            required_capabilities=["a2a.send"],
            adapter_type="a2a.outbound",
        )

        self.assertIsNotNone(binding)
        self.assertEqual(binding.agent_id, "outbound-agent")

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

    async def test_catalog_writer_creates_normalized_binding_at_version_one(
        self,
    ) -> None:
        calls: list[AgentCatalogMutation] = []

        async def write(mutation: AgentCatalogMutation) -> dict[str, object]:
            calls.append(mutation)
            return {
                "scope_id": mutation.scope_id,
                "agent_id": mutation.binding.agent_id,
                "adapter_type": mutation.binding.adapter_type,
                "capabilities": list(mutation.binding.capabilities),
                "enabled": mutation.enabled,
                "source_version": 1,
                "updated_at": "2026-08-14T10:00:00+00:00",
            }

        record = await DatabaseAgentCatalogWriter(write).put(
            scope_id=" workspace-1 ",
            agent_id="reviewer",
            adapter_type="local_codex",
            capabilities=["repository.write", "repository.write"],
            enabled=True,
            expected_version=0,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].scope_id, "workspace-1")
        self.assertEqual(calls[0].binding.capabilities, ("repository.write",))
        self.assertEqual(record.source_version, 1)
        self.assertEqual(record.to_public_dict()["agentId"], "reviewer")

    async def test_catalog_writer_accepts_exact_version_update(self) -> None:
        async def write(mutation: AgentCatalogMutation) -> dict[str, object]:
            return {
                "scope_id": mutation.scope_id,
                "agent_id": mutation.binding.agent_id,
                "adapter_type": mutation.binding.adapter_type,
                "capabilities": list(mutation.binding.capabilities),
                "enabled": mutation.enabled,
                "source_version": 8,
                "updated_at": "2026-08-14T10:00:00+00:00",
            }

        record = await DatabaseAgentCatalogWriter(write).put(
            scope_id="workspace-1",
            agent_id="reviewer",
            adapter_type="local_codex",
            capabilities=[],
            enabled=False,
            expected_version=7,
        )

        self.assertEqual(record.source_version, 8)
        self.assertFalse(record.enabled)

    async def test_catalog_writer_reports_version_conflict(self) -> None:
        async def write(_mutation: AgentCatalogMutation) -> None:
            return None

        with self.assertRaisesRegex(
            AgentCatalogVersionConflictError,
            "version conflict",
        ):
            await DatabaseAgentCatalogWriter(write).put(
                scope_id="workspace-1",
                agent_id="reviewer",
                adapter_type="local_codex",
                capabilities=[],
                enabled=True,
                expected_version=2,
            )

    async def test_catalog_writer_rejects_invalid_returned_version(self) -> None:
        async def write(mutation: AgentCatalogMutation) -> dict[str, object]:
            return {
                "scope_id": mutation.scope_id,
                "agent_id": mutation.binding.agent_id,
                "adapter_type": mutation.binding.adapter_type,
                "capabilities": [],
                "enabled": True,
                "source_version": 99,
                "updated_at": "2026-08-14T10:00:00+00:00",
            }

        with self.assertRaisesRegex(
            AgentBindingUnavailableError,
            "invalid mutation",
        ):
            await DatabaseAgentCatalogWriter(write).put(
                scope_id="workspace-1",
                agent_id="reviewer",
                adapter_type="local_codex",
                capabilities=[],
                enabled=True,
                expected_version=2,
            )

    async def test_catalog_writer_rejects_changed_returned_binding(self) -> None:
        async def write(mutation: AgentCatalogMutation) -> dict[str, object]:
            return {
                "scope_id": mutation.scope_id,
                "agent_id": mutation.binding.agent_id,
                "adapter_type": "local_claude",
                "capabilities": [],
                "enabled": mutation.enabled,
                "source_version": 1,
                "updated_at": "2026-08-14T10:00:00+00:00",
            }

        with self.assertRaisesRegex(
            AgentBindingUnavailableError,
            "invalid mutation",
        ):
            await DatabaseAgentCatalogWriter(write).put(
                scope_id="workspace-1",
                agent_id="reviewer",
                adapter_type="local_codex",
                capabilities=[],
                enabled=True,
                expected_version=0,
            )

    async def test_catalog_writer_rejects_invalid_adapter_before_write(self) -> None:
        called = False

        async def write(_mutation: AgentCatalogMutation) -> None:
            nonlocal called
            called = True

        with self.assertRaisesRegex(ValueError, "adapter_type is invalid"):
            await DatabaseAgentCatalogWriter(write).put(
                scope_id="workspace-1",
                agent_id="reviewer",
                adapter_type="https://provider.invalid",
                capabilities=[],
                enabled=True,
                expected_version=0,
            )
        self.assertFalse(called)
