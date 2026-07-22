"""Typed site descriptor used to start evidence acquisition."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DocumentationScope(BaseModel):
    """Bounded documentation-discovery scope for one site."""

    model_config = ConfigDict(extra="forbid")

    allowed_domains: list[str]
    preferred_path_tokens: list[str]


class SiteDescriptor(BaseModel):
    """Minimal site identity and bounded documentation-discovery hints."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"]
    site_id: str = Field(min_length=1)
    site_name: str = Field(min_length=1)
    scheduler: Literal["slurm", "htcondor", "unknown"]
    aliases: list[str]
    hostname_patterns: list[str]
    documentation: DocumentationScope
