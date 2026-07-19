"""Load and validate site information from JSON."""

from pathlib import Path

from hpc_site_preflight.exceptions import FeatureNotImplementedError
from hpc_site_preflight.site_info.models import SiteInfo


def load_site_info(path: Path) -> SiteInfo:
    """Load site information; implemented in Milestone 2."""

    raise FeatureNotImplementedError("Site-info loading is planned for Milestone 2.")
