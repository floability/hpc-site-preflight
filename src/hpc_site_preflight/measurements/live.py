"""Capture structured measurements on the current HPC login node."""

from collections.abc import Sequence

from pydantic import ValidationError

from hpc_site_preflight.exceptions import MeasurementValidationError
from hpc_site_preflight.measurements.base import MeasurementBundle, MeasurementProvider
from hpc_site_preflight.measurements.capture import collect_measurements
from hpc_site_preflight.reporting.tracker import RunTracker


class LiveMeasurementProvider(MeasurementProvider):
    """Capture and validate the reviewed structured login facts."""

    def __init__(
        self,
        site_name: str,
        *,
        keywords: Sequence[str] = (),
        documentation_domains: Sequence[str] = (),
        condor_pool: str | None = None,
        storage_paths: Sequence[str] = (),
        storage_roots: Sequence[str] = (),
    ) -> None:
        self.site_name = site_name
        self.keywords = list(keywords)
        self.documentation_domains = list(documentation_domains)
        self.condor_pool = condor_pool
        self.storage_paths = list(storage_paths)
        self.storage_roots = list(storage_roots)

    def collect(self, tracker: RunTracker) -> MeasurementBundle:
        with tracker.stage("login_measurement_collect"):
            payload = collect_measurements(
                site_name=self.site_name,
                keywords=self.keywords,
                domain_overrides=self.documentation_domains,
                condor_pool=self.condor_pool,
                storage_paths=self.storage_paths,
                storage_roots=self.storage_roots,
            )
        with tracker.stage("login_measurement_validate"):
            try:
                return MeasurementBundle.model_validate(payload)
            except ValidationError as exc:
                raise MeasurementValidationError(
                    f"Measured login facts failed {exc.error_count()} contract validation(s)."
                ) from exc
