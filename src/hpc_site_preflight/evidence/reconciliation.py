"""Apply field-specific evidence precedence while preserving conflicts."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def reconcile_evidence(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("Evidence reconciliation is planned for Milestone 6.")
