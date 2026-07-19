"""Create stable evidence identifiers and detailed provenance references."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError


def build_provenance(*args: object, **kwargs: object) -> object:
    raise FeatureNotImplementedError("Detailed provenance is planned for Milestone 6.")
