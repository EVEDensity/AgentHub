"""Static, credential-free errors for the experimental strict provider port."""


class StrictProviderError(RuntimeError):
    """The strict profile cannot return a billable model result safely."""


class StrictProviderReplayError(StrictProviderError):
    """An already claimed operation must never send a second HTTP request."""


class StrictProviderUsageError(StrictProviderError):
    """An accepted response has no trusted final token receipt."""
