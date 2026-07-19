"""HTCondor submission templates for approved pilot jobs."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def build_htcondor_pilot(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("HTCondor pilot templates are planned for Milestone 8.")
