"""Determine freshness per profile field rather than per whole file."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def assess_freshness(*args: object, **kwargs: object) -> object:
    """Assess field freshness; implemented in Milestone 3."""

    raise FeatureNotImplementedError("Field freshness is planned for Milestone 3.")
