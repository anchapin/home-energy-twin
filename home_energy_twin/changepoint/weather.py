"""Weather features for the change-point baseline.

The change-point baseline is a regression of billing-period energy on the
period's mean outdoor temperature, so the calibrator needs two things from
the weather series:

* a **daily mean dry-bulb temperature** per UTC day, and
* per billing period: the number of days, the mean temperature, and the
  degree-day totals at the 18 °C balance point.

Rules honoured here:

* **LAW-10** — calibration uses *actual* (observed/reanalysis) weather only.
  TMY or forecast weather is refused by the calibrator with a named reason,
  instead of being silently used.
* **LAW-5** — all bucketing is done on UTC; the house's local time is not
  needed for a monthly balance-point regression.
* **LAW-11** — no real data: the functions operate on whatever series they
  are handed and never fetch anything (no network calls in the core).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Sequence

from ..types import UtilitySeries, WeatherSeries

__all__ = [
    "BALANCE_POINT_C",
    "MIN_WEATHER_COVERAGE",
    "PeriodFeatures",
    "daily_mean_temperatures",
    "period_features",
    "series_period_features",
    "usable_features",
]

#: The balance-point temperature (°C) used for the degree-day integrals.
BALANCE_POINT_C = 18.0

#: Fraction of a billing period's days that must be covered by weather for
#: the period to be usable.
MIN_WEATHER_COVERAGE = 0.5


@dataclass(frozen=True)
class PeriodFeatures:
    """Weather features of one billing period."""

    key: tuple[int, int]
    days: int
    mean_temp_c: float
    hdd_kk: float
    cdd_kk: float
    covered_days: int

    @property
    def weather_coverage(self) -> float:
        """Fraction of the period's days covered by weather observations."""
        if self.days <= 0:
            return 0.0
        return self.covered_days / self.days

    @property
    def has_weather(self) -> bool:
        """True when the period has usable weather coverage."""
        return (
            self.days > 0
            and self.covered_days > 0
            and self.weather_coverage >= MIN_WEATHER_COVERAGE
            and self.mean_temp_c == self.mean_temp_c  # not NaN
        )


def daily_mean_temperatures(weather: WeatherSeries) -> dict[date, float]:
    """Mean dry-bulb temperature per UTC day.

    Hourly or sub-hourly points are averaged within the day; a daily series
    is taken as-is.  Days without any observation are absent from the map.
    """
    totals: dict[date, list[float]] = {}
    for point in weather.points:
        day = point.timestamp_utc.date()
        totals.setdefault(day, []).append(point.dry_bulb_c)
    return {day: sum(values) / len(values) for day, values in totals.items()}


def _days_of(start_utc: datetime, end_utc: datetime) -> list[date]:
    """The UTC dates spanned by ``[start, end)``."""
    start = start_utc.date()
    end = end_utc.date()
    days: list[date] = []
    current = start
    while current < end:
        days.append(current)
        current = current + timedelta(days=1)
    if not days:
        days.append(start)
    return days


def period_features(
    start_utc: datetime,
    end_utc: datetime,
    daily: dict[date, float],
) -> PeriodFeatures:
    """Weather features for one billing period ``[start, end)``."""
    days = _days_of(start_utc, end_utc)
    temperatures = [daily[day] for day in days if day in daily]
    covered = len(temperatures)
    if not temperatures:
        return PeriodFeatures(
            key=(start_utc.year, start_utc.month),
            days=len(days),
            mean_temp_c=float("nan"),
            hdd_kk=0.0,
            cdd_kk=0.0,
            covered_days=0,
        )
    mean_temp = sum(temperatures) / covered
    hdd = sum(max(0.0, BALANCE_POINT_C - value) for value in temperatures)
    cdd = sum(max(0.0, value - BALANCE_POINT_C) for value in temperatures)
    return PeriodFeatures(
        key=(start_utc.year, start_utc.month),
        days=len(days),
        mean_temp_c=mean_temp,
        hdd_kk=hdd,
        cdd_kk=cdd,
        covered_days=covered,
    )


def series_period_features(
    series: UtilitySeries,
    weather: WeatherSeries,
) -> tuple[list[tuple[tuple[int, int], float]], list[PeriodFeatures]]:
    """Pair every billing period of ``series`` with its weather features.

    Returns the ``(period key, energy kWh)`` observations and the matching
    :class:`PeriodFeatures`.  Periods with insufficient weather coverage are
    still returned (the caller decides how to degrade on them).
    """
    daily = daily_mean_temperatures(weather)
    observations: list[tuple[tuple[int, int], float]] = []
    features: list[PeriodFeatures] = []
    for point in series.points:
        key = (point.start_utc.year, point.start_utc.month)
        observations.append((key, point.value))
        features.append(period_features(point.start_utc, point.end_utc, daily))
    return observations, features


def usable_features(features: Sequence[PeriodFeatures]) -> list[PeriodFeatures]:
    """The subset of ``features`` with enough weather coverage to fit on."""
    return [item for item in features if item.has_weather]
