"""Tests for the Spike A adapter skeleton (no engine binary required).

The skeleton is pure Python and synthetic (LAW-11), so these tests run in
the core suite with no engine installed (RD-7).  They prove two things:

* the *absent engine* path raises the shared typed error
  ``home_energy_twin.errors.EngineUnavailable`` (not a bespoke error class);
* the *version pin* parsing behaves as the pinned candidate version
  (OpenStudio-HPXML-Calibration v0.1, docs/PLAN.md line 114) requires.

Selected by ``pytest -k hpxml``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from home_energy_twin.errors import EngineUnavailable
from home_energy_twin.types import HPXMLRef, House, LatLon
from spikes.hpxml_calibration.adapter import (
    ENGINE_BIN_ENV,
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

SHA = "0" * 64
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def _house() -> House:
    return House(
        house_id=UUID("11111111-2222-3333-4444-555555555555"),
        hpxml_ref=HPXMLRef(uri="file:///synthetic/home.xml", sha256=SHA),
        timezone="America/Juneau",
        location=LatLon(latitude=58.3, longitude=-134.4),
        state_hash="synthetic-state-hash",
    )


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
    env: dict[str, str] = {}
    with pytest.raises(EngineUnavailable):
        require_engine(env)


def test_engine_is_found_when_env_var_points_at_a_real_file(tmp_path: Path) -> None:
    binary = tmp_path / "hpxml_calibration"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    env = {ENGINE_BIN_ENV: str(binary)}
    assert engine_bin_path(env) == binary
    assert engine_available(env) is True
    assert require_engine(env) == binary


def test_adapter_calibrate_fails_with_engine_unavailable_when_engine_absent() -> None:
    adapter = HPXMLCalibrationAdapter(env={ENGINE_BIN_ENV: "/nonexistent/engine"})
    with pytest.raises(EngineUnavailable):
        adapter.calibrate(_house(), [], _weather())  # type: ignore[arg-type]


def _weather() -> object:
    from home_energy_twin.types import WeatherSeries

    return WeatherSeries(
        points=[],
        source_kind="observed",
        station_or_cell="synthetic",
        distance_km=0.0,
        provenance={
            "source": "synthetic-spike-a",
            "retrieved_at_utc": START,
            "terms_tag": "synthetic",
        },
    )


# --------------------------------------------------------------------------
# version pin
# --------------------------------------------------------------------------


def test_pinned_version_is_the_documented_candidate() -> None:
    # docs/PLAN.md line 114: "OpenStudio-HPXML-Calibration (BPI-2400, v0.1, ...)"
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


def test_parse_engine_version_output_rejects_an_unpinned_major_line() -> None:
    banner = "OpenStudio-HPXML-Calibration 0.2.0\n"
    assert parse_engine_version_output(banner) == "0.2.0"
    assert pin_satisfied_by(banner) is False


# --------------------------------------------------------------------------
# out-of-process runner (fake engine binary)
# --------------------------------------------------------------------------

FAKE_ENGINE = """#!/usr/bin/env python3
import json
import sys

args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
with open(args["--request"], encoding="utf-8") as handle:
    request = json.load(handle)

house = request["house"]
payload = {
    "house_id": house["house_id"],
    "state_hash": house["state_hash"],
    "engine_ref": "openstudio-hpxml-calibration-0.1",
    "hpxml_ref": house["hpxml_ref"],
    "parameters": {
        "ua": {
            "value": 0.5,
            "low": 0.4,
            "high": 0.6,
            "unit": "W/K",
            "method": "fake-engine",
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
    "bpi2400_metrics": {"cvrmse": 0.18},
}
with open(args["--output"], "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
"""


def test_fake_engine_is_driven_out_of_process(tmp_path: Path) -> None:
    engine = tmp_path / "fake_engine.py"
    engine.write_text(FAKE_ENGINE, encoding="utf-8")
    engine.chmod(0o755)
    adapter = HPXMLCalibrationAdapter(env={ENGINE_BIN_ENV: str(engine)})
    model = adapter.calibrate(_house(), [], _weather())  # type: ignore[arg-type]
    assert model.metrics.nmbe_pct == pytest.approx(2.1)
    assert model.metrics.guideline14_pass is True
    assert model.bpi2400_metrics == {"cvrmse": 0.18}
    assert model.parameters["ua"].low == 0.4


def test_cli_reports_engine_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENGINE_BIN_ENV, raising=False)
    hpxml = tmp_path / "home.xml"
    hpxml.write_text("<HPXML/>", encoding="utf-8")
    bills = tmp_path / "bills.json"
    bills.write_text('{"bills": []}', encoding="utf-8")
    from spikes.hpxml_calibration.run_calibration import main

    code = main(
        [
            "--hpxml",
            str(hpxml),
            "--bills",
            str(bills),
            "--output",
            str(tmp_path / "out.json"),
        ]
    )
    assert code == 2


# --------------------------------------------------------------------------
# SPEC §2.4 payload mapping
# --------------------------------------------------------------------------


def _payload() -> dict[str, object]:
    return {
        "house_id": "11111111-2222-3333-4444-555555555555",
        "state_hash": "synthetic-state-hash",
        "engine_ref": "openstudio-hpxml-calibration-0.1",
        "hpxml_ref": {"uri": "file:///synthetic/home.xml", "sha256": SHA},
        "parameters": {
            "ua": {
                "value": 0.5,
                "low": 0.4,
                "high": 0.6,
                "unit": "W/K",
                "method": "hpxml-calibration",
            }
        },
        "metrics": {
            "nmbe_pct": 2.1,
            "cvrmse_pct": 11.0,
            "granularity": "billing_period",
            "guideline14_pass": True,
        },
        "calibration_window": {
            "start_utc": START.isoformat(),
            "end_utc": datetime(2026, 12, 31, 0, 0, tzinfo=UTC).isoformat(),
        },
        "data_granularity": "billing_period",
        "status": "valid",
        "bpi2400_metrics": {"cvrmse": 0.18},
    }


def test_payload_maps_onto_the_spec_24_model() -> None:
    model = parse_calibration_payload(_payload())
    assert model.metrics.guideline14_pass is True
    assert model.parameters["ua"].low == 0.4
    assert model.bpi2400_metrics == {"cvrmse": 0.18}


def test_payload_missing_a_spec_24_field_is_an_error() -> None:
    payload = _payload()
    del payload["metrics"]
    with pytest.raises(ValueError):
        parse_calibration_payload(payload)


# --------------------------------------------------------------------------
# the spike stays out of the core
# --------------------------------------------------------------------------


def test_core_package_does_not_import_the_spike() -> None:
    core = Path(__file__).resolve().parent.parent / "home_energy_twin"
    offenders = [
        str(path.relative_to(core.parent))
        for path in core.rglob("*.py")
        if "spikes" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
