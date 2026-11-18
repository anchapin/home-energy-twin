"""Out-of-process adapter skeleton for OpenStudio-HPXML-Calibration (Spike A).

This module is spike scaffolding living under ``spikes/`` only.  It is never
imported by ``home_energy_twin`` and it does **not** register a production
``Calibrator`` (AGENTS.md: "Heavy engines stay out of the core", RD-7).

Design constraints honoured here:

* **Out of process.** The engine is a separate executable; this module only
  writes a request file, runs the binary and reads a result file.  No
  in-process import of OpenStudio/EnergyPlus (they are multi-hundred-MB
  runtimes).
* **One named configuration mechanism.** The engine executable is located
  *only* through the environment variable ``HET_HPXML_CALIBRATION_BIN``.
  Unset or pointing at a nonexistent path means the engine is absent.
* **One shared error type.** When the engine is absent the adapter raises
  the repository's typed ``home_energy_twin.errors.EngineUnavailable`` — it
  does not invent a second error type (the ``spikes/ochre`` precedent shape
  is followed, but the shared error is used).
* **Ranges, not points.** Calibrated parameters are carried as
  ``ParameterEstimate`` ranges (LAW-1) and Guideline-14 metrics are kept
  separate from BPI-2400 acceptance metrics (LAW-2 / SPEC §2.4).
* **Synthetic data only.** No real user data is used or shipped (LAW-11).

The request/response contract below is *spike-local* and is what a capable
machine would exercise; the exact hand-off to the real Ruby/CLI tool is
listed in ``SPIKE_REPORT.md``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from home_energy_twin.errors import EngineUnavailable
from home_energy_twin.types import (
    CalibrationMetrics,
    CalibratedModel,
    Granularity,
    House,
    ParameterEstimate,
    TimeWindow,
    UtilitySeries,
    WeatherSeries,
)

__all__ = [
    "ENGINE_BIN_ENV",
    "PINNED_ENGINE_NAME",
    "PINNED_ENGINE_VERSION",
    "HPXMLCalibrationAdapter",
    "VersionPin",
    "engine_available",
    "engine_bin_path",
    "parse_calibration_payload",
    "parse_engine_version_output",
    "parse_version_pin",
    "require_engine",
]


ENGINE_BIN_ENV = "HET_HPXML_CALIBRATION_BIN"
"""Environment variable holding the path to the engine executable."""

PINNED_ENGINE_NAME = "OpenStudio-HPXML-Calibration"

PINNED_ENGINE_VERSION = "0.1"
"""The pinned candidate version (docs/PLAN.md line 114 — "v0.1")."""


# --------------------------------------------------------------------------
# locating the engine
# --------------------------------------------------------------------------


def engine_bin_path(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The configured engine executable, or ``None`` when the engine is absent.

    The engine is present only when ``HET_HPXML_CALIBRATION_BIN`` is set to a
    path that exists.  Anything else (unset, empty, whitespace, dangling
    path) means "engine absent".
    """
    source = os.environ if env is None else env
    raw = source.get(ENGINE_BIN_ENV)
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if not path.exists():
        return None
    return path


def engine_available(env: Optional[Mapping[str, str]] = None) -> bool:
    """True when the engine executable can be located."""
    return engine_bin_path(env) is not None


def require_engine(env: Optional[Mapping[str, str]] = None) -> Path:
    """Return the engine executable path or raise ``EngineUnavailable``.

    The engine is a heavy external dependency (RD-7): when it is missing the
    honest outcome is the shared typed error, not a guess and not a second
    bespoke error class.
    """
    path = engine_bin_path(env)
    if path is not None:
        return path
    source = os.environ if env is None else env
    raw = source.get(ENGINE_BIN_ENV)
    if raw is None or not raw.strip():
        reason = f"{ENGINE_BIN_ENV} is not set"
    else:
        reason = f"{ENGINE_BIN_ENV}={raw!r} does not point at an existing file"
    raise EngineUnavailable(
        f"{PINNED_ENGINE_NAME} engine is unavailable ({reason}); "
        f"expected an executable at the path named by {ENGINE_BIN_ENV}",
        reason=reason,
    )


