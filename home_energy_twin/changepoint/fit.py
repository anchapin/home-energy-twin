"""Change-point (PRISM-lineage) baseline regression.

The change-point baseline is the pure-core, offline cross-check / fallback
calibrator of the project (docs/PLAN.md §4: "Change-point baseline — Build
(thin) — PRISM / ASHRAE change-point regression — Literature (Fels; G14)").

Model
-----
For one fuel and one billing period the baseline predicts the *daily* energy

    y = c + m1·(x − k₁) + m2·(x − k₁)⁺ + m3·(x − k₂)⁺

where ``x`` is the period's mean outdoor temperature (°C), ``k₁`` is the
**heating change-point** (the temperature below which the heating slope
changes) and ``k₂`` is the **cooling change-point** (the temperature above
which the cooling slope changes).  The function is continuous and piecewise
linear; its three slopes are

    below k₁            : m₁            (heating)
    between k₁ and k₂   : m₁ + m₂
    above k₂            : m₁ + m₂ + m₃  (cooling)

which is the shape a PRISM-style balance-point regression produces: the
heating branch is dominated by the envelope, the shoulder is the
non-weather-dependent load, and the cooling branch picks up the cooling
equipment.

Estimation
----------
The change-points are searched on a grid (coarse pass, then a local refine)
and the coefficients are solved by ordinary least squares for each candidate
pair; the candidate with the lowest BIC wins.  Uncertainty comes from a
**residual bootstrap** (SPEC §5: "residual-bootstrap ranges for the
change-point baseline"), so every reported parameter is a *range* (LAW-1) —
never a bare point.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Optional, Sequence

__all__ = [
    "COOLING_KNOT_RANGE_C",
    "DEFAULT_BOOTSTRAP_REPLICATES",
    "HEATING_KNOT_RANGE_C",
    "KNOT_GRID_STEP_C",
    "MIN_PERIODS_FOR_FIT",
    "PiecewiseFit",
    "FitParameters",
    "bootstrap_parameter_ranges",
    "fit_change_point_baseline",
    "predict",
]


#: Minimum number of usable monthly billing periods for a change-point fit.
MIN_PERIODS_FOR_FIT = 12

#: Coarse search grid for the change-points (°C).
KNOT_GRID_STEP_C = 1.0

#: Heating change-point search range (°C).
HEATING_KNOT_RANGE_C = (-25.0, 15.0)

#: Cooling change-point search range (°C).
COOLING_KNOT_RANGE_C = (5.0, 30.0)

#: Default number of residual-bootstrap replicates.
DEFAULT_BOOTSTRAP_REPLICATES = 200

#: BIC window around the best change-point pair whose alternatives count as
#: statistically indistinguishable (ΔBIC ≈ 6 is the conventional "strong
#: evidence" boundary).  Those alternatives carry the change-point
#: *selection* uncertainty into the reported ranges.
KNOT_BIC_TOLERANCE = 6.0

#: Number of estimated parameters of the piecewise model (4 coefficients and
#: 2 change-points) — used for the G14 degrees of freedom.
N_MODEL_PARAMETERS = 6

_Z95 = 1.959964


@dataclass(frozen=True)
class FitParameters:
    """The estimated parameters of one change-point fit."""

    heating_knot_c: float
    cooling_knot_c: float
    intercept: float
    heating_slope: float
    middle_slope: float
    cooling_slope: float
    load_at_heating_knot_kwh_per_day: float


@dataclass(frozen=True)
class PiecewiseFit:
    """A fitted change-point baseline for one fuel."""

    parameters: FitParameters
    residuals: tuple[float, ...]
    fitted: tuple[float, ...]
    observed: tuple[float, ...]
    n_periods: int
    #: Parameters of the BIC-plausible alternative change-point pairs
    #: (empty when the fit did not come from the candidate search).
    candidate_parameters: tuple[FitParameters, ...] = ()

    @property
    def sse(self) -> float:
        """Sum of squared residuals."""
        return math.fsum(r * r for r in self.residuals)


# --------------------------------------------------------------------------
# small dense linear algebra (no third-party dependency, deterministic)
# --------------------------------------------------------------------------


def _solve(matrix: list[list[float]], rhs: list[float]) -> Optional[list[float]]:
    """Solve a small dense system by Gaussian elimination with partial pivot."""
    size = len(rhs)
    augmented = [row[:] + [rhs[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column] / augmented[column][column]
            for cell in range(column, size + 1):
                augmented[row][cell] -= factor * augmented[column][cell]
    return [augmented[index][size] / augmented[index][index] for index in range(size)]


def _design_row(x: float, heating_knot: float, cooling_knot: float) -> list[float]:
    """One row of the piecewise-linear design matrix."""
    return [
        1.0,
        x - heating_knot,
        max(0.0, x - heating_knot),
        max(0.0, x - cooling_knot),
    ]


def _fit_for_knots(
    xs: Sequence[float],
    ys: Sequence[float],
    heating_knot: float,
    cooling_knot: float,
) -> Optional[tuple[list[float], list[float], list[float]]]:
    """OLS fit for fixed change-points.

    Returns ``(coefficients, fitted, residuals)`` or ``None`` when the normal
    equations are singular (the candidate is then simply not identifiable).
    """
    rows = [_design_row(x, heating_knot, cooling_knot) for x in xs]
    normal = [
        [math.fsum(row[i] * row[j] for row in rows) for j in range(4)]
        for i in range(4)
    ]
    rhs = [math.fsum(row[i] * y for row, y in zip(rows, ys)) for i in range(4)]
    coefficients = _solve(normal, rhs)
    if coefficients is None:
        return None
    fitted = [
        math.fsum(value * coefficient for value, coefficient in zip(row, coefficients))
        for row in rows
    ]
    residuals = [y - f for y, f in zip(ys, fitted)]
    return coefficients, fitted, residuals


def _bic(sse: float, count: int) -> float:
    """Bayesian information criterion of a candidate fit."""
    if sse <= 0.0:
        sse = 1e-12
    return count * math.log(sse / count) + N_MODEL_PARAMETERS * math.log(count)


def _grid(start: float, stop: float, step: float) -> list[float]:
    """A grid of candidate values from ``start`` to ``stop`` inclusive."""
    values: list[float] = []
    current = start
    while current <= stop + 1e-9:
        values.append(round(current, 6))
        current += step
    return values


def _search_knots(
    xs: Sequence[float],
    ys: Sequence[float],
    step: float = KNOT_GRID_STEP_C,
    refine: bool = True,
) -> Optional[tuple[float, float, list[float], list[float], list[float]]]:
    """Search the change-point pair that minimises BIC."""
    search = _search_knots_with_candidates(xs, ys, step=step, refine=refine)
    if search is None:
        return None
    best, _ = search
    return best


def _search_knots_with_candidates(
    xs: Sequence[float],
    ys: Sequence[float],
    step: float = KNOT_GRID_STEP_C,
    refine: bool = True,
) -> Optional[
    tuple[
        tuple[float, float, list[float], list[float], list[float]],
        list[tuple[float, float, list[float], float]],
    ]
]:
    """Search the change-points and keep the BIC-plausible alternatives.

    The second element of the result is the list of candidate change-point
    pairs that are statistically indistinguishable from the best one (their
    BIC is within :data:`KNOT_BIC_TOLERANCE` of the best BIC).  Those
    alternatives carry the *change-point selection* uncertainty, which the
    residual bootstrap alone does not see.
    """
    heating_grid = _grid(*HEATING_KNOT_RANGE_C, step)
    cooling_grid = _grid(*COOLING_KNOT_RANGE_C, step)

    candidates: list[tuple[float, float, list[float], float]] = []
    best: Optional[tuple[float, float, list[float], list[float], list[float]]] = None
    best_score = math.inf
    for heating_knot in heating_grid:
        for cooling_knot in cooling_grid:
            if cooling_knot <= heating_knot:
                continue
            candidate = _fit_for_knots(xs, ys, heating_knot, cooling_knot)
            if candidate is None:
                continue
            coefficients, fitted, residuals = candidate
            score = _bic(math.fsum(r * r for r in residuals), len(xs))
            candidates.append((heating_knot, cooling_knot, coefficients, score))
            if score < best_score:
                best_score = score
                best = (heating_knot, cooling_knot, coefficients, fitted, residuals)

    if best is None:
        return None

    # Local refinement of the winning pair on a quarter-degree grid.
    heating_knot, cooling_knot, coefficients, fitted, residuals = best
    best_score = _bic(math.fsum(r * r for r in residuals), len(xs))
    if not refine:
        return (heating_knot, cooling_knot, coefficients, fitted, residuals), candidates
    for heating_offset in (-0.5, -0.25, 0.0, 0.25, 0.5):
        for cooling_offset in (-0.5, -0.25, 0.0, 0.25, 0.5):
            candidate_heating = heating_knot + heating_offset
            candidate_cooling = cooling_knot + cooling_offset
            if candidate_cooling <= candidate_heating:
                continue
            candidate = _fit_for_knots(xs, ys, candidate_heating, candidate_cooling)
            if candidate is None:
                continue
            candidate_coefficients, candidate_fitted, candidate_residuals = candidate
            score = _bic(
                math.fsum(r * r for r in candidate_residuals), len(xs)
            )
            if score < best_score:
                best_score = score
                heating_knot = candidate_heating
                cooling_knot = candidate_cooling
                coefficients = candidate_coefficients
                fitted = candidate_fitted
                residuals = candidate_residuals
    return (heating_knot, cooling_knot, coefficients, fitted, residuals), candidates


def _parameters_from(
    heating_knot: float,
    cooling_knot: float,
    coefficients: Sequence[float],
) -> FitParameters:
    """Turn the OLS coefficients into the reported fit parameters."""
    intercept, m1, m2, m3 = coefficients
    return FitParameters(
        heating_knot_c=heating_knot,
        cooling_knot_c=cooling_knot,
        intercept=intercept,
        heating_slope=m1,
        middle_slope=m1 + m2,
        cooling_slope=m1 + m2 + m3,
        load_at_heating_knot_kwh_per_day=intercept,
    )


def predict(
    parameters: FitParameters,
    x: float,
) -> float:
    """Predicted daily energy (kWh/day) at mean temperature ``x`` (°C)."""
    heating_knot = parameters.heating_knot_c
    cooling_knot = parameters.cooling_knot_c
    return (
        parameters.intercept
        + parameters.heating_slope * (x - heating_knot)
        + (parameters.middle_slope - parameters.heating_slope)
        * max(0.0, x - heating_knot)
        + (parameters.cooling_slope - parameters.middle_slope)
        * max(0.0, x - cooling_knot)
    )


def fit_change_point_baseline(
    xs: Sequence[float],
    ys: Sequence[float],
) -> Optional[PiecewiseFit]:
    """Fit the piecewise-linear change-point baseline.

    ``xs`` are period mean temperatures (°C) and ``ys`` the matching daily
    energy (kWh/day).  Returns ``None`` when the fit is not identifiable
    (too few periods, no spread in temperature, or a singular design).
    """
    count = len(xs)
    if count != len(ys):
        raise ValueError("xs and ys must have the same length")
    if count < MIN_PERIODS_FOR_FIT:
        return None
    if max(xs) - min(xs) < 5.0:
        # Without a temperature spread the change-points are meaningless.
        return None
    search = _search_knots_with_candidates(xs, ys)
    if search is None:
        return None
    best, candidates = search
    heating_knot, cooling_knot, coefficients, fitted, residuals = best
    best_score = min((score for *_pair, score in candidates), default=math.inf)
    alternatives = tuple(
        _parameters_from(pair_heating, pair_cooling, pair_coefficients)
        for pair_heating, pair_cooling, pair_coefficients, score in candidates
        if score <= best_score + KNOT_BIC_TOLERANCE
    )
    return PiecewiseFit(
        parameters=_parameters_from(heating_knot, cooling_knot, coefficients),
        residuals=tuple(residuals),
        fitted=tuple(fitted),
        observed=tuple(ys),
        n_periods=count,
        candidate_parameters=alternatives,
    )


# --------------------------------------------------------------------------
# residual bootstrap (SPEC §5 — residual-bootstrap ranges)
# --------------------------------------------------------------------------


def _fit_for_bootstrap(
    xs: Sequence[float],
    ys: Sequence[float],
) -> Optional[PiecewiseFit]:
    """Refit inside a bootstrap replicate.

    The bootstrap uses a coarser change-point grid (and skips the local
    refinement) — the replicate distribution is a *range* estimate, so the
    extra precision of a quarter-degree refine is not worth the runtime.
    """
    if len(xs) < MIN_PERIODS_FOR_FIT:
        return None
    search = _search_knots(xs, ys, step=2.0, refine=False)
    if search is None:
        return None
    heating_knot, cooling_knot, coefficients, fitted, residuals = search
    return PiecewiseFit(
        parameters=_parameters_from(heating_knot, cooling_knot, coefficients),
        residuals=tuple(residuals),
        fitted=tuple(fitted),
        observed=tuple(ys),
        n_periods=len(xs),
    )


def _percentile(values: Sequence[float], quantile: float) -> float:
    """Linear-interpolation percentile of ``values``."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def bootstrap_parameter_ranges(
    fit: PiecewiseFit,
    xs: Sequence[float],
    ys: Sequence[float],
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = 0,
) -> dict[str, tuple[float, float]]:
    """Residual-bootstrap 95 % ranges for every reported parameter.

    The residuals of the fitted model are resampled with replacement and the
    model is refitted (change-point search included) on each replicate; the
    reported range is the 2.5 % / 97.5 % percentile of the replicate
    distribution.  Returns an empty mapping when no replicate could be
    fitted.
    """
    if replicates <= 0:
        return {}
    rng = random.Random(f"changepoint-bootstrap:{seed}")
    residuals = list(fit.residuals)
    samples: dict[str, list[float]] = {
        "heating_change_point_c": [],
        "cooling_change_point_c": [],
        "heating_slope_kwh_per_kday": [],
        "middle_slope_kwh_per_kday": [],
        "cooling_slope_kwh_per_kday": [],
        "load_at_heating_knot_kwh_per_day": [],
    }
    for _ in range(replicates):
        perturbed = [
            value + rng.choice(residuals) for value in fit.fitted
        ]
        replicate = _fit_for_bootstrap(xs, perturbed)
        if replicate is None:
            continue
        parameters = replicate.parameters
        samples["heating_change_point_c"].append(parameters.heating_knot_c)
        samples["cooling_change_point_c"].append(parameters.cooling_knot_c)
        samples["heating_slope_kwh_per_kday"].append(parameters.heating_slope)
        samples["middle_slope_kwh_per_kday"].append(parameters.middle_slope)
        samples["cooling_slope_kwh_per_kday"].append(parameters.cooling_slope)
        samples["load_at_heating_knot_kwh_per_day"].append(
            parameters.load_at_heating_knot_kwh_per_day
        )
    ranges: dict[str, tuple[float, float]] = {}
    for name, values in samples.items():
        if not values:
            continue
        low = _percentile(values, 0.025)
        high = _percentile(values, 0.975)
        if low > high:
            low, high = high, low
        ranges[name] = (low, high)
    # Widen each range with the change-point *selection* envelope: the
    # parameter values of the BIC-plausible alternative knot pairs, which
    # the residual bootstrap alone does not see, and always include the
    # point estimate itself — a reported range that excludes its own point
    # value is not an honest range.
    point = fit.parameters
    point_values = {
        "heating_change_point_c": point.heating_knot_c,
        "cooling_change_point_c": point.cooling_knot_c,
        "heating_slope_kwh_per_kday": point.heating_slope,
        "middle_slope_kwh_per_kday": point.middle_slope,
        "cooling_slope_kwh_per_kday": point.cooling_slope,
        "load_at_heating_knot_kwh_per_day": (
            point.load_at_heating_knot_kwh_per_day
        ),
    }
    for alternative in fit.candidate_parameters:
        alt_values = {
            "heating_change_point_c": alternative.heating_knot_c,
            "cooling_change_point_c": alternative.cooling_knot_c,
            "heating_slope_kwh_per_kday": alternative.heating_slope,
            "middle_slope_kwh_per_kday": alternative.middle_slope,
            "cooling_slope_kwh_per_kday": alternative.cooling_slope,
            "load_at_heating_knot_kwh_per_day": (
                alternative.load_at_heating_knot_kwh_per_day
            ),
        }
        for name, value in alt_values.items():
            if name in ranges:
                low, high = ranges[name]
                ranges[name] = (min(low, value), max(high, value))
    for name, value in point_values.items():
        if name in ranges:
            low, high = ranges[name]
            ranges[name] = (min(low, value), max(high, value))
    return ranges
