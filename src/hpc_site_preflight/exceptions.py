"""Project-specific exception hierarchy."""


class PreflightError(Exception):
    """Base exception for expected HPC Site Preflight failures."""


class FeatureNotImplementedError(PreflightError):
    """Raised when a planned milestone has not yet been implemented."""


class ConfigurationError(PreflightError):
    """Raised when command or configuration input is invalid."""


class FixtureLoadError(PreflightError):
    """Raised when a reviewed fixture cannot be read or decoded."""


class FixtureValidationError(PreflightError):
    """Raised when fixture contents do not match the requested site or contract."""