# --------------------------------------------------------------------------
# version pin handling
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionPin:
    """A parsed ``major.minor[.patch]`` version pin."""

    raw: str
    major: int
    minor: int
    patch: Optional[int] = None

    def is_satisfied_by(self, actual: "VersionPin") -> bool:
        """True when ``actual`` is at least the pinned major.minor line.

        A pin of ``0.1`` is satisfied by ``0.1.0`` and by ``0.1.3``; it is
        *not* satisfied by ``0.2`` or by ``1.0``.
        """
        if actual.major != self.major:
            return False
        if actual.minor != self.minor:
            return False
        if self.patch is None:
            return True
        return actual.patch is not None and actual.patch >= self.patch


_VERSION_TOKEN = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version_pin(text: str) -> VersionPin:
    """Parse a version string such as ``"0.1"``, ``"v0.1"`` or ``"0.1.2"``.

    Raises ``ValueError`` when the text does not start with a version.
    """
    cleaned = text.strip().lstrip("vV")
    if not cleaned:
        raise ValueError("empty version string")
    match = _VERSION_TOKEN.match(cleaned)
    if match is None:
        raise ValueError(f"not a version string: {text!r}")
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) is not None else 0
    patch = int(match.group(3)) if match.group(3) is not None else None
    return VersionPin(raw=cleaned, major=major, minor=minor, patch=patch)


def parse_engine_version_output(stdout: str) -> Optional[str]:
    """Extract the engine version from a ``--version`` banner.

    Looks for the engine name followed by a version token; falls back to the
    first dotted version token in the output.  Returns ``None`` when no
    version can be found.
    """
    for line in stdout.splitlines():
        if PINNED_ENGINE_NAME.lower() in line.lower():
            match = _VERSION_TOKEN.search(line[line.lower().find(PINNED_ENGINE_NAME.lower()) :])
            if match is not None:
                return match.group(0)
    match = _VERSION_TOKEN.search(stdout)
    if match is None:
        return None
    return match.group(0)


def pin_satisfied_by(output: str, pin: str = PINNED_ENGINE_VERSION) -> bool:
    """True when a ``--version`` banner satisfies the pinned version line."""
    actual = parse_engine_version_output(output)
    if actual is None:
        return False
    try:
        return parse_version_pin(pin).is_satisfied_by(parse_version_pin(actual))
    except ValueError:  # pragma: no cover - defensive
        return False


# --------------------------------------------------------------------------
# the out-of-process adapter
# --------------------------------------------------------------------------


