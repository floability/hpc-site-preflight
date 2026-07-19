"""Bounded controller state, budgets, and termination reason contracts."""

from pydantic import BaseModel, ConfigDict


class ControllerState(BaseModel):
    """Minimal placeholder for Milestone 11 controller state."""

    model_config = ConfigDict(extra="forbid")

    actions_taken: int = 0
    maximum_actions: int = 10
    termination_reason: str | None = None
