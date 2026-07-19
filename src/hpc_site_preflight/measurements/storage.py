"""Measure visible storage paths, capacity, writability, symlinks, and hard links."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def measure_storage(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("Storage measurements are planned for Milestone 5.")
