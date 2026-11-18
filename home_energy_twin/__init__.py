"""home-energy-twin core domain package.

Phase 1 foundation (SPEC §2–§3, §5):

* :mod:`home_energy_twin.types` — pydantic v2 domain models.
* :mod:`home_energy_twin.protocols` — PEP 544 service Protocols.
* :mod:`home_energy_twin.errors` — typed errors that may cross a service boundary.
* :mod:`home_energy_twin.timebase` — UTC/local time plumbing (LAW-5).
* :mod:`home_energy_twin.intervals` — interval and billing-period arithmetic (RD-6).

This package contains no I/O, no engine imports, no telemetry and no actuation
(LAW-6, LAW-7).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
