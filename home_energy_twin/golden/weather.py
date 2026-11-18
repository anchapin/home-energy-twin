"""Synthetic weather for the golden-house harness (SPEC §6).

The weather is fabricated from the archetype's climate parameters plus a
seeded random component, so a ``(seed, archetype)`` pair always yields the
same weather.  It is *synthetic* (LAW-11) and is emitted as a
:class:`~home_energy_twin.types.WeatherSeries` with ``source_kind="observed"``
because the harness only ever uses it for calibration-style recovery
(LAW-10 — never TMY).

Only the daily dry-bulb mean is modelled: at monthly granularity the
degree-day integrals are what the recovery uses.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ..types import Provenance, WeatherPoint, WeatherSeries
from .types import Archetype, MonthlyDegreeDays

__all__ = [
    "HDD_BASE_C",
    "CDD_BASE_C",
    "monthly_degree_days",
    "synthetic_weather",
]

UTC = timezone.utc

HDD_BASE_C = 18.0
CDD_BASE_C = 18.0

#: Peak of the northern-hemisphere seasonal cycle (late July).
_PEAK_DAY_OF_YEAR = 206


def _provenance(year: int) -> Provenance:
    return Provenance(
        source="synthetic-golden-house-weather",
        retrieved_at_utc=datetime(year, 1, 1, tzinfo=UTC),
        terms_tag="CC0-1.0 (synthetic)",
    )


def synthetic_weather(
    archetype: Archetype,
    seed: int,
    year: int = 2024,
) -> WeatherSeries:
    """Build a deterministic daily synthetic weather series for an archetype.

    The daily mean temperature is a sinusoid around the archetype's annual
    mean, plus seeded day-to-day noise and one seeded cold-snap excursion.
    """
    rng = random.Random(f"golden-weather:{seed}:{archetype.name}:{year}")
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)
    total_days = (end - start).days

    snap_start = rng.randrange(0, max(total_days - 6, 1))
    snap_depth = rng.uniform(4.0, 9.0)
    snap_length = rng.randrange(3, 8)

    points: list[WeatherPoint] = []
    for index in range(total_days):
        day = start + timedelta(days=index)
        doy = day.timetuple().tm_yday
        seasonal = archetype.seasonal_amplitude_c * math.cos(
            2.0 * math.pi * (doy - _PEAK_DAY_OF_YEAR) / 365.25
        )
        temperature = archetype.annual_mean_c + seasonal + rng.gauss(0.0, 2.2)
        if snap_start <= index < snap_start + snap_length:
            temperature -= snap_depth * (1.0 - abs(index - snap_start - snap_length / 2) / (snap_length / 2))
        rh = min(95.0, max(25.0, 70.0 - 1.6 * (temperature - archetype.annual_mean_c) + rng.gauss(0.0, 4.0)))
        points.append(
            WeatherPoint(
                timestamp_utc=datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC),
                dry_bulb_c=round(temperature, 4),
                rh=round(rh, 2),
                wind_mps=round(abs(rng.gauss(2.0, 1.0)), 3),
                ghi_wm2=0.0,
            )
        )

    return WeatherSeries(
        points=points,
        source_kind="observed",
        station_or_cell=f"synthetic-{archetype.climate_zone}-{archetype.name}",
        distance_km=0.0,
        provenance=_provenance(year),
    )


def monthly_degree_days(
    weather: WeatherSeries,
    hdd_base_c: float = HDD_BASE_C,
    cdd_base_c: float = CDD_BASE_C,
) -> list[MonthlyDegreeDays]:
    """Integrate the daily temperatures of ``weather`` into monthly degree days."""
    buckets: dict[tuple[int, int], list[float]] = {}
    for point in weather.points:
        key = (point.timestamp_utc.year, point.timestamp_utc.month)
        buckets.setdefault(key, []).append(point.dry_bulb_c)

    months: list[MonthlyDegreeDays] = []
    for (year, month) in sorted(buckets):
        temps = buckets[(year, month)]
        hdd = sum(max(0.0, hdd_base_c - t) for t in temps)
        cdd = sum(max(0.0, t - cdd_base_c) for t in temps)
        months.append(
            MonthlyDegreeDays(
                year=year,
                month=month,
                hdd_kk=round(hdd, 6),
                cdd_kk=round(cdd, 6),
                days=len(temps),
            )
        )
    return months


def weather_year(weather: WeatherSeries) -> Optional[int]:
    """The calendar year covered by the weather series, if it is unambiguous."""
    years = {point.timestamp_utc.year for point in weather.points}
    if len(years) == 1:
        return years.pop()
    return None
