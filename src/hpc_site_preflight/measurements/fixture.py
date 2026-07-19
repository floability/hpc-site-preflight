"""Load reviewed measurement fixtures when running from a laptop."""

import json
from pathlib import Path

from pydantic import ValidationError

from hpc_site_preflight.exceptions import FixtureLoadError, FixtureValidationError
from hpc_site_preflight.measurements.base import MeasurementBundle, MeasurementProvider
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class FixtureMeasurementProvider(MeasurementProvider):
    """Read and validate one reviewed measurement fixture."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def collect(self, site: SiteInfo, tracker: RunTracker) -> MeasurementBundle:
        with tracker.stage("fixture_measurement_load"):
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise FixtureLoadError(f"Could not read measurement fixture: {self.path}") from exc
            except json.JSONDecodeError as exc:
                raise FixtureLoadError(
                    f"Measurement fixture is invalid JSON at line {exc.lineno}, column {exc.colno}."
                ) from exc

        with tracker.stage("fixture_measurement_validate"):
            try:
                bundle = MeasurementBundle.model_validate(payload)
            except ValidationError as exc:
                raise FixtureValidationError(
                    f"Measurement fixture failed {exc.error_count()} contract validation(s)."
                ) from exc

            if bundle.source_mode != "fixture":
                raise FixtureValidationError("Fixture provider requires source_mode 'fixture'.")
            if bundle.site_id != site.site_id:
                raise FixtureValidationError(
                    f"Fixture site_id '{bundle.site_id}' does not match '{site.site_id}'."
                )
            if bundle.scheduler_type != site.scheduler:
                raise FixtureValidationError(
                    "Fixture scheduler "
                    f"'{bundle.scheduler_type}' does not match '{site.scheduler}'."
                )

        return bundle
