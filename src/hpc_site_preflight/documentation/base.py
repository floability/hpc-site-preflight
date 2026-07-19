"""Provider-neutral interface for documentation policy construction."""

from abc import ABC, abstractmethod

from hpc_site_preflight.documentation.models import ContextMode, DocumentationEvidence
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class DocumentationPolicyProvider(ABC):
    """Build validated documentation evidence behind one stable interface."""

    @abstractmethod
    def build(
        self,
        site: SiteInfo,
        tracker: RunTracker,
        *,
        context_mode: ContextMode,
    ) -> DocumentationEvidence:
        """Return documentation findings with exact local citations."""


__all__ = ["DocumentationEvidence", "DocumentationPolicyProvider"]
