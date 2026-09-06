"""Shared evidence levels used by tests, CI, and documentation."""
from enum import StrEnum

class VerificationLevel(StrEnum):
    UNIT = "unit"
    CONTRACT = "contract"
    INTEGRATION = "integration"
    REAL_PROVIDER = "real-provider"
    REAL_TTY = "real-tty"
    CROSS_PLATFORM = "cross-platform"
    PRODUCTION = "production"

__all__ = ["VerificationLevel"]
