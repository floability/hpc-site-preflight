"""Summarize HTCondor pool and ClassAd resource groups instead of partitions."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def measure_htcondor(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("HTCondor measurements are planned for Milestone 5.")
