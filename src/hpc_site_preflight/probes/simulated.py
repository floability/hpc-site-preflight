"""Load simulated pilot results when running from a laptop."""

from pathlib import Path

from hpc_site_preflight.exceptions import FeatureNotImplementedError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.probes.base import PilotProvider, PilotResultBundle
from hpc_site_preflight.reporting.tracker import RunTracker


class SimulatedPilotProvider(PilotProvider):
    """Read simulated pilot results in a future milestone."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def collect(
        self, measurements: MeasurementBundle, tracker: RunTracker
    ) -> PilotResultBundle:
        raise FeatureNotImplementedError("Simulated pilot ingestion is not implemented yet.")
