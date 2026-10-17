"""Bounded ASCII-name Agent Skills subset, not a universal format validator."""

from __future__ import annotations

from dataclasses import dataclass

import yaml
from yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)

from .models import ReviewedSkillError, skill_name

MAX_TEXT_BYTES = 131_072
MAX_FRONTMATTER_BYTES = 8192
_FIELDS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)


def canonical_text_bytes(content: bytes) -> bytes:
    if not isinstance(content, bytes) or len(content) > MAX_TEXT_BYTES:
        raise ReviewedSkillError("reviewed skill text exceeds its byte bound")
    invalid = False
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        invalid = True
    if invalid:
        raise ReviewedSkillError("reviewed skill text must be UTF-8")
    if text.startswith("\ufeff") or "\x00" in text:
        raise ReviewedSkillError("reviewed skill text must not contain BOM or NUL")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


class _UniqueSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ReviewedSkillError(
                    "reviewed skill frontmatter has duplicate or non-string keys"
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _bounded_yaml(text: str) -> dict:
    depth = 0
    for count, event in enumerate(yaml.parse(text), 1):
        if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
            raise ReviewedSkillError(
                "reviewed skill YAML anchors and aliases are unsupported"
            )
        if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
            depth += 1
        if isinstance(event, (MappingEndEvent, SequenceEndEvent)):
            depth -= 1
        if count > 256 or depth > 4:
            raise ReviewedSkillError(
                "reviewed skill frontmatter exceeds structure bounds"
            )
    parsed = yaml.load(text, Loader=_UniqueSafeLoader)
    if not isinstance(parsed, dict) or not set(parsed) <= _FIELDS:
        raise ReviewedSkillError("reviewed skill frontmatter fields are unsupported")
    return parsed


def _text(value: object, maximum: int, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ReviewedSkillError("reviewed skill frontmatter text is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ParsedSkill:
    name: str
    description: str
    version: str
    license: str
    compatibility: str
    allowed_tools: str
    metadata: tuple[tuple[str, str], ...]
    body: str


def _parsed_fields(values: dict, body: str, expected_name: str) -> ParsedSkill:
    name = skill_name(values.get("name"))
    if name != expected_name:
        raise ReviewedSkillError("reviewed skill name differs from its directory")
    metadata = values.get("metadata")
    if not isinstance(metadata, dict) or not 1 <= len(metadata) <= 16:
        raise ReviewedSkillError("reviewed skill metadata requires an explicit version")
    if any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or len(key) > 64
        or len(value) > 1024
        for key, value in metadata.items()
    ):
        raise ReviewedSkillError(
            "reviewed skill metadata must map bounded strings to strings"
        )
    return ParsedSkill(
        name,
        _text(values.get("description"), 1024),
        _text(metadata.get("version"), 64),
        _text(values.get("license"), 255),
        _text(values.get("compatibility"), 500, required=False),
        _text(values.get("allowed-tools"), 2048, required=False),
        tuple(sorted(metadata.items())),
        body,
    )


def parse_skill(content: bytes, *, expected_name: str) -> ParsedSkill:
    content = canonical_text_bytes(content)
    text = content.decode("utf-8")
    if not text.startswith("---\n"):
        raise ReviewedSkillError("reviewed skill requires YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0 or len(text[4:end].encode()) > MAX_FRONTMATTER_BYTES:
        raise ReviewedSkillError("reviewed skill frontmatter boundary is invalid")
    body = text[end + 5 :].strip()
    if not body:
        raise ReviewedSkillError("reviewed skill body is empty")
    invalid = False
    try:
        values = _bounded_yaml(text[4:end])
    except (yaml.YAMLError, RecursionError):
        invalid = True
    if invalid:
        raise ReviewedSkillError("reviewed skill YAML is invalid")
    return _parsed_fields(values, body, expected_name)
