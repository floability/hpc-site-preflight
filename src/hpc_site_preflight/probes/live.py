"""Run approved scheduler pilots using measured site inputs."""

from pathlib import Path

from hpc_site_preflight.exceptions import ConfigurationError, FeatureNotImplementedError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.probes.base import PilotInputs, PilotProvider, PilotResultBundle
from hpc_site_preflight.probes.slurm import SlurmPilot
from hpc_site_preflight.reporting.tracker import RunTracker


class LivePilotProvider(PilotProvider):
    """Derive safe pilot inputs from login measurements and run Slurm."""

    def __init__(
        self,
        output: Path,
        runs_dir: Path,
        *,
        start_port: int = 9000,
        coordination_timeout: float = 3600.0,
    ) -> None:
        self.output = output
        self.runs_dir = runs_dir
        self.start_port = start_port
        self.coordination_timeout = coordination_timeout

    def collect(
        self, measurements: MeasurementBundle, tracker: RunTracker
    ) -> PilotResultBundle:
        if measurements.scheduler_type == "htcondor":
            raise FeatureNotImplementedError(
                "HTCondor pipeline pilots are not implemented yet."
            )
        if measurements.scheduler_type != "slurm":
            raise ConfigurationError("The live pilot requires measured Slurm facts.")
        login_host = measurements.site_facts.fqdn or measurements.site_facts.hostname
        if login_host is None:
            raise ConfigurationError("The live pilot requires a measured login hostname.")

        storage = {
            name: location.observed_path
            for name, location in measurements.storage.items()
            if name in {"home", "scratch", "project"}
            and location.observed_path is not None
            and location.exists is True
        }
        inputs = PilotInputs(
            site_id=measurements.site_id,
            scheduler="slurm",
            login_host=login_host,
            storage=storage,
            output=self.output,
            runs_dir=self.runs_dir,
            start_port=self.start_port,
            coordination_timeout=self.coordination_timeout,
        )
        with tracker.stage("approved_slurm_pilot"):
            result = SlurmPilot().run(inputs)
            tracker.add_artifact(kind="pilot_result", path=self.output)
        return result
