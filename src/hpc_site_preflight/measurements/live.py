"""Capture safe common measurements on a real HPC login node."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError
from hpc_site_preflight.measurements.base import MeasurementBundle, MeasurementProvider
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_descriptor.models import SiteDescriptor


class LiveMeasurementProvider(MeasurementProvider):
    """Capture live measurements in Milestone 5."""

    def collect(self, site: SiteDescriptor, tracker: RunTracker) -> MeasurementBundle:
        raise FeatureNotImplementedError("Live measurements are planned for Milestone 5.")
