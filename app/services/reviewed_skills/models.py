"""Immutable reviewed content values; capability declarations grant nothing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


class ReviewedSkillError(ValueError):
    """A static, safe reason why reviewed content cannot be consumed."""


def skill_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None
    ):
        raise ReviewedSkillError("reviewed skill name is invalid")
    return value


def resource_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"references/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.md", value)
        is None
    ):
        raise ReviewedSkillError("reviewed skill resource path is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ReviewedSkillMetadata:
    name: str
    description: str
    version: str
    license: str
    source_url: str
    source_revision: str
    package_digest: str
    required_capabilities: tuple[str, ...]
    compatibility: str = ""
    allowed_tools: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def to_public_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "license": self.license,
            "sourceUrl": self.source_url,
            "sourceRevision": self.source_revision,
            "packageDigest": self.package_digest,
            "requiredCapabilities": list(self.required_capabilities),
            "compatibility": self.compatibility,
            "allowedTools": self.allowed_tools,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ReviewedSkillDocument:
    metadata: ReviewedSkillMetadata
    body: str = field(repr=False)
    skill_md_digest: str
    resource_paths: tuple[str, ...]

    def to_public_dict(self) -> dict:
        return {
            **self.metadata.to_public_dict(),
            "body": self.body,
            "skillMdDigest": self.skill_md_digest,
            "resourcePaths": list(self.resource_paths),
        }


@dataclass(frozen=True, slots=True)
class ReviewedSkillResource:
    skill_name: str
    relative_path: str
    content: str = field(repr=False)
    digest: str
    size_bytes: int
    package_digest: str

    def to_public_dict(self) -> dict:
        return {
            "name": self.skill_name,
            "path": self.relative_path,
            "content": self.content,
            "digest": self.digest,
            "sizeBytes": self.size_bytes,
            "packageDigest": self.package_digest,
        }


@dataclass(frozen=True, slots=True)
class _FrozenSkill:
    document: ReviewedSkillDocument
    resources: Mapping[str, ReviewedSkillResource] = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "resources", MappingProxyType(dict(self.resources)))


@dataclass(frozen=True, slots=True)
class ReviewedSkillCatalog:
    digest: str
    _skills: Mapping[str, _FrozenSkill] = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_skills", MappingProxyType(dict(self._skills)))

    def list_metadata(self) -> tuple[ReviewedSkillMetadata, ...]:
        return tuple(
            self._skills[name].document.metadata for name in sorted(self._skills)
        )

    def load(self, name: str) -> ReviewedSkillDocument:
        return self._find(name).document

    def read_resource(self, name: str, relative_path: str) -> ReviewedSkillResource:
        path = resource_path(relative_path)
        skill = self._find(name)
        if path not in skill.resources:
            raise ReviewedSkillError("reviewed skill resource is not listed")
        return skill.resources[path]

    def _find(self, name: str) -> _FrozenSkill:
        name = skill_name(name)
        if name not in self._skills:
            raise ReviewedSkillError("reviewed skill is not listed")
        return self._skills[name]
