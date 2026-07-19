"""Project-specific exception hierarchy."""


class PreflightError(Exception):
    """Base exception for expected HPC Site Preflight failures."""


class FeatureNotImplementedError(PreflightError):
    """Raised when a planned milestone has not yet been implemented."""


class ConfigurationError(PreflightError):
    """Raised when command or configuration input is invalid."""
