"""Submit only predefined bounded pilots on Slurm or HTCondor."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.probes.base import PilotProvider, PilotResultBundle
from hpc_site_preflight.reporting.tracker import RunTracker


class LivePilotProvider(PilotProvider):
    """Run approved pilots in Milestone 8."""

    def collect(
        self, measurements: MeasurementBundle, tracker: RunTracker
    ) -> PilotResultBundle:
        raise FeatureNotImplementedError("Live pilot execution is planned for Milestone 8.")
