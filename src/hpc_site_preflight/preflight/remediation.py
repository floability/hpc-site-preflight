"""Short deterministic remediation messages for preflight issues."""


def remediation_for(issue: str) -> str:
    """Return one fixed remediation for a reviewed issue category."""

    messages = {
        "scheduler": "Select a site with this scheduler or remove the explicit batch type.",
        "resource": (
            "Reduce the per-worker request or choose a site with a matching resource shape."
        ),
        "disk": "Measure or document compute-node disk capacity before submission.",
        "network": "Run the approved network pilot or choose a verified connection setting.",
        "required_option": "Supply the missing site-specific scheduler value and rerun preflight.",
        "unsupported_option": (
            "Remove the unsupported scheduler option from the Floability command."
        ),
    }
    return messages[issue]
