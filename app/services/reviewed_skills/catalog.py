"""Read and freeze only an explicit operator index and its bounded text files."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .frontmatter import MAX_TEXT_BYTES, canonical_text_bytes, parse_skill
from .models import (
    ReviewedSkillCatalog,
    ReviewedSkillDocument,
    ReviewedSkillError,
    ReviewedSkillMetadata,
    ReviewedSkillResource,
    _FrozenSkill,
    resource_path,
    skill_name,
)


def _canonical_json(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def sha256_digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def package_digest(file_entries) -> str:
    return sha256_digest(
        _canonical_json(sorted(file_entries, key=lambda entry: entry["path"]))
    )


Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=255)]


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _File(_Schema):
    path: str
    sizeBytes: Annotated[int, Field(ge=1, le=MAX_TEXT_BYTES)]
    sha256: Digest

    @field_validator("path")
    @classmethod
    def supported_path(cls, value):
        return value if value in {"SKILL.md", "LICENSE"} else resource_path(value)


class _Skill(_Schema):
    name: str
    version: Text
    license: Text
    sourceUrl: Annotated[str, Field(min_length=1, max_length=2048)]
    sourceRevision: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    requiredCapabilities: Annotated[list[Text], Field(max_length=16)]
    files: Annotated[list[_File], Field(min_length=2, max_length=16)]
    packageDigest: Digest

    @model_validator(mode="after")
    def coherent_package(self):
        skill_name(self.name)
        paths = [entry.path for entry in self.files]
        if len(set(paths)) != len(paths) or not {"SKILL.md", "LICENSE"} <= set(paths):
            raise ReviewedSkillError("reviewed package file list is invalid")
        if len(set(self.requiredCapabilities)) != len(self.requiredCapabilities):
            raise ReviewedSkillError("reviewed package repeats a required capability")
        if any(
            re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", item) is None
            for item in self.requiredCapabilities
        ):
            raise ReviewedSkillError("reviewed package capability name is invalid")
        _source(self)
        return self


def _source(skill) -> None:
    value = urlsplit(skill.sourceUrl)
    suffix = f"/blob/{skill.sourceRevision}/app/services/reviewed_skills/packages/{skill.name}/SKILL.md"
    if (
        value.scheme != "https"
        or value.netloc != "github.com"
        or value.query
        or value.fragment
        or not value.path.endswith(suffix)
    ):
        raise ReviewedSkillError(
            "reviewed package source must bind its pinned GitHub content revision"
        )
    if (
        re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value.path[: -len(suffix)])
        is None
    ):
        raise ReviewedSkillError("reviewed package source repository is invalid")


class _Index(_Schema):
    schemaVersion: Literal[1]
    skills: Annotated[list[_Skill], Field(min_length=1, max_length=32)]

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value):
        if not isinstance(value, dict) or type(value.get("schemaVersion")) is not int:
            raise ReviewedSkillError("reviewed catalog requires integer schemaVersion")
        return value


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReviewedSkillError("reviewed catalog has duplicate JSON keys")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ReviewedSkillError("reviewed catalog contains a nonfinite value")


def _regular(entry) -> None:
    if isinstance(entry, Path):
        metadata = entry.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & 0x400
        ):
            raise ReviewedSkillError("reviewed catalog refuses linked paths")
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise ReviewedSkillError("reviewed catalog refuses hardlinked files")
        if not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISREG(metadata.st_mode):
            raise ReviewedSkillError("reviewed catalog requires regular text files")


def _read(entry) -> bytes:
    _regular(entry)
    with entry.open("rb") as stream:
        return canonical_text_bytes(stream.read(MAX_TEXT_BYTES + 1))


def _inventory(directory, prefix="") -> set[str]:
    _regular(directory)
    result = set()
    for entry in directory.iterdir():
        _regular(entry)
        relative = prefix + entry.name
        if entry.is_dir():
            if prefix or entry.name != "references":
                raise ReviewedSkillError("reviewed package directories are unsupported")
            result.update(_inventory(entry, "references/"))
        elif entry.is_file():
            result.add(relative)
        else:
            raise ReviewedSkillError("reviewed package contains a special file")
        if len(result) > 16:
            raise ReviewedSkillError("reviewed package file count exceeds its bound")
    return result


def _frozen_skill(directory, specification: _Skill) -> _FrozenSkill:
    entries = [entry.model_dump() for entry in specification.files]
    if package_digest(entries) != specification.packageDigest:
        raise ReviewedSkillError("reviewed package file-list digest differs")
    if _inventory(directory) != {entry.path for entry in specification.files}:
        raise ReviewedSkillError("reviewed package inventory differs from its index")
    content = {}
    for entry in specification.files:
        value = _read(directory.joinpath(*entry.path.split("/")))
        if len(value) != entry.sizeBytes or sha256_digest(value) != entry.sha256:
            raise ReviewedSkillError("reviewed package text differs from its index")
        content[entry.path] = value
    parsed = parse_skill(content["SKILL.md"], expected_name=specification.name)
    if (parsed.version, parsed.license) != (
        specification.version,
        specification.license,
    ):
        raise ReviewedSkillError(
            "reviewed package version or license differs from frontmatter"
        )
    metadata = ReviewedSkillMetadata(
        parsed.name,
        parsed.description,
        parsed.version,
        parsed.license,
        specification.sourceUrl,
        specification.sourceRevision,
        specification.packageDigest,
        tuple(sorted(specification.requiredCapabilities)),
        parsed.compatibility,
        parsed.allowed_tools,
        parsed.metadata,
    )
    resources = {
        path: ReviewedSkillResource(
            metadata.name,
            path,
            value.decode(),
            sha256_digest(value),
            len(value),
            metadata.package_digest,
        )
        for path, value in content.items()
        if path.startswith("references/")
    }
    document = ReviewedSkillDocument(
        metadata,
        parsed.body,
        sha256_digest(content["SKILL.md"]),
        tuple(sorted(resources)),
    )
    return _FrozenSkill(document, resources)


def _load(root) -> ReviewedSkillCatalog:
    _regular(root)
    raw = json.loads(
        _read(root.joinpath("catalog.json")),
        object_pairs_hook=_unique_json,
        parse_constant=_reject_constant,
    )
    index = _Index.model_validate(raw)
    names = [skill.name for skill in index.skills]
    if len(names) != len(set(names)):
        raise ReviewedSkillError("reviewed catalog repeats a skill name")
    packages = root.joinpath("packages")
    _regular(packages)
    if {entry.name for entry in packages.iterdir()} != set(names):
        raise ReviewedSkillError("reviewed catalog package inventory differs")
    result = {
        skill.name: _frozen_skill(packages.joinpath(skill.name), skill)
        for skill in index.skills
    }
    return ReviewedSkillCatalog(
        sha256_digest(_canonical_json(index.model_dump())), result
    )


def load_catalog(root) -> ReviewedSkillCatalog:
    """Operator-only root injection; never populate this from model/API input."""
    invalid = False
    try:
        result = _load(Path(root) if isinstance(root, (str, Path)) else root)
    except (OSError, ValueError, TypeError, RecursionError, ValidationError):
        invalid = True
    if invalid:
        raise ReviewedSkillError("reviewed catalog is unavailable or invalid")
    return result


def load_bundled_catalog() -> ReviewedSkillCatalog:
    return load_catalog(files("app.services.reviewed_skills"))
