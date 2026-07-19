"""Collect Slurm-specific visible configuration without treating it as policy."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def measure_slurm(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("Slurm measurements are planned for Milestone 5.")
