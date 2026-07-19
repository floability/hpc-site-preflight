"""Choose the next approved evidence action for unresolved profile fields."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def run_controller(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("The agentic controller is planned for Milestone 11.")