class HPXMLCalibrationAdapter:
    """Spike adapter: a ``Calibrator``-shaped facade over the engine binary.

    It deliberately implements the *shape* of
    ``home_energy_twin.protocols.Calibrator`` (``id``, ``version``,
    ``supported_granularities``, ``calibrate``) so the mapping can be
    exercised, but it is registered nowhere and is imported by no core
    module.
    """

    id = "openstudio-hpxml-calibration"
    version = PINNED_ENGINE_VERSION
    supported_granularities: set[Granularity] = {"billing_period", "daily"}

    def __init__(
        self,
        *,
        env: Optional[Mapping[str, str]] = None,
        timeout_s: float = 1800.0,
    ) -> None:
        self._env = env
        self._timeout_s = timeout_s

    # -- engine plumbing -------------------------------------------------

    def engine_path(self) -> Path:
        """The engine executable; raises ``EngineUnavailable`` when absent."""
        return require_engine(self._env)

    def engine_version(self) -> VersionPin:
        """Run the engine's ``--version`` banner and parse the version pin."""
        path = self.engine_path()
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=self._timeout_s,
            check=False,
        )
        banner = f"{completed.stdout}\n{completed.stderr}"
        raw = parse_engine_version_output(banner)
        if raw is None:
            raise EngineUnavailable(
                f"{PINNED_ENGINE_NAME} did not report a version in its --version banner",
                reason="no version token in --version output",
            )
        return parse_version_pin(raw)

    # -- calibration -----------------------------------------------------

    def calibrate(
        self,
        house: House,
        utility: Sequence[UtilitySeries],
        weather: WeatherSeries,
    ) -> CalibratedModel:
        """Run one calibration out of process and map the result to §2.4.

        Raises ``EngineUnavailable`` when the engine is absent; raises
        ``ValueError`` when the engine's result cannot be mapped onto the
        SPEC §2.4 field list.
        """
        path = self.engine_path()
        request = {
            "house": house.model_dump(mode="json"),
            "utility": [series.model_dump(mode="json") for series in utility],
            "weather": weather.model_dump(mode="json"),
        }
        with tempfile.TemporaryDirectory(prefix="hpxml-calibration-") as tmp:
            workdir = Path(tmp)
            request_path = workdir / "request.json"
            result_path = workdir / "result.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [
                    str(path),
                    "--request",
                    str(request_path),
                    "--output",
                    str(result_path),
                ],
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
            if completed.returncode != 0:
                raise EngineUnavailable(
                    f"{PINNED_ENGINE_NAME} exited with status {completed.returncode}",
                    reason=completed.stderr.strip()[:500],
                )
            if not result_path.exists():
                raise EngineUnavailable(
                    f"{PINNED_ENGINE_NAME} produced no result file",
                    reason="missing --output file",
                )
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        return parse_calibration_payload(payload)


def parse_calibration_payload(payload: Mapping[str, Any]) -> CalibratedModel:
    """Map an engine result payload onto the SPEC §2.4 ``CalibratedModel``.

    The key set below is the *documented* output contract of the engine and
    the checklist used in ``SPIKE_REPORT.md``.  Every §2.4 field must be
    present; a missing field is an error rather than a silent default.
    """
    missing = [
        key
        for key in (
            "house_id",
            "state_hash",
            "engine_ref",
            "hpxml_ref",
            "parameters",
            "metrics",
            "calibration_window",
            "data_granularity",
            "status",
        )
        if key not in payload
    ]
    if missing:
        raise ValueError(f"engine result is missing SPEC 2.4 fields: {sorted(missing)}")

    metrics_raw = dict(payload["metrics"])
    metrics = CalibrationMetrics(
        nmbe_pct=float(metrics_raw["nmbe_pct"]),
        cvrmse_pct=float(metrics_raw["cvrmse_pct"]),
        granularity=metrics_raw["granularity"],
        guideline14_pass=bool(metrics_raw["guideline14_pass"]),
    )
    window_raw = dict(payload["calibration_window"])
    window = TimeWindow(
        start_utc=window_raw["start_utc"],
        end_utc=window_raw["end_utc"],
    )
    hpxml_raw = dict(payload["hpxml_ref"])
    parameters = {
        name: ParameterEstimate(**dict(estimate))
        for name, estimate in dict(payload["parameters"]).items()
    }
    bpi2400 = payload.get("bpi2400_metrics")
    return CalibratedModel(
        house_id=payload["house_id"],
        state_hash=str(payload["state_hash"]),
        engine_ref=str(payload["engine_ref"]),
        hpxml_ref=hpxml_raw,  # type: ignore[arg-type]
        parameters=parameters,
        metrics=metrics,
        calibration_window=window,
        data_granularity=payload["data_granularity"],
        calibrator_id=str(payload.get("calibrator_id", HPXMLCalibrationAdapter.id)),
        calibrator_version=str(payload.get("calibrator_version", PINNED_ENGINE_VERSION)),
        status=payload["status"],
        rc_parameters=payload.get("rc_parameters"),
        bpi2400_metrics=(
            {str(k): float(v) for k, v in dict(bpi2400).items()} if bpi2400 is not None else None
        ),
    )
