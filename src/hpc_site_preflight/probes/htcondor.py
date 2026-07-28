"""HTCondor pilot class boundary for the next scheduler integration."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError
from hpc_site_preflight.probes.base import PilotInputs, PilotResultBundle, PilotRunner


class HTCondorPilot(PilotRunner):
    """Reserve the same runner interface for the working HTCondor prototype."""

    def run(self, inputs: PilotInputs) -> PilotResultBundle:
        """Refuse until the standalone HTCondor pilot is integrated."""

        raise FeatureNotImplementedError(
            "HTCondor pilot integration is not implemented yet."
        )
