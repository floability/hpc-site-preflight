"""Typed identifiers for evidence actions the controller may select."""

from enum import StrEnum


class EvidenceAction(StrEnum):
    """Approved high-level actions; implementations remain deterministic."""

    LOAD_PROFILE = "load_profile"
    MEASURE_LOGIN = "measure_login"
    READ_DOCUMENTATION = "read_documentation"
    RUN_APPROVED_PILOT = "run_approved_pilot"
    REQUEST_USER_INPUT = "request_user_input"
    REQUEST_ADMIN_CONFIRMATION = "request_admin_confirmation"
    STOP_READY = "stop_ready"
    STOP_BLOCKED = "stop_blocked"
