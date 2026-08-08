from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class A2ATaskCreateRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    task_id: Annotated[str, Field(min_length=1, max_length=255)]
    workspace_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ]
    objective: Annotated[str, Field(min_length=1, max_length=10000)]
    agent_url: Annotated[str, Field(min_length=1, max_length=2048)]
    required_capabilities: list[
        Annotated[str, Field(min_length=1, max_length=255)]
    ] = Field(default_factory=list)
    time_seconds: Annotated[int, Field(ge=1, le=86400)] = 3600
    model_cost: Annotated[float, Field(ge=0)] = 10
    retries: Annotated[int, Field(ge=0, le=20)] = 2

    @field_validator("required_capabilities")
    @classmethod
    def validate_unique_capabilities(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("required capabilities must be unique")
        return value

    @field_validator("task_id", "objective")
    @classmethod
    def validate_non_whitespace_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value cannot contain only whitespace")
        return value

    @field_validator("agent_url")
    @classmethod
    def validate_agent_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("agentUrl must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("agentUrl cannot contain user information")
        if parsed.fragment:
            raise ValueError("agentUrl cannot contain a fragment")
        return value


class A2ATaskCancelRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    task_id: Annotated[str, Field(min_length=1, max_length=255)]
    workspace_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ]


class A2ATaskFailRequest(A2ATaskCancelRequest):
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
