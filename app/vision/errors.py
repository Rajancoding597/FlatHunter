"""Typed errors raised by vision provider configuration and extraction."""


class VisionError(RuntimeError):
    """Base class for provider-neutral vision failures."""


class VisionConfigurationError(VisionError):
    """The selected provider cannot be initialized from configuration."""


class VisionInputError(VisionError):
    """The information inputs cannot be sent to the selected provider."""


class VisionProviderError(VisionError):
    """A provider request failed before a validated extraction was produced."""

    def __init__(self, message: str, *, provider: str, code: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code


class VisionJSONModeError(VisionProviderError):
    """Groq server-side JSON validation rejected the model generation."""


class VisionValidationError(VisionProviderError):
    """Provider output was not valid FlatHunterExtractionV1 data."""
