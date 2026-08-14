from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class AgentCatalogBindingPutRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )

    adapter_type: Annotated[
        str,
        Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$"),
    ]
    capabilities: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(
        default_factory=list,
        max_length=256,
    )
    enabled: bool = True
    expected_version: Annotated[int, Field(ge=0, le=2_147_483_646)]
