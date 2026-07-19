"""Load captured measurement evidence when running from a laptop."""

from pathlib import Path

from hpc_site_preflight.exceptions import FeatureNotImplementedError
from hpc_site_preflight.measurements.base import MeasurementBundle, MeasurementProvider
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class ReplayMeasurementProvider(MeasurementProvider):
    """Read a measurement bundle from JSON in Milestone 2."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def collect(self, site: SiteInfo, tracker: RunTracker) -> MeasurementBundle:
        raise FeatureNotImplementedError("Replay measurements are planned for Milestone 2.")
