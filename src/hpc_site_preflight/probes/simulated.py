"""Load flat partial pilot results for simulated-site runs."""

import json
from pathlib import Path

from hpc_site_preflight.exceptions import ConfigurationError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.probes.base import PilotProvider, PilotResultBundle
from hpc_site_preflight.reporting.tracker import RunTracker


class SimulatedPilotProvider(PilotProvider):
    """Ignore missing optional fields while validating pilot identity."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def collect(
        self, measurements: MeasurementBundle, tracker: RunTracker
    ) -> PilotResultBundle:
        with tracker.stage("simulated_pilot_load"):
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            bundle = PilotResultBundle.model_validate(payload)
        if bundle.site_id != measurements.site_id:
            raise ConfigurationError(
                "Pilot result site_id does not match the login measurements."
            )
        if bundle.scheduler != measurements.scheduler_type:
            raise ConfigurationError(
                "Pilot result scheduler does not match the login measurements."
            )
        bundle.source_reference = str(self.path)
        return bundle
