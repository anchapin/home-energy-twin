"""Tests for the golden-house forward model and its synthetic weather.

The forward model is a small in-repo degree-day model (not an engine
integration), so these tests run with no engine binaries installed (RD-7).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from home_energy_twin.golden import (
    ForwardConfig,
    archetype_by_name,
    generate_house,
    monthly_degree_days,
    monthly_energy,
    simulate_monthly,
    synthetic_weather,
)

UTC = timezone.utc


def _house_and_weather(seed: int = 4, index: int = 0):
    house = generate_house(seed, index=index)
    weather = synthetic_weather(archetype_by_name(house.archetype), seed=seed + index)
    return house, weather


# --------------------------------------------------------------------------
# weather
# --------------------------------------------------------------------------


def test_synthetic_weather_is_deterministic_and_daily() -> None:
    archetype = archetype_by_name("cold_climate_heat_pump")
    first = synthetic_weather(archetype, seed=3)
    second = synthetic_weather(archetype, seed=3)
    assert first.model_dump_json() == second.model_dump_json()
    assert len(first.points) == 366  # 2024 is a leap year
    for previous, current in zip(first.points, first.points[1:]):
        assert (
            current.timestamp_utc - previous.timestamp_utc
        ).total_seconds() == pytest.approx(24 * 3600, abs=1)
        assert current.timestamp_utc.tzinfo is not None
        assert current.timestamp_utc.utcoffset() == timezone.utc.utcoffset(None)


def test_synthetic_weather_changes_with_the_seed() -> None:
    archetype = archetype_by_name("hot_humid_central_ac")
    first = synthetic_weather(archetype, seed=3)
    second = synthetic_weather(archetype, seed=4)
    assert [p.dry_bulb_c for p in first.points] != [p.dry_bulb_c for p in second.points]


def test_monthly_degree_days_cover_twelve_months() -> None:
    house, weather = _house_and_weather()
    months = monthly_degree_days(weather)
    assert len(months) == 12
    assert [row.month for row in months] == list(range(1, 13))
    assert all(row.days in (28, 29, 30, 31) for row in months)
    assert all(row.hdd_kk >= 0.0 and row.cdd_kk >= 0.0 for row in months)


# --------------------------------------------------------------------------
# forward model
# --------------------------------------------------------------------------


def test_forward_model_produces_a_year_of_monthly_billing_periods() -> None:
    house, weather = _house_and_weather()
    series = simulate_monthly(house, weather)
    assert series
    for one in series:
        assert one.granularity == "billing_period"
        assert one.stamp_semantics == "interval_start"
        assert one.kind == "import"
        assert len(one.points) == 12
        for point in one.points:
            assert point.end_utc > point.start_utc
            assert point.start_utc.tzinfo is not None
            assert point.start_utc.utcoffset() == timezone.utc.utcoffset(None)
            assert point.value >= 0.0
            assert point.unit == "kWh"


def test_monthly_points_are_calendar_months() -> None:
    house, weather = _house_and_weather()
    series = simulate_monthly(house, weather)
    points = series[0].points
    assert points[0].start_utc == datetime(2024, 1, 1, tzinfo=UTC)
    assert points[0].end_utc == datetime(2024, 2, 1, tzinfo=UTC)
    assert points[-1].end_utc == datetime(2025, 1, 1, tzinfo=UTC)
    assert points[0].start_utc < points[1].start_utc


def test_energy_is_positive_in_both_seasons() -> None:
    house, weather = _house_and_weather(seed=8, index=1)  # temperate gas furnace
    rows = monthly_energy(house, weather)
    assert all(row.kwh > 0.0 for row in rows)
    winter = [row for row in rows if row.month in (1, 2)]
    summer = [row for row in rows if row.month in (7, 8)]
    assert sum(row.kwh for row in winter) > sum(row.kwh for row in summer)


def test_gaps_are_injected_on_request() -> None:
    house, weather = _house_and_weather()
    clean = simulate_monthly(house, weather)
    gapped = simulate_monthly(house, weather, ForwardConfig(gap_pct=100.0))
    assert all(len(one.points) == 0 for one in gapped)


def test_estimated_reads_are_flagged_on_request() -> None:
    house, weather = _house_and_weather()
    series = simulate_monthly(house, weather, ForwardConfig(estimated_read_pct=100.0))
    points = [point for one in series for point in one.points]
    assert points
    assert all(point.estimated for point in points)
    clean = simulate_monthly(house, weather)
    assert all(
        not point.estimated for one in clean for point in one.points
    )


def test_noise_changes_the_values_but_not_the_mean_much() -> None:
    house, weather = _house_and_weather()
    clean = simulate_monthly(house, weather)
    noisy = simulate_monthly(house, weather, ForwardConfig(noise_pct=8.0, seed=1))
    clean_total = sum(point.value for one in clean for point in one.points)
    noisy_total = sum(point.value for one in noisy for point in one.points)
    assert clean_total != noisy_total
    assert noisy_total == pytest.approx(clean_total, rel=0.05)


def test_forward_model_is_deterministic_for_a_seed() -> None:
    house, weather = _house_and_weather()
    config = ForwardConfig(noise_pct=4.0, estimated_read_pct=20.0, gap_pct=10.0, seed=17)
    first = [one.model_dump_json() for one in simulate_monthly(house, weather, config)]
    second = [one.model_dump_json() for one in simulate_monthly(house, weather, config)]
    assert first == second
