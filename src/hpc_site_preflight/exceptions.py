"""Project-specific exception hierarchy."""


class PreflightError(Exception):
    """Base exception for expected HPC Site Preflight failures."""


class FeatureNotImplementedError(PreflightError):
    """Raised when a planned milestone has not yet been implemented."""


class ConfigurationError(PreflightError):
    """Raised when command or configuration input is invalid."""


class SimulationLoadError(PreflightError):
    """Raised when simulated evidence cannot be read or decoded."""


class SimulationValidationError(PreflightError):
    """Raised when simulated evidence does not match the site or contract."""


class ModelProviderError(PreflightError):
    """Raised when a model request or structured response is invalid."""


class DocumentationError(PreflightError):
    """Raised when bounded documentation processing cannot validate an operation."""
