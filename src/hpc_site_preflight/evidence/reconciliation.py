"""Small deterministic rule table used by later reconciliation."""

from fnmatch import fnmatchcase
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceType = Literal["measurement", "documentation", "pilot", "user"]
RuleAction = Literal[
    "run_pilot",
    "additional_documentation",
    "user_input",
    "admin_confirmation",
]


class FieldRule(BaseModel):
    """Allowed sources, precedence, and unresolved action for one field family."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    field_pattern: str
    allowed_sources: list[SourceType]
    precedence: list[SourceType]
    conflict_behavior: Literal["retain_note", "none"]
    unresolved_action: RuleAction
    action_id: str
    not_applicable_for: list[Literal["slurm", "htcondor"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_precedence(self) -> Self:
        if not set(self.precedence) <= set(self.allowed_sources):
            raise ValueError("rule precedence must use allowed sources.")
        return self


RULES = (
    FieldRule(
        rule_id="scheduler_identity",
        field_pattern="/scheduler_*",
        allowed_sources=["measurement", "documentation", "user"],
        precedence=["measurement", "documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="scheduler_identity_search",
    ),
    FieldRule(
        rule_id="slurm_visible_partition",
        field_pattern="/partitions/*/visible_walltime_seconds",
        allowed_sources=["measurement"],
        precedence=["measurement"],
        conflict_behavior="none",
        unresolved_action="additional_documentation",
        action_id="partition_visibility_search",
        not_applicable_for=["htcondor"],
    ),
    FieldRule(
        rule_id="documented_limit_over_visible_configuration",
        field_pattern="/partitions/*/maximum_walltime_seconds",
        allowed_sources=["documentation", "pilot", "measurement"],
        precedence=["documentation", "pilot", "measurement"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="partition_policy_search",
        not_applicable_for=["htcondor"],
    ),
    FieldRule(
        rule_id="measured_resource_group",
        field_pattern="/resource_groups/*",
        allowed_sources=["measurement", "documentation"],
        precedence=["measurement", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="resource_group_search",
        not_applicable_for=["slurm"],
    ),
    FieldRule(
        rule_id="measured_resource_shape",
        field_pattern="/resource_shapes/*",
        allowed_sources=["measurement", "documentation"],
        precedence=["measurement", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="resource_shape_search",
    ),
    FieldRule(
        rule_id="storage_login_observation",
        field_pattern="/storage/*/path",
        allowed_sources=["measurement", "documentation"],
        precedence=["measurement", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="user_input",
        action_id="storage_path_input",
    ),
    FieldRule(
        rule_id="storage_compute_behavior",
        field_pattern="/storage/*/compute_*",
        allowed_sources=["pilot", "documentation"],
        precedence=["pilot", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="run_pilot",
        action_id="shared_storage_visibility",
    ),
    FieldRule(
        rule_id="compute_network_behavior",
        field_pattern="/network/*",
        allowed_sources=["pilot", "documentation"],
        precedence=["pilot", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="run_pilot",
        action_id="compute_network_check",
    ),
    FieldRule(
        rule_id="visible_accounts",
        field_pattern="/accounting/visible_accounts",
        allowed_sources=["measurement", "documentation", "user"],
        precedence=["measurement", "documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="user_input",
        action_id="account_input",
    ),
    FieldRule(
        rule_id="allocation_requirement",
        field_pattern="/accounting/allocation_required",
        allowed_sources=["documentation", "user"],
        precedence=["documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="admin_confirmation",
        action_id="allocation_requirement_confirmation",
    ),
    FieldRule(
        rule_id="accounting_policy",
        field_pattern="/accounting/*",
        allowed_sources=["documentation", "user"],
        precedence=["documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="accounting_policy_search",
    ),
    FieldRule(
        rule_id="software_visibility",
        field_pattern="/software/*",
        allowed_sources=["measurement", "documentation"],
        precedence=["measurement", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="software_search",
    ),
)


def get_rule(field_path: str) -> FieldRule | None:
    """Return the first reviewed rule matching a profile field."""

    return next((rule for rule in RULES if fnmatchcase(field_path, rule.field_pattern)), None)


def select_preferred_source(
    rule: FieldRule, available_sources: set[SourceType]
) -> SourceType | None:
    """Select the highest-precedence available source."""

    return next((source for source in rule.precedence if source in available_sources), None)


def is_not_applicable(rule: FieldRule, scheduler: Literal["slurm", "htcondor"]) -> bool:
    """Return whether the field family does not apply to the scheduler."""

    return scheduler in rule.not_applicable_for
