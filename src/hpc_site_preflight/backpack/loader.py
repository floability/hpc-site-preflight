"""Load a backpack and normalize its deployment requirements."""

from pathlib import Path

from hpc_site_preflight.backpack.models import BackpackRequirements
from hpc_site_preflight.exceptions import FeatureNotImplementedError


def load_backpack(path: Path) -> BackpackRequirements:
    """Load backpack requirements; implemented in Milestone 9."""

    raise FeatureNotImplementedError("Backpack loading is planned for Milestone 9.")
