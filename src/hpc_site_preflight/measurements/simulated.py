"""Load simulated measurements when running from a laptop."""

import json
from pathlib import Path

from pydantic import ValidationError

from hpc_site_preflight.exceptions import SimulationLoadError, SimulationValidationError
from hpc_site_preflight.measurements.base import MeasurementBundle, MeasurementProvider
from hpc_site_preflight.reporting.tracker import RunTracker


class SimulatedMeasurementProvider(MeasurementProvider):
    """Read and validate one simulated measurement file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def collect(self, tracker: RunTracker) -> MeasurementBundle:
        with tracker.stage("simulated_measurement_load"):
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise SimulationLoadError(
                    f"Could not read simulated measurements: {self.path}"
                ) from exc
            except json.JSONDecodeError as exc:
                raise SimulationLoadError(
                    "Simulated measurements are invalid JSON at "
                    f"line {exc.lineno}, column {exc.colno}."
                ) from exc

        with tracker.stage("simulated_measurement_validate"):
            try:
                bundle = MeasurementBundle.model_validate(payload)
            except ValidationError as exc:
                raise SimulationValidationError(
                    f"Simulated measurements failed {exc.error_count()} contract validation(s)."
                ) from exc

        return bundle
