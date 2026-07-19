"""Load reviewed pilot-result fixtures when running from a laptop."""

from pathlib import Path

from hpc_site_preflight.exceptions import FeatureNotImplementedError
from hpc_site_preflight.probes.base import PilotProvider, PilotResultBundle
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class FixturePilotProvider(PilotProvider):
    """Read fixture pilot results in Milestone 27."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def collect(self, site: SiteInfo, tracker: RunTracker) -> PilotResultBundle:
        raise FeatureNotImplementedError("Fixture pilot ingestion is planned for Milestone 27.")
