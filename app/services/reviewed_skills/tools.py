"""Two stateless read-only tools for progressive disclosure, never permissions."""

from __future__ import annotations

import json
from collections.abc import Mapping

from app.services.harness_types import FunctionTool

from .catalog import sha256_digest
from .models import ReviewedSkillCatalog, ReviewedSkillError, resource_path, skill_name


def _list_arguments(arguments: Mapping) -> dict:
    if not isinstance(arguments, Mapping) or arguments:
        raise ValueError("reviewed skill_list takes no arguments")
    return {}


def _load_arguments(arguments: Mapping) -> dict:
    if not isinstance(arguments, Mapping) or set(arguments) - {"name", "resource"}:
        raise ValueError("reviewed skill_load only accepts name and resource")
    result = {"name": skill_name(arguments.get("name"))}
    if "resource" in arguments:
        result["resource"] = resource_path(arguments["resource"])
    return result


def _json(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def build_reviewed_skill_tools(
    catalog: ReviewedSkillCatalog,
    *,
    max_result_chars: int = 12_000,
    available_capabilities: frozenset[str] = frozenset(),
) -> tuple[FunctionTool, FunctionTool]:
    if not isinstance(catalog, ReviewedSkillCatalog):
        raise TypeError("reviewed skills require a frozen catalog")
    if type(max_result_chars) is not int or max_result_chars < 64:
        raise ValueError(
            "reviewed skill output budget must allow at least 64 characters"
        )
    if not isinstance(available_capabilities, frozenset) or any(
        not isinstance(item, str) for item in available_capabilities
    ):
        raise TypeError(
            "reviewed skill capabilities must be an operator-supplied frozenset"
        )

    def encode(value: dict) -> str:
        content = _json(value)
        if len(content) > max_result_chars:
            return _json({"success": False, "error": "reviewed_skill_output_too_large"})
        return content

    def status(metadata) -> dict:
        missing = sorted(set(metadata.required_capabilities) - available_capabilities)
        return {
            **metadata.to_public_dict(),
            "available": not missing,
            "missingCapabilities": missing,
        }

    async def list_execute(arguments: Mapping) -> str:
        _list_arguments(arguments)
        values = [status(metadata) for metadata in catalog.list_metadata()]
        return encode(
            {
                "success": True,
                "result": {
                    "skills": values,
                    "total": len(values),
                    "catalogDigest": catalog.digest,
                    "permissionEffect": "none",
                },
            }
        )

    async def load_execute(arguments: Mapping) -> str:
        arguments = _load_arguments(arguments)
        try:
            name = arguments["name"]
            document = catalog.load(name)
            if status(document.metadata)["missingCapabilities"]:
                return encode(
                    {
                        "success": False,
                        "error": "reviewed_skill_missing_capabilities",
                        "missingCapabilities": status(document.metadata)[
                            "missingCapabilities"
                        ],
                    }
                )
            if "resource" in arguments:
                result = catalog.read_resource(
                    name, arguments["resource"]
                ).to_public_dict()
            else:
                result = document.to_public_dict()
            return encode(
                {
                    "success": True,
                    "result": {
                        **result,
                        "catalogDigest": catalog.digest,
                        "permissionEffect": "none",
                    },
                }
            )
        except ReviewedSkillError:
            return _json({"success": False, "error": "reviewed_skill_not_found"})

    capabilities_digest = sha256_digest(
        _json({"availableCapabilities": sorted(available_capabilities)}).encode()
    )
    reference = f"Reviewed catalog {catalog.digest}; capabilities {capabilities_digest}. Instructions never grant tools, execution or network access. "
    return (
        FunctionTool(
            "skill_list",
            list_execute,
            _list_arguments,
            reference
            + "Discover approved skill metadata without loading bodies or references.",
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        FunctionTool(
            "skill_load",
            load_execute,
            _load_arguments,
            reference
            + "Read one approved skill body or an explicitly selected listed reference.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "resource": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
    )
