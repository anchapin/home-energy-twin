"""Tests for the Spike B harness logic (no OCHRE binary required).

The harness itself is pure Python and synthetic (LAW-11), so these tests run
in the core suite with no engine installed (RD-7).  The one test that needs
an OCHRE distribution is marked and skips cleanly when OCHRE is absent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from home_energy_twin.protocols import EngineAdapter
from home_energy_twin.types import AdvisoryControls, EngineState, WeatherSeries
from spikes.ochre.harness import (
    RESTORE_TOLERANCE_C,
    LiveHarness,
    run_demo,
)
from spikes.ochre.ochre_adapter import (
    OchreUnavailableError,
    ochre_available,
    ochre_version,
    require_ochre,
)
from spikes.ochre.rc_engine import RCEngine, RCParams
from spikes.ochre.weather import synthetic_weather

START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def _harness(days: int = 2, step_minutes: int = 15) -> LiveHarness:
    weather = synthetic_weather(START, days=days, step_minutes=step_minutes, seed=7)
    return LiveHarness(engine=RCEngine(RCParams()), weather=weather)


# --------------------------------------------------------------------------
# synthetic weather
# --------------------------------------------------------------------------


def test_weather_is_deterministic_for_a_seed() -> None:
    a = synthetic_weather(START, days=2, seed=11)
    b = synthetic_weather(START, days=2, seed=11)
    assert [p.dry_bulb_c for p in a.points] == [p.dry_bulb_c for p in b.points]


def test_weather_changes_with_the_seed() -> None:
    a = synthetic_weather(START, days=2, seed=11)
    b = synthetic_weather(START, days=2, seed=12)
    assert [p.dry_bulb_c for p in a.points] != [p.dry_bulb_c for p in b.points]


def test_weather_cadence_and_length() -> None:
    series = synthetic_weather(START, days=7, step_minutes=15)
    assert len(series.points) == 7 * 96
    first, second = series.points[0], series.points[1]
    assert second.timestamp_utc - first.timestamp_utc == timedelta(minutes=15)
    assert series.provenance.source == "synthetic-spike-b"
    assert series.source_kind == "forecast"


def test_weather_rejects_coarse_steps() -> None:
    with pytest.raises(ValueError):
        synthetic_weather(START, days=1, step_minutes=60)


# --------------------------------------------------------------------------
# engine shape
# --------------------------------------------------------------------------


def test_engine_matches_the_engine_adapter_protocol() -> None:
    engine = RCEngine(RCParams())
    assert isinstance(engine, EngineAdapter)
    assert engine.name == "rc-1r1c"


def test_engine_step_advances_state() -> None:
    harness = _harness()
    engine = harness.engine
    state = engine.initial_state(START)
    first = engine.step(state, harness.weather.points[0], harness.controls)
    assert first.timestamp_utc == harness.weather.points[0].timestamp_utc
    assert first.zone_temperatures_c["main"] != state.zone_temperatures_c["main"]


# --------------------------------------------------------------------------
# state carry
# --------------------------------------------------------------------------


def test_state_at_step_n_plus_one_depends_on_step_n() -> None:
    harness = _harness(days=2)
    engine = harness.engine
    state = engine.initial_state(START)
    records, mid_state = harness.run(state, 20)
    assert len(records) == 20

    # replaying the prefix from the same cold start reproduces the trajectory
    replayed, _ = harness.run(state, 20)
    assert [r.indoor_c for r in replayed] == [r.indoor_c for r in records]

    # the next step differs depending on whether the state is carried or cold
    from_carried = engine.step(mid_state, harness.weather.points[20], harness.controls)
    from_cold = engine.step(state, harness.weather.points[20], harness.controls)
    assert from_carried.zone_temperatures_c["main"] != pytest.approx(
        from_cold.zone_temperatures_c["main"], abs=1e-6
    )

    # perturbing the carried state changes the next step
    perturbed = EngineState(
        timestamp_utc=mid_state.timestamp_utc,
        zone_temperatures_c={**mid_state.zone_temperatures_c, "main": 25.0},
        thermal_state_c=25.0,
        runtime_s=mid_state.runtime_s,
        extra=dict(mid_state.extra),
    )
    other = engine.step(perturbed, harness.weather.points[20], harness.controls)
    assert other.zone_temperatures_c["main"] != pytest.approx(
        from_carried.zone_temperatures_c["main"], abs=1e-6
    )


def test_two_runs_from_different_states_diverge() -> None:
    harness = _harness(days=2)
    engine = harness.engine
    warm = engine.initial_state(START)
    cold = EngineState(
        timestamp_utc=START,
        zone_temperatures_c={"main": 16.0},
        thermal_state_c=16.0,
        runtime_s=0.0,
        extra={"heating_wh": 0.0, "cooling_wh": 0.0, "outdoor_c": 0.0},
    )
    warm_records, _ = harness.run(warm, 30)
    cold_records, _ = harness.run(cold, 30)
    assert warm_records[-1].indoor_c != pytest.approx(
        cold_records[-1].indoor_c, abs=1e-6
    )


# --------------------------------------------------------------------------
# correction
# --------------------------------------------------------------------------


def test_correction_moves_state_and_is_bounded() -> None:
    engine = RCEngine(RCParams())
    harness = LiveHarness(
        engine=engine, weather=synthetic_weather(START, days=2, step_minutes=15, seed=7)
    )
    state = engine.initial_state(START)
    _, mid = harness.run(state, 20)
    before = mid.zone_temperatures_c["main"]
    observed = before + 1.2
    corrected = engine.assimilate(mid, observed, 0.5)
    after = corrected.zone_temperatures_c["main"]
    assert after == pytest.approx(before + 0.6, abs=1e-9)
    assert abs(after - before) <= 0.5 * abs(observed - before) + 1e-12
    assert corrected.extra["correction_offset_c"] == pytest.approx(0.6, abs=1e-9)


def test_correction_changes_the_downstream_trajectory() -> None:
    engine = RCEngine(RCParams())
    harness = LiveHarness(
        engine=engine, weather=synthetic_weather(START, days=2, step_minutes=15, seed=7)
    )
    state = engine.initial_state(START)
    _, mid = harness.run(state, 20)
    corrected = engine.assimilate(mid, mid.zone_temperatures_c["main"] + 1.2, 0.5)
    base, _ = harness.run(mid, 48, start_index=20)
    corr, _ = harness.run(corrected, 48, start_index=20)
    assert [r.indoor_c for r in base] != [r.indoor_c for r in corr]


# --------------------------------------------------------------------------
# checkpoint / restore
# --------------------------------------------------------------------------


def test_checkpoint_restore_continuation_matches_uninterrupted_run() -> None:
    harness = _harness(days=2)
    engine = harness.engine
    state = engine.initial_state(START)
    reference, _ = harness.run(state, 60)

    _, mid = harness.run(state, 30)
    checkpoint = harness.checkpoint(mid)
    assert json.loads(checkpoint)["zone_temperatures_c"]["main"] == pytest.approx(
        mid.zone_temperatures_c["main"]
    )

    restored = harness.restore(checkpoint)
    continuation, _ = harness.run(restored, 30, start_index=30)

    assert len(continuation) == len(reference[30:])
    for expected, actual in zip(reference[30:], continuation):
        assert actual.indoor_c == pytest.approx(expected.indoor_c, abs=RESTORE_TOLERANCE_C)


def test_checkpoint_is_json_serialisable_and_restores_extra_fields() -> None:
    harness = _harness(days=1)
    engine = harness.engine
    state = engine.initial_state(START)
    _, mid = harness.run(state, 5)
    restored = harness.restore(harness.checkpoint(mid))
    assert restored.extra["heating_wh"] == pytest.approx(mid.extra["heating_wh"])
    assert restored.timestamp_utc == mid.timestamp_utc


# --------------------------------------------------------------------------
# the demonstration itself
# --------------------------------------------------------------------------


def test_demo_runs_one_simulated_week_at_fifteen_minute_steps() -> None:
    report = run_demo(days=7, step_minutes=15)
    assert report.n_steps == 672
    assert report.simulated_days == pytest.approx(7.0)
    assert report.step_minutes <= 15
    assert report.state_carry["demonstrated"] is True
    assert report.correction["demonstrated"] is True
    assert report.checkpoint_restore["demonstrated"] is True
    assert report.checkpoint_restore["max_abs_delta_vs_uninterrupted_c"] <= (
        RESTORE_TOLERANCE_C
    )
    assert report.runtime_s >= 0.0


def test_demo_weather_series_is_synthetic_and_ordered() -> None:
    harness = _harness(days=1)
    series: WeatherSeries = harness.weather
    stamps = [p.timestamp_utc for p in series.points]
    assert stamps == sorted(stamps)
    assert all(s.tzinfo is not None for s in stamps)


def test_demo_is_reproducible() -> None:
    a = run_demo(days=2, step_minutes=15, seed=3)
    b = run_demo(days=2, step_minutes=15, seed=3)
    assert a.totals == b.totals


def test_advisory_controls_are_advisory_only() -> None:
    controls = AdvisoryControls(heating_setpoint_c=21.0, cooling_setpoint_c=24.0)
    assert "device" not in controls.model_dump_json()


# --------------------------------------------------------------------------
# OCHRE availability (skips cleanly when OCHRE is absent)
# --------------------------------------------------------------------------


def test_ochre_probe_reports_absence_without_raising() -> None:
    version = ochre_version()
    if version is None:
        assert ochre_available() is False
    else:
        assert ochre_available() is True


def test_require_ochre_raises_a_helpful_error_when_absent() -> None:
    if ochre_available():
        pytest.skip("OCHRE is installed in this environment")
    with pytest.raises(OchreUnavailableError) as excinfo:
        require_ochre()
    assert "not usable" in str(excinfo.value)


@pytest.mark.skipif(not ochre_available(), reason="OCHRE not installed")
def test_ochre_is_importable_when_installed() -> None:
    module = require_ochre()
    assert module is not None
    assert ochre_version() is not None
