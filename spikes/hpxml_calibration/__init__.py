"""Spike A — OpenStudio-HPXML-Calibration hands-on evaluation.

Everything in this package is spike scaffolding: it is *not* imported by
``home_energy_twin`` and it is *not* a core dependency (RD-7).  The core
package and its test suite must pass with no engine installed.

The heavy engine (OpenStudio-HPXML-Calibration + its OpenStudio/EnergyPlus
runtime) is invoked **out of process** and is located through exactly one
named configuration mechanism: the environment variable
``HET_HPXML_CALIBRATION_BIN``.  When that variable is unset — or points at a
path that does not exist — the engine is *absent* and the adapter raises the
repository's shared typed error ``home_energy_twin.errors.EngineUnavailable``.

See ``spikes/hpxml_calibration/SPIKE_REPORT.md`` for the Phase A probe
evidence, the version pin, the SPEC §2.4 output-completeness checklist and
the hands-on script a capable machine would run.
"""

from __future__ import annotations

from .adapter import (
    ENGINE_BIN_ENV,
    PINNED_ENGINE_VERSION,
    HPXMLCalibrationAdapter,
    VersionPin,
    engine_bin_path,
    engine_available,
    parse_engine_version_output,
    parse_version_pin,
    require_engine,
)

__all__ = [
    "ENGINE_BIN_ENV",
    "PINNED_ENGINE_VERSION",
    "HPXMLCalibrationAdapter",
    "VersionPin",
    "engine_bin_path",
    "engine_available",
    "parse_engine_version_output",
    "parse_version_pin",
    "require_engine",
]
