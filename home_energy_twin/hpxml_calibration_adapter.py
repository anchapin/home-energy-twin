"""Production adapter for the OpenStudio-HPXML-Calibration engine (issue #8).

This module promotes the Spike A adapter (the spike-tree
``hpxml_calibration/adapter.py``, pinned findings in that tree's
``SPIKE_REPORT.md``) into a production
:class:`home_energy_twin.protocols.Calibrator` implementation that lives in the
core package but is **never imported by core code** — selection is by
configuration (AGENTS.md: "Protocols (PEP 544) for every adapter boundary ...
no conditional imports of adapters inside core code").

Design rules honoured here:

* **Out of process only.** The engine is a separate executable (or a container
  wrapper script that fronts one).  This module writes a request file, runs
  the binary and reads a result file.  There is *no* in-process import of
  OpenStudio / EnergyPlus / HPXML-Calibration anywhere in ``home_energy_twin``
  (AGENTS.md: "Heavy engines stay out of the core").
* **Absent engine degrades honestly.** When the engine is missing, the adapter
  raises the repository's typed error
  :class:`home_energy_twin.errors.EngineUnavailable` — it never crashes the
  core and never fabricates a result (RD-7).
* **Version pin.** The engine is pinned to the Spike A candidate version
  ``0.1`` (``docs/PLAN.md:114`` — "OpenStudio-HPXML-Calibration (BPI-2400,
  v0.1, incomplete impl.)").  A pin of ``0.1`` is satisfied by the ``0.1.x``
  line only; a drifted engine is refused with a typed error.
* **One named configuration mechanism.** The engine executable is located
  *only* through the environment variable ``HET_HPXML_CALIBRATION_BIN``.
* **Ranges, not points.** Calibrated parameters are carried as
  ``ParameterEstimate`` ranges (LAW-1).  Guideline-14 metrics and BPI-2400
  acceptance metrics are kept in separate fields and never conflated
  (LAW-2 / SPEC §2.4).
* **Synthetic data only.** No real user data is used or shipped (LAW-11).

Typical use::

    from home_energy_twin.hpxml_calibration_adapter import HPXMLCalibrationAdapter

    calibrator = HPXMLCalibrationAdapter()
    try:
        model = calibrator.calibrate(house, utility_series, weather_series)
    except EngineUnavailable:
        ...  # degrade honestly; the core keeps working without the engine
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from .errors import EnergyTwinError, EngineUnavailable, UnidentifiableConfiguration
from .types import (
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
    "pin_satisfied_by",
    "require_engine",
]


ENGINE_BIN_ENV = "HET_HPXML_CALIBRATION_BIN"
"""Environment variable holding the path to the engine executable."""

PINNED_ENGINE_NAME = "OpenStudio-HPXML-Calibration"

PINNED_ENGINE_VERSION = "0.1"
"""The pinned engine version (docs/PLAN.md:114 — "v0.1"; Spike A §4).

