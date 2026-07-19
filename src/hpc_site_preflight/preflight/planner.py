"""Compare normalized backpack requirements with a candidate site profile."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def plan_preflight(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("The deterministic planner is planned for Milestone 10.")
