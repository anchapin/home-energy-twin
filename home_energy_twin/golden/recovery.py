"""Monthly recovery for the golden-house harness (SPEC §6).

This is the *harness side* of the monthly bar: a degree-day regression over
the generated monthly billing-period series, which recovers

* **NAC** — weather-normalised annual consumption, reported as a range
  (LAW-1) built from the regression's own uncertainty, and
* **UA** — the envelope UA, reported as a range (LAW-1).

Honesty note (important): monthly data identifies the *product* of UA and
equipment efficiency, never the two separately.  The reported UA range
therefore spans the efficiency band that is plausible for the declared
heating fuel (a declared-fuel prior, stated in the estimate's ``method``),
rather than pretending a point value.  That is exactly the "range honesty"
bar of SPEC §6 for the monthly granularity.

When the data or the house configuration cannot identify the parameters, the
recovery degrades explicitly (LAW-3) with a named reason.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from ..types import Fuel, ParameterEstimate, UtilitySeries, WeatherSeries
from .types import GoldenHouse, MonthlyRecovery
from .weather import monthly_degree_days

__all__ = [
    "EFFICIENCY_BANDS",
    "MIN_MONTHS_FOR_RECOVERY",
    "recover_monthly",
]

#: Minimum number of monthly billing periods needed for a 3-parameter fit.
MIN_MONTHS_FOR_RECOVERY = 6

#: Plausible efficiency band per heating fuel, used to turn the recovered
#: heating slope into a UA *range* (UA and efficiency are not separable from
#: monthly data).
EFFICIENCY_BANDS: dict[Fuel, tuple[float, float]] = {
    "electricity": (1.8, 3.6),  # heat pump / mini-split COP
    "natural_gas": (0.55, 0.98),  # furnace AFUE (old furnaces are low)
    "oil": (0.55, 0.92),
    "propane": (0.55, 0.92),
    "district": (0.80, 0.98),
}

_METHOD = "monthly degree-day regression (OLS)"

# 95 % two-sided normal quantile, used for the reported ranges.
_Z95 = 1.959964


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Solve a small dense linear system by Gaussian elimination."""
    size = len(rhs)
    augmented = [row[:] + [rhs[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular normal equations")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column] / augmented[column][column]
            for cell in range(column, size + 1):
                augmented[row][cell] -= factor * augmented[column][cell]
    return [augmented[index][size] / augmented[index][index] for index in range(size)]


def _inverse(matrix: list[list[float]]) -> list[list[float]]:
    """Invert a small dense matrix by Gauss-Jordan elimination."""
    size = len(matrix)
    augmented = [row[:] + [1.0 if i == j else 0.0 for j in range(size)] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        for cell in range(2 * size):
            augmented[column][cell] /= scale
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0.0:
                continue
            for cell in range(2 * size):
                augmented[row][cell] -= factor * augmented[column][cell]
    return [row[size:] for row in augmented]


def _observed_monthly(series: Sequence[UtilitySeries]) -> dict[tuple[int, int], float]:
    """Total observed energy per calendar month across all fuels."""
    totals: dict[tuple[int, int], float] = {}
    for one_series in series:
        for point in one_series.points:
            key = (point.start_utc.year, point.start_utc.month)
            totals[key] = totals.get(key, 0.0) + point.value
    return totals


def recover_monthly(
    house: GoldenHouse,
    series: Sequence[UtilitySeries],
    weather: WeatherSeries,
) -> MonthlyRecovery:
    """Recover NAC and UA from monthly billing periods and weather.

    The recovery never looks at the house's ground truth — it only sees the
    observed series, the weather, and the house's *declared* systems.
    """
    degree_days = monthly_degree_days(weather)
    if not degree_days:
        return MonthlyRecovery(
            months_used=0,
            degraded=True,
            degradation_reason="insufficient_data: weather series is empty",
            method=_METHOD,
        )

    dd_by_month = {(row.year, row.month): row for row in degree_days}
    observed = _observed_monthly(series)
    months = sorted(key for key in observed if key in dd_by_month)

    if len(months) < MIN_MONTHS_FOR_RECOVERY:
        return MonthlyRecovery(
            months_used=len(months),
            degraded=True,
            degradation_reason=(
                f"insufficient_data: {len(months)} monthly billing periods, "
                f"{MIN_MONTHS_FOR_RECOVERY} required"
            ),
            method=_METHOD,
        )

    design = [[1.0, dd_by_month[key].hdd_kk, dd_by_month[key].cdd_kk] for key in months]
    target = [observed[key] for key in months]

    normal = [[sum(row[i] * row[j] for row in design) for j in range(3)] for i in range(3)]
    rhs = [sum(row[i] * y for row, y in zip(design, target)) for i in range(3)]
    try:
        coefficients = _solve(normal, rhs)
    except ValueError:
        return MonthlyRecovery(
            months_used=len(months),
            degraded=True,
            degradation_reason=(
                "unidentifiable_configuration: degree days do not vary enough over "
                "the observed months to separate heating and cooling"
            ),
            method=_METHOD,
        )
    intercept, heating_slope, cooling_slope = coefficients

    residuals = [
        y - (row[0] * intercept + row[1] * heating_slope + row[2] * cooling_slope)
        for row, y in zip(design, target)
    ]
    dof = len(months) - 3
    sigma_squared = sum(r * r for r in residuals) / dof if dof > 0 else 0.0
    covariance = [[sigma_squared * value for value in row] for row in _inverse(normal)]

    annual_hdd = sum(row.hdd_kk for row in degree_days)
    annual_cdd = sum(row.cdd_kk for row in degree_days)

    def variance(weights: list[float]) -> float:
        total = 0.0
        for i in range(len(weights)):
            for j in range(len(weights)):
                total += weights[i] * covariance[i][j] * weights[j]
        return max(0.0, total)

    nac = 12.0 * intercept + heating_slope * annual_hdd + cooling_slope * annual_cdd
    nac_se = math.sqrt(variance([12.0, annual_hdd, annual_cdd]))
    nac_estimate = ParameterEstimate(
        value=max(0.0, nac),
        low=max(0.0, nac - _Z95 * nac_se),
        high=max(0.0, nac + _Z95 * nac_se),
        unit="kWh/year",
        method=_METHOD,
    )

    degradation_reason: Optional[str] = None
    if house.confounded:
        degradation_reason = (
            "confounded_configuration: PV / battery / EV / dual-fuel / aux-heat "
            "flags present, monthly recovery is degraded-confidence (LAW-3)"
        )

    heating_se = math.sqrt(variance([0.0, 1.0, 0.0]))
    band_low, band_high = EFFICIENCY_BANDS[house.heating_fuel]
    band_mid = 0.5 * (band_low + band_high)
    ua_estimate: Optional[ParameterEstimate] = None
    if heating_slope <= 0.0:
        degradation_reason = degradation_reason or (
            "unidentifiable_configuration: non-positive heating slope, UA not identifiable"
        )
    else:
        slope_low = max(0.0, heating_slope - _Z95 * heating_se)
        slope_high = max(0.0, heating_slope + _Z95 * heating_se)
        ua_estimate = ParameterEstimate(
            value=heating_slope * 1000.0 * band_mid / 24.0,
            low=slope_low * 1000.0 * band_low / 24.0,
            high=slope_high * 1000.0 * band_high / 24.0,
            unit="W/K",
            method=(
                f"{_METHOD}; UA = slope x efficiency band "
                f"[{band_low}, {band_high}] for declared fuel {house.heating_fuel!r}"
            ),
        )

    return MonthlyRecovery(
        nac_kwh=nac_estimate,
        ua_w_per_k=ua_estimate,
        months_used=len(months),
        heating_slope_kwh_per_hdd=heating_slope,
        cooling_slope_kwh_per_cdd=cooling_slope,
        degraded=degradation_reason is not None,
        degradation_reason=degradation_reason,
        method=_METHOD,
    )
