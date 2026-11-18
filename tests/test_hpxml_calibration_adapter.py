"""Tests for the production HPXML-Calibration adapter (issue #8).

The adapter is out-of-process by construction: no OpenStudio / EnergyPlus /
HPXML-Calibration runtime is imported, downloaded or needed here.  These tests
therefore run in the core suite with **no engine installed** (RD-7).

They cover the three acceptance shapes:

* the *absent engine* path refuses with the shared typed error
  ``home_energy_twin.errors.EngineUnavailable`` (never a crash, never a
  fabricated result);
* the *version pin* (``0.1``, the 0.1.x line, per Spike A / docs/PLAN.md:114)
  is accepted on the line and refused when the engine drifts;
* the *subprocess invocation shape* — request file in, result file out — is
  exercised with a synthetic stub executable committed under ``tests/fixtures``.

All fixtures are synthetic (LAW-11).
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from home_energy_twin.errors import EnergyTwinError, EngineUnavailable
from home_energy_twin.hpxml_calibration_adapter import (
    ENGINE_BIN_ENV,
    PINNED_ENGINE_NAME,
    PINNED_ENGINE_VERSION,
    HPXMLCalibrationAdapter,
    engine_available,
    engine_bin_path,
    parse_calibration_payload,
    parse_engine_version_output,
    parse_version_pin,
    pin_satisfied_by,
    require_engine,
)
from home_energy_twin.protocols import Calibrator
from home_energy_twin.types import (
    EnergyPoint,
    HPXMLRef,
    House,
    LatLon,
    Provenance,
    UtilitySeries,
    WeatherSeries,
)

SHA = "0" * 64
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"
STUB_ENGINE = FIXTURES / "stub_hpxml_calibration.py"


def _house() -> House:
    return House(
        house_id=UUID("11111111-2222-3333-4444-555555555555"),
        hpxml_ref=HPXMLRef(uri="file:///synthetic/home.xml", sha256=SHA),
        timezone="America/Juneau",
        location=LatLon(latitude=58.3, longitude=-134.4),
        state_hash="synthetic-state-hash",
    )


def _provenance() -> Provenance:
    return Provenance(
        source="synthetic-fixture",
        retrieved_at_utc=START,
        terms_tag="synthetic",
    )


def _utility() -> list[UtilitySeries]:
    return [
        UtilitySeries(
            fuel="electricity",
            kind="import",
            granularity="billing_period",
            points=[
                EnergyPoint(
                    start_utc=START,
                    end_utc=datetime(2026, 2, 1, tzinfo=UTC),
                    value=1420.0,
                    unit="kWh",
                )
            ],
            stamp_semantics="interval_start",
            provenance=_provenance(),
            meter_id="synthetic-electricity",
        )
    ]


def _weather() -> WeatherSeries:
    return WeatherSeries(
        points=[],
        source_kind="observed",
        station_or_cell="synthetic",
        distance_km=0.0,
        provenance=_provenance(),
    )


def _stub_env(version: str = "0.1.0") -> dict[str, str]:
    """An environment pointing at the committed stub engine."""
    return {ENGINE_BIN_ENV: str(STUB_ENGINE), "HET_STUB_ENGINE_VERSION": version}


# --------------------------------------------------------------------------
# Protocol shape
# --------------------------------------------------------------------------


def test_adapter_satisfies_the_calibrator_protocol() -> None:
    adapter = HPXMLCalibrationAdapter()
    assert isinstance(adapter, Calibrator)
    assert adapter.id == "openstudio-hpxml-calibration"
    assert adapter.version == PINNED_ENGINE_VERSION
    assert adapter.supported_granularities == {"billing_period"}
    assert callable(adapter.calibrate)


def test_adapter_is_not_imported_by_core_code() -> None:
    """Core modules must not import adapters; selection is by configuration."""
    package = Path(__file__).resolve().parents[1] / "home_energy_twin"
    offenders = [
        path.name
        for path in sorted(package.glob("*.py"))
        if path.name != "hpxml_calibration_adapter.py"
        and "hpxml_calibration_adapter" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# --------------------------------------------------------------------------
# absent engine
# --------------------------------------------------------------------------


def test_engine_is_absent_when_env_var_is_unset() -> None:
    env = {k: v for k, v in os.environ.items() if k != ENGINE_BIN_ENV}
    assert engine_bin_path(env) is None
    assert engine_available(env) is False


def test_engine_is_absent_when_env_var_is_blank() -> None:
    env = {ENGINE_BIN_ENV: "   "}
    assert engine_bin_path(env) is None
    assert engine_available(env) is False


def test_engine_is_absent_when_env_var_points_nowhere() -> None:
    env = {ENGINE_BIN_ENV: "/nonexistent/openstudio-hpxml-calibration"}
    assert engine_bin_path(env) is None
    assert engine_available(env) is False


def test_require_engine_raises_the_shared_typed_error() -> None:
    env = {ENGINE_BIN_ENV: "/nonexistent/openstudio-hpxml-calibration"}
    with pytest.raises(EngineUnavailable) as excinfo:
        require_engine(env)
    assert excinfo.value.reason is not None
    assert ENGINE_BIN_ENV in excinfo.value.reason


def test_require_engine_raises_the_shared_typed_error_when_unset() -> None:
    with pytest.raises(EngineUnavailable):
        require_engine({})


def test_calibrate_refuses_with_typed_error_when_engine_absent() -> None:
    adapter = HPXMLCalibrationAdapter(env={ENGINE_BIN_ENV: "/nonexistent/engine"})
    with pytest.raises(EngineUnavailable) as excinfo:
        adapter.calibrate(_house(), _utility(), _weather())
    assert isinstance(excinfo.value, EnergyTwinError)
    assert excinfo.value.reason is not None


def test_calibrate_refuses_with_typed_error_when_env_var_unset() -> None:
    adapter = HPXMLCalibrationAdapter(env={})
    with pytest.raises(EngineUnavailable):
        adapter.calibrate(_house(), _utility(), _weather())


def test_engine_is_found_when_env_var_points_at_a_real_file(tmp_path: Path) -> None:
    binary = tmp_path / "hpxml_calibration"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    env = {ENGINE_BIN_ENV: str(binary)}
    assert engine_bin_path(env) == binary
    assert engine_available(env) is True
    assert require_engine(env) == binary


# --------------------------------------------------------------------------
# version pin
# --------------------------------------------------------------------------


def test_pinned_version_is_the_documented_candidate() -> None:
    # docs/PLAN.md:114 — "OpenStudio-HPXML-Calibration (BPI-2400, v0.1, ...)".
    assert PINNED_ENGINE_VERSION == "0.1"


def test_parse_version_pin_accepts_the_documented_forms() -> None:
    assert parse_version_pin("0.1") == parse_version_pin("v0.1")
    pin = parse_version_pin("0.1")
    assert (pin.major, pin.minor, pin.patch) == (0, 1, None)
    full = parse_version_pin("0.1.3")
    assert (full.major, full.minor, full.patch) == (0, 1, 3)


def test_parse_version_pin_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_version_pin("not-a-version")
    with pytest.raises(ValueError):
        parse_version_pin("")


def test_version_pin_line_is_satisfied_only_by_that_line() -> None:
    pin = parse_version_pin(PINNED_ENGINE_VERSION)
    assert pin.is_satisfied_by(parse_version_pin("0.1"))
    assert pin.is_satisfied_by(parse_version_pin("0.1.0"))
    assert pin.is_satisfied_by(parse_version_pin("0.1.9"))
    assert not pin.is_satisfied_by(parse_version_pin("0.2"))
    assert not pin.is_satisfied_by(parse_version_pin("1.0"))
    assert not pin.is_satisfied_by(parse_version_pin("0.0.9"))


def test_parse_engine_version_output_reads_the_banner() -> None:
    banner = "OpenStudio-HPXML-Calibration 0.1.0\nOpenStudio 3.6.0\n"
    assert parse_engine_version_output(banner) == "0.1.0"
    assert pin_satisfied_by(banner) is True


def test_parse_engine_version_output_returns_none_without_a_version() -> None:
    assert parse_engine_version_output("no version here") is None
    assert pin_satisfied_by("no version here") is False


def test_engine_version_is_read_out_of_process() -> None:
    adapter = HPXMLCalibrationAdapter(env=_stub_env("0.1.4"))
    assert adapter.engine_version().raw == "0.1.4"


def test_version_pin_satisfied_by_the_stub_engine() -> None:
    adapter = HPXMLCalibrationAdapter(env=_stub_env("0.1.0"))
    assert adapter.check_version_pin().raw == "0.1.0"


def test_drifted_engine_version_is_refused_with_typed_error() -> None:
    adapter = HPXMLCalibrationAdapter(env=_stub_env("0.2.0"))
    with pytest.raises(EngineUnavailable) as excinfo:
        adapter.check_version_pin()
    assert excinfo.value.reason is not None
    assert "drift" in excinfo.value.reason


def test_drifted_engine_version_blocks_calibration() -> None:
    adapter = HPXMLCalibrationAdapter(env=_stub_env("0.2.0"))
    with pytest.raises(EngineUnavailable):
        adapter.calibrate(_house(), _utility(), _weather())


def test_engine_without_a_version_banner_is_refused() -> None:
    adapter = HPXMLCalibrationAdapter(
        env={ENGINE_BIN_ENV: str(FIXTURES / "stub_no_version.py")}
    )
    with pytest.raises(EngineUnavailable):
        adapter.engine_version()


# --------------------------------------------------------------------------
# out-of-process invocation shape (synthetic stub engine)
# --------------------------------------------------------------------------


def test_calibrate_invokes_the_engine_out_of_process() -> None:
    adapter = HPXMLCalibrationAdapter(env=_stub_env("0.1.0"))
    model = adapter.calibrate(_house(), _utility(), _weather())
    assert model.metrics.nmbe_pct == pytest.approx(2.1)
    assert model.metrics.cvrmse_pct == pytest.approx(11.0)
    assert model.metrics.guideline14_pass is True
    assert model.data_granularity == "billing_period"
    assert model.calibrator_id == "openstudio-hpxml-calibration"
    assert model.bpi2400_metrics == {"cvrmse": 0.18}
    assert model.parameters["ua"].low == pytest.approx(0.4)
    assert model.parameters["ua"].high == pytest.approx(0.6)


def test_request_file_carries_house_utility_and_weather(tmp_path: Path) -> None:
    """The stub echoes the request it was given, proving the hand-off shape."""
    adapter = HPXMLCalibrationAdapter(env=_stub_env("0.1.0"))
    model = adapter.calibrate(_house(), _utility(), _weather())
    assert model.house_id == _house().house_id
    assert model.state_hash == "synthetic-state-hash"
    assert model.engine_ref == f"openstudio-hpxml-calibration-{PINNED_ENGINE_VERSION}"
    assert model.calibration_window.start_utc == START


def test_engine_failure_is_reported_as_a_typed_error() -> None:
    adapter = HPXMLCalibrationAdapter(
        env={ENGINE_BIN_ENV: str(FIXTURES / "stub_failing_engine.py")}
    )
    with pytest.raises(EngineUnavailable) as excinfo:
        adapter.calibrate(_house(), _utility(), _weather())
    assert excinfo.value.reason is not None


def test_unsupported_granularity_is_refused() -> None:
    series = _utility()
    hourly = UtilitySeries(
        fuel="electricity",
        kind="import",
        granularity="hourly",
        points=[],
        stamp_semantics="interval_start",
        provenance=_provenance(),
    )
    with pytest.raises(EnergyTwinError):
        HPXMLCalibrationAdapter(env=_stub_env("0.1.0")).calibrate(
            _house(), [*series, hourly], _weather()
        )


# --------------------------------------------------------------------------
# result mapping
# --------------------------------------------------------------------------


def _valid_payload() -> dict[str, object]:
    return {
        "house_id": str(UUID("11111111-2222-3333-4444-555555555555")),
        "state_hash": "synthetic-state-hash",
        "engine_ref": "openstudio-hpxml-calibration-0.1",
        "hpxml_ref": {"uri": "file:///synthetic/home.xml", "sha256": SHA},
        "parameters": {
            "ua": {
                "value": 0.5,
                "low": 0.4,
                "high": 0.6,
                "unit": "W/K",
                "method": "stub-engine",
            }
        },
        "metrics": {
            "nmbe_pct": 2.1,
            "cvrmse_pct": 11.0,
            "granularity": "billing_period",
            "guideline14_pass": True,
        },
        "calibration_window": {
            "start_utc": "2026-01-01T00:00:00Z",
            "end_utc": "2026-12-31T00:00:00Z",
        },
        "data_granularity": "billing_period",
        "status": "valid",
    }


def test_payload_maps_onto_the_spec_2_4_model() -> None:
    model = parse_calibration_payload(_valid_payload())
    assert model.metrics.granularity == "billing_period"
    assert model.parameters["ua"].value == pytest.approx(0.5)


def test_payload_missing_a_spec_field_is_an_error_not_a_default() -> None:
    payload = _valid_payload()
    del payload["state_hash"]
    with pytest.raises(EnergyTwinError):
        parse_calibration_payload(payload)


def test_payload_point_only_parameter_is_rejected() -> None:
    """LAW-1: a calibrated parameter must arrive as a range, not a point."""
    payload = _valid_payload()
    payload["parameters"] = {"ua": {"value": 0.5, "unit": "W/K", "method": "stub"}}
    with pytest.raises(EnergyTwinError):
        parse_calibration_payload(payload)


def test_stub_engine_is_executable() -> None:
    assert STUB_ENGINE.exists()
    mode = STUB_ENGINE.stat().st_mode
    assert mode & stat.S_IXUSR


def test_stub_engine_output_is_valid_json() -> None:
    """The committed stub stays in sync with the payload contract."""
    payload = json.loads(
        (FIXTURES / "stub_payload.json").read_text(encoding="utf-8")
    )
    model = parse_calibration_payload(payload)
    assert model.status == "valid"
    assert PINNED_ENGINE_NAME == "OpenStudio-HPXML-Calibration"
