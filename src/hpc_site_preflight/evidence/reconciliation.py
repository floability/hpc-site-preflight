"""Small deterministic rule table used by later reconciliation."""

from fnmatch import fnmatchcase
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceType = Literal["measurement", "documentation", "pilot", "user"]
RuleAction = Literal[
    "login_measurement",
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
        rule_id="documented_submission_requirement",
        field_pattern="/slurm/options/*/required",
        allowed_sources=["documentation", "user"],
        precedence=["documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="submission_policy_search",
    ),
    FieldRule(
        rule_id="documented_submission_option",
        field_pattern="/slurm/options/*/*",
        allowed_sources=["documentation", "user"],
        precedence=["documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="submission_policy_search",
        not_applicable_for=["htcondor"],
    ),
    FieldRule(
        rule_id="documented_htcondor_submission_requirement",
        field_pattern="/htcondor/submit_attributes/*/required",
        allowed_sources=["documentation", "user"],
        precedence=["documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="submission_policy_search",
        not_applicable_for=["slurm"],
    ),
    FieldRule(
        rule_id="documented_htcondor_submission_option",
        field_pattern="/htcondor/submit_attributes/*/*",
        allowed_sources=["documentation", "user"],
        precedence=["documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="submission_policy_search",
        not_applicable_for=["slurm"],
    ),
    FieldRule(
        rule_id="unmapped_slurm_submission_requirement",
        field_pattern="/slurm/unmapped_options",
        allowed_sources=["documentation"],
        precedence=["documentation"],
        conflict_behavior="retain_note",
        unresolved_action="admin_confirmation",
        action_id="submission_option_mapping",
        not_applicable_for=["htcondor"],
    ),
    FieldRule(
        rule_id="unmapped_htcondor_submission_requirement",
        field_pattern="/htcondor/unmapped_submit_attributes",
        allowed_sources=["documentation"],
        precedence=["documentation"],
        conflict_behavior="retain_note",
        unresolved_action="admin_confirmation",
        action_id="submission_option_mapping",
        not_applicable_for=["slurm"],
    ),
    FieldRule(
        rule_id="documented_limit_over_visible_configuration",
        field_pattern="/slurm/partitions/*/maximum_walltime_seconds",
        allowed_sources=["documentation", "pilot", "measurement"],
        precedence=["documentation", "pilot", "measurement"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="partition_policy_search",
        not_applicable_for=["htcondor"],
    ),
    FieldRule(
        rule_id="documented_htcondor_runtime_policy",
        field_pattern="/htcondor/guaranteed_runtime",
        allowed_sources=["documentation"],
        precedence=["documentation"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="htcondor_runtime_policy_search",
        not_applicable_for=["slurm"],
    ),
    FieldRule(
        rule_id="documented_htcondor_preemption_policy",
        field_pattern="/htcondor/preemptible",
        allowed_sources=["documentation"],
        precedence=["documentation"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="htcondor_runtime_policy_search",
        not_applicable_for=["slurm"],
    ),
    FieldRule(
        rule_id="measured_htcondor_resources",
        field_pattern="/htcondor/*",
        allowed_sources=["measurement", "documentation"],
        precedence=["measurement", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="resource_group_search",
        not_applicable_for=["slurm"],
    ),
    FieldRule(
        rule_id="measured_slurm_partition_resource",
        field_pattern="/slurm/partitions/*/*",
        allowed_sources=["measurement", "documentation"],
        precedence=["measurement", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="partition_resource_search",
        not_applicable_for=["htcondor"],
    ),
    FieldRule(
        rule_id="storage_login_observation",
        field_pattern="/storage/*/path_pattern",
        allowed_sources=["measurement", "documentation"],
        precedence=["measurement", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="login_measurement",
        action_id="storage_path_input",
    ),
    FieldRule(
        rule_id="storage_login_access",
        field_pattern="/storage/*/login_*",
        allowed_sources=["measurement", "documentation"],
        precedence=["measurement", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="login_measurement",
        action_id="storage_login_access",
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
        rule_id="shared_storage_behavior",
        field_pattern="/storage/*/shared_across_compute_nodes",
        allowed_sources=["pilot", "documentation"],
        precedence=["pilot", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="run_pilot",
        action_id="shared_storage_visibility",
    ),
    FieldRule(
        rule_id="storage_retention_policy",
        field_pattern="/storage/*/purge_after_days",
        allowed_sources=["documentation", "user"],
        precedence=["documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="storage_policy_search",
    ),
    FieldRule(
        rule_id="documented_storage_policy",
        field_pattern="/storage/*/*",
        allowed_sources=["documentation", "pilot", "user"],
        precedence=["pilot", "documentation", "user"],
        conflict_behavior="retain_note",
        unresolved_action="additional_documentation",
        action_id="storage_policy_search",
    ),
    FieldRule(
        rule_id="login_network_observation",
        field_pattern="/network/login/*",
        allowed_sources=["measurement", "documentation"],
        precedence=["measurement", "documentation"],
        conflict_behavior="retain_note",
        unresolved_action="login_measurement",
        action_id="login_network_check",
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