The pin denotes the **0.1.x line**: ``0.1`` and ``0.1.3`` satisfy it, ``0.2``
and ``1.0`` do not.
"""

DEFAULT_TIMEOUT_S = 1800.0
"""Default wall-clock budget for one engine invocation (30 minutes)."""

_REQUEST_KEYS = (
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
    honest outcome is the shared typed error, not a guess and not a bespoke
    error class.
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
        """True when ``actual`` lies on the pinned version line.

        A pin of ``0.1`` is satisfied by ``0.1`` and by ``0.1.3``; it is *not*
        satisfied by ``0.2`` or by ``1.0``.
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
            index = line.lower().find(PINNED_ENGINE_NAME.lower())
            match = _VERSION_TOKEN.search(line[index:])
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
    """Production ``Calibrator`` facade over the HPXML-Calibration engine.

    The class satisfies the ``home_energy_twin.protocols.Calibrator``
    Protocol: ``id``, ``version``, ``supported_granularities`` and
    ``calibrate(house, utility, weather)``.  Core code never imports this
    module — the calibrator is selected by configuration (AGENTS.md).

    The engine is invoked strictly out of process, through the executable
    named by ``HET_HPXML_CALIBRATION_BIN`` (which may be a thin wrapper that
    fronts a container).  When the engine is absent the adapter raises
    :class:`home_energy_twin.errors.EngineUnavailable`; when the engine
    reports a version outside the pinned ``0.1.x`` line it raises the same
    typed error with a drift reason.
    """

    id = "openstudio-hpxml-calibration"
    version = PINNED_ENGINE_VERSION
    supported_granularities: set[Granularity] = {"billing_period"}

    def __init__(
        self,
        *,
        env: Optional[Mapping[str, str]] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._env = env
        self._timeout_s = timeout_s

    # -- engine plumbing -------------------------------------------------

    def engine_path(self) -> Path:
        """The engine executable; raises ``EngineUnavailable`` when absent."""
        return require_engine(self._env)

    def _child_env(self) -> Optional[dict[str, str]]:
        """The environment handed to the engine subprocess.

        The adapter's own configuration mapping (when given) is layered over
        the ambient environment, so a caller can point the engine at a
        container runtime or a scratch directory without mutating the parent
        process environment.
        """
        if self._env is None:
            return None
        merged = dict(os.environ)
        merged.update(self._env)
        return merged

    def engine_version(self) -> VersionPin:
        """Run the engine's ``--version`` banner and parse the version pin."""
        path = self.engine_path()
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=self._timeout_s,
            check=False,
            env=self._child_env(),
        )
        banner = f"{completed.stdout}\n{completed.stderr}"
        raw = parse_engine_version_output(banner)
        if raw is None:
            raise EngineUnavailable(
                f"{PINNED_ENGINE_NAME} did not report a version in its --version banner",
                reason="no version token in --version output",
            )
        return parse_version_pin(raw)

    def check_version_pin(self) -> VersionPin:
        """Verify the engine version against the Spike A pin.

        Raises ``EngineUnavailable`` when the engine is absent, when it does
        not report a version, or when the reported version has drifted off the
        pinned ``0.1.x`` line.
        """
        actual = self.engine_version()
        pin = parse_version_pin(PINNED_ENGINE_VERSION)
        if not pin.is_satisfied_by(actual):
            raise EngineUnavailable(
                f"{PINNED_ENGINE_NAME} version {actual.raw} does not satisfy the "
                f"pinned version line {PINNED_ENGINE_VERSION}",
                reason=(
                    f"version drift: engine reports {actual.raw}, "
                    f"pinned line is {PINNED_ENGINE_VERSION}.x"
                ),
            )
        return actual

    # -- calibration -----------------------------------------------------

    def calibrate(
        self,
        house: House,
        utility: list[UtilitySeries],
        weather: WeatherSeries,
    ) -> CalibratedModel:
        """Run one calibration out of process and map the result to SPEC §2.4.

        Raises ``EngineUnavailable`` when the engine is absent or has drifted
        off the pinned version line; raises ``UnidentifiableConfiguration``
        when the requested data granularity is not one this calibrator
        supports; raises ``EnergyTwinError`` when the engine's result cannot be
        mapped onto the SPEC §2.4 field list.
        """
        self._check_granularity(utility)
        path = self.engine_path()
        self.check_version_pin()
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
                env=self._child_env(),
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

    def _check_granularity(self, utility: list[UtilitySeries]) -> None:
        """Refuse data this calibrator cannot honestly consume (LAW-3)."""
        unsupported = sorted(
            {series.granularity for series in utility} - self.supported_granularities
        )
        if unsupported:
            raise UnidentifiableConfiguration(
                f"{self.id} calibrates {sorted(self.supported_granularities)} data only; "
                f"got {unsupported}",
                reason=f"unsupported data granularity: {unsupported}",
            )


def parse_calibration_payload(payload: Mapping[str, Any]) -> CalibratedModel:
    """Map an engine result payload onto the SPEC §2.4 ``CalibratedModel``.

    The key set below is the *documented* output contract of the engine and
    the checklist used in ``SPIKE_REPORT.md`` §5.  Every §2.4 field must be
    present; a missing field is an error rather than a silent default.
    """
    missing = [key for key in _REQUEST_KEYS if key not in payload]
    if missing:
        raise EnergyTwinError(
            f"engine result is missing SPEC 2.4 fields: {sorted(missing)}",
            reason=f"missing fields: {sorted(missing)}",
        )

    try:
        return _build_calibrated_model(payload)
    except ValidationError as exc:
        raise EnergyTwinError(
            f"engine result does not map onto the SPEC 2.4 model: {exc}",
            reason=f"invalid engine result payload: {exc}",
        ) from exc


def _build_calibrated_model(payload: Mapping[str, Any]) -> CalibratedModel:
    """Build the SPEC §2.4 model from a complete engine payload."""
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
        calibrator_version=str(
            payload.get("calibrator_version", PINNED_ENGINE_VERSION)
        ),
        status=payload["status"],
        rc_parameters=payload.get("rc_parameters"),
        bpi2400_metrics=(
            {str(k): float(v) for k, v in dict(bpi2400).items()}
            if bpi2400 is not None
            else None
        ),
    )
