"""Application-level paths and runtime configuration."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class AppConfig(BaseModel):
    """Shared runtime configuration for one CLI command."""

    model_config = ConfigDict(extra="forbid")

    run_dir: Path = Field(default=Path("runs"))
    quiet: bool = False
