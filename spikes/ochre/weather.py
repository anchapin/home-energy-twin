"""Deterministic **synthetic** weather for the Spike B harness.

LAW-11: committed fixtures are synthetic.  No real observation, no real
station, no network call — the series is generated from a seeded PRNG so any
run of the spike reproduces bit-for-bit.

The series is labelled ``source_kind="forecast"`` because that is the only
``WeatherSourceKind`` in the core type that honestly describes a series that
is *supplied to* a forward simulation rather than being an observation of a
real place (LAW-10 forbids feeding TMY/forecast data to calibration).
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from home_energy_twin.types import Provenance, WeatherPoint, WeatherSeries

DEFAULT_SEED: int = 20260218
DEFAULT_STEP_MINUTES: int = 15
DEFAULT_DAYS: int = 7

__all__ = [
    "DEFAULT_DAYS",
    "DEFAULT_SEED",
    "DEFAULT_STEP_MINUTES",
    "synthetic_weather",
]


def _daily_shape(hour: float) -> float:
    """Smooth diurnal shape in [-1, 1]; peak at 15:00 local, trough at 03:00."""
    return math.sin(2.0 * math.pi * (hour - 9.0) / 24.0)


def _solar_factor(hour: float) -> float:
    """Crude clear-sky weight in [0, 1]; zero at night, peak at solar noon."""
    return max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))


def synthetic_weather(
    start_utc: datetime,
    days: int = DEFAULT_DAYS,
    step_minutes: int = DEFAULT_STEP_MINUTES,
    seed: int = DEFAULT_SEED,
    mean_c: float = 6.0,
    amplitude_c: float = 6.0,
) -> WeatherSeries:
    """Build a synthetic weather series at ``step_minutes`` cadence.

    The generator is a sinusoid (diurnal + slow synoptic drift) plus a small
    AR(1) noise term.  It is *not* physically calibrated — it only has to be
    deterministic, smooth enough to drive a stepping engine, and visibly
    different from a flat line.
    """
    if days <= 0:
        raise ValueError("days must be positive")
    if step_minutes <= 0 or step_minutes > 15:
        raise ValueError("step_minutes must be in (0, 15] for this spike")
    if start_utc.tzinfo is None:
        raise ValueError("start_utc must be timezone-aware (UTC)")

    rng = random.Random(seed)
    n_steps = round(days * 24 * 60 / step_minutes)
    points: list[WeatherPoint] = []
    ar_state = 0.0
    ar_coef = 0.85
    ar_sigma = 0.35

    for i in range(n_steps):
        ts = start_utc + timedelta(minutes=step_minutes * i)
        hour = ts.hour + ts.minute / 60.0
        ar_state = ar_coef * ar_state + rng.gauss(0.0, ar_sigma)
        dry_bulb = mean_c + amplitude_c * _daily_shape(hour) + ar_state
        rh = min(100.0, max(5.0, 85.0 - 1.2 * (dry_bulb - mean_c) + rng.gauss(0.0, 1.5)))
        wind = max(0.0, 2.5 + rng.gauss(0.0, 0.8))
        ghi = max(0.0, 780.0 * _solar_factor(hour) * (0.75 + 0.25 * rng.random()))
        points.append(
            WeatherPoint(
                timestamp_utc=ts,
                dry_bulb_c=round(dry_bulb, 4),
                rh=round(rh, 3),
                wind_mps=round(wind, 3),
                ghi_wm2=round(ghi, 2),
            )
        )

    return WeatherSeries(
        points=points,
        source_kind="forecast",
        station_or_cell="synthetic-spike-b",
        distance_km=0.0,
        provenance=Provenance(
            source="synthetic-spike-b",
            retrieved_at_utc=start_utc,
            terms_tag="synthetic-fixture-no-real-weather",
        ),
    )
