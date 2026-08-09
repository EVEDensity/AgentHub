from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from app.services.capability_tools import (
    CapabilityResolutionError,
    CapabilityToolBinding,
    CapabilityToolResolver,
)
from app.services.model_port import build_function_tool_schemas
from tests.domain.factories import build_contract, build_work_unit


def _binding(
    capability: str = "repository.write",
    function_name: str = "write_file",
) -> CapabilityToolBinding:
    def validate(
        arguments: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        path = arguments.get("path")
        allowed_paths = scope.get("paths", [])
        if not isinstance(path, str) or path not in allowed_paths:
            raise ValueError("path is outside the capability scope")
        return {"path": path}

    async def handle(
        arguments: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> str:
        return f"{arguments['path']}:{','.join(scope['paths'])}"

    return CapabilityToolBinding(
        capability=capability,
        function_name=function_name,
        description="Write one allowed file",
        parameters={"type": "object", "required": ["path"]},
        validate_arguments=validate,
        handler=handle,
    )


class CapabilityToolResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_only_work_unit_required_capabilities_with_scope(self) -> None:
        contract = build_contract(
            allowed_capabilities=[
                {"capability": "repository.write", "scope": {"paths": ["app/main.py"]}},
                {"capability": "repository.read", "scope": {"paths": ["app/**"]}},
            ]
        )
        work_unit = build_work_unit(required_capabilities=["repository.write"])
        resolver = CapabilityToolResolver(
            [_binding(), _binding("repository.read", "read_file")]
        )

        tools = resolver.resolve(contract, work_unit)

        self.assertEqual([tool.name for tool in tools], ["write_file"])
        self.assertEqual(
            build_function_tool_schemas(tools)[0]["function"],
            {
                "name": "write_file",
                "description": "Write one allowed file",
                "parameters": {"type": "object", "required": ["path"]},
            },
        )
        arguments = tools[0].validate_arguments({"path": "app/main.py"})
        self.assertEqual(await tools[0].handler(arguments), "app/main.py:app/main.py")
        with self.assertRaisesRegex(ValueError, "outside the capability scope"):
            tools[0].validate_arguments({"path": "secrets.txt"})

    def test_rejects_capability_not_granted_by_contract(self) -> None:
        resolver = CapabilityToolResolver([_binding("network.read", "fetch")])
        with self.assertRaisesRegex(CapabilityResolutionError, "not granted"):
            resolver.resolve(
                build_contract(),
                build_work_unit(required_capabilities=["network.read"]),
            )

    def test_rejects_required_capability_without_registered_binding(self) -> None:
        with self.assertRaisesRegex(CapabilityResolutionError, "No tool binding"):
            CapabilityToolResolver([]).resolve(build_contract(), build_work_unit())

    def test_rejects_duplicate_function_names_across_capabilities(self) -> None:
        contract = build_contract(
            allowed_capabilities=[
                {"capability": "repository.write"},
                {"capability": "repository.read"},
            ]
        )
        resolver = CapabilityToolResolver(
            [_binding(), _binding("repository.read", "write_file")]
        )
        with self.assertRaisesRegex(CapabilityResolutionError, "Duplicate function"):
            resolver.resolve(
                contract,
                build_work_unit(
                    required_capabilities=["repository.write", "repository.read"]
                ),
            )
