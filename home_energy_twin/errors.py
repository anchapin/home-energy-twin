"""Typed errors that may cross a service boundary (SPEC §5).

No bare ``ValueError`` crosses a service boundary; adapters and services
raise these instead, so callers can degrade honestly.
"""

from __future__ import annotations

from typing import Optional

__all__ = [
    "EnergyTwinError",
    "EngineUnavailable",
    "InsufficientData",
    "ProvenanceMissing",
    "StaleCostData",
    "UnidentifiableConfiguration",
]


class EnergyTwinError(Exception):
    """Base class for all typed errors crossing a service boundary."""

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


class UnidentifiableConfiguration(EnergyTwinError):
    """The data cannot identify the requested configuration (LAW-3)."""

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message, reason=reason)


class InsufficientData(EnergyTwinError):
    """Not enough data in the requested window to do the job."""

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message, reason=reason)


class ProvenanceMissing(EnergyTwinError):
    """A series or cost figure reached a boundary without provenance (LAW-12)."""

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message, reason=reason)


class StaleCostData(EnergyTwinError):
    """Cost data is too old to be used for headline numbers (RD-10)."""

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message, reason=reason)


class EngineUnavailable(EnergyTwinError):
    """A heavy engine is not installed; core features keep working (RD-7)."""

    def __init__(self, message: str, *, reason: Optional[str] = None) -> None:
        super().__init__(message, reason=reason)
