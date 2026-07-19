"""Adapter for tested components migrated from `hpc-site-policy-agent`."""

from hpc_site_preflight.documentation.base import DocumentationEvidence, DocumentationPolicyProvider
from hpc_site_preflight.exceptions import FeatureNotImplementedError
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class PolicyAgentAdapter(DocumentationPolicyProvider):
    """Wrap the existing policy-agent subsystem in Milestone 4."""

    def build(
        self,
        site: SiteInfo,
        tracker: RunTracker,
        *,
        context_mode: str,
    ) -> DocumentationEvidence:
        raise FeatureNotImplementedError(
            "Documentation-agent migration is planned for Milestone 4."
        )
