"""Generate deterministic remediation text from known issue categories."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def remediation_for(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("Remediation rules are planned for Milestone 10.")
