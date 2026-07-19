"""Field-level resource, scheduler, storage, and network compatibility checks."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def check_compatibility(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("Compatibility checks are planned for Milestone 10.")
