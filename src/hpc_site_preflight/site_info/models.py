"""Typed site identity and documentation-scope information."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SiteInfo(BaseModel):
    """Stable identity inputs used to scope evidence collection."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    site_id: str
    site_name: str
    hostname: str | None = None
    scheduler_hint: Literal["slurm", "htcondor", "unknown"] = "unknown"
    aliases: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    preferred_path_tokens: list[str] = Field(default_factory=list)
    excluded_site_tokens: list[str] = Field(default_factory=list)
