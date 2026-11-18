"""Tests for the change-point baseline calibrator and its report (issue #10).

The calibrator is the pure-core cross-check / fallback calibrator of the
project: a PRISM-lineage change-point regression over monthly billing data.

What is asserted here:

* **Recovery** — on a synthetic fixture with *known* change-points and
  slopes, the calibrator recovers them within the tolerances stated below.
* **LAW-1** — every calibrated parameter is reported as a range; the
  rendered report carries no point-only figure.
* **LAW-2** — the Guideline 14 metrics are always present and always shown
  with the thresholds they are judged against.
* **LAW-3** — too few billing periods, missing weather and confounded
  (dual-fuel) signals *degrade* the run with a named reason; they never
  raise and never produce a silent best-effort result.

All fixtures are synthetic (LAW-11).
"""

from __future__ import annotations

import math
import random
import re
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from home_energy_twin.changepoint import (
    ChangePointCalibrator,
    Guideline14Thresholds,
    bootstrap_parameter_ranges,
    cvrmse_pct,
    fit_change_point_baseline,
    nmbe_pct,
    predict,
)
from home_energy_twin.types import (
    EnergyPoint,
    House,
    HPXMLRef,
    LatLon,
    ParameterEstimate,
    Provenance,
    SystemInventory,
    UtilitySeries,
    WeatherPoint,
    WeatherSeries,
)

UTC = timezone.utc

# --------------------------------------------------------------------------
# the synthetic fixture: known change-points, known slopes
# --------------------------------------------------------------------------

#: The heating change-point of the fixture (°C).
TRUE_HEATING_CHANGE_POINT_C = -5.0

#: The cooling change-point of the fixture (°C).
TRUE_COOLING_CHANGE_POINT_C = 18.0

#: The heating slope of the fixture, as ``d(energy) / d(temperature)``.
TRUE_HEATING_SLOPE = -1.2

#: The slope between the two change-points.
TRUE_MIDDLE_SLOPE = 0.15

#: The cooling slope of the fixture.
TRUE_COOLING_SLOPE = 0.45

#: Tolerances the recovery test states (the test's own contract).
CHANGE_POINT_TOLERANCE_C = 2.5
HEATING_SLOPE_TOLERANCE_PCT = 30.0
COOLING_SLOPE_TOLERANCE_PCT = 30.0

FIXTURE_SEED = 7
FIXTURE_NOISE_PCT = 2.0
FIXTURE_YEARS = (2023, 2024)


def _fixture_truth(mean_temp_c: float) -> float:
    """The fixture's true daily energy (kWh/day) at a mean temperature."""
    if mean_temp_c <= TRUE_HEATING_CHANGE_POINT_C:
        return 4.0 + abs(TRUE_HEATING_SLOPE) * (
            TRUE_HEATING_CHANGE_POINT_C - mean_temp_c
        )
    if mean_temp_c <= TRUE_COOLING_CHANGE_POINT_C:
        return 4.0 + TRUE_MIDDLE_SLOPE * (
            mean_temp_c - TRUE_HEATING_CHANGE_POINT_C
        )
    return (
        4.0
        + TRUE_MIDDLE_SLOPE * (TRUE_COOLING_CHANGE_POINT_C - TRUE_HEATING_CHANGE_POINT_C)
        + TRUE_COOLING_SLOPE * (mean_temp_c - TRUE_COOLING_CHANGE_POINT_C)
    )


def _provenance() -> Provenance:
    return Provenance(
        source="synthetic-changepoint-fixture",
        retrieved_at_utc=datetime(2025, 1, 1, tzinfo=UTC),
        terms_tag="CC0-1.0 (synthetic)",
    )


def _synthetic_weather(
    years: tuple[int, int] = FIXTURE_YEARS,
    seed: int = FIXTURE_SEED,
) -> WeatherSeries:
    """Two years of synthetic daily dry-bulb weather (LAW-11)."""
    rng = random.Random(f"changepoint-weather:{seed}")
    points: list[WeatherPoint] = []
    day = datetime(years[0], 1, 1, tzinfo=UTC)
    end = datetime(years[-1] + 1, 1, 1, tzinfo=UTC)
    while day < end:
        day_of_year = day.timetuple().tm_yday
        seasonal = 10.0 - 25.0 * math.cos(
            2.0 * math.pi * (day_of_year - 15.0) / 365.0
        )
        points.append(
            WeatherPoint(timestamp_utc=day, dry_bulb_c=seasonal + rng.gauss(0.0, 1.5))
        )
        day += timedelta(days=1)
    return WeatherSeries(
        points=points,
        source_kind="observed",
        station_or_cell="SYNTHETIC-STATION",
        distance_km=1.0,
        provenance=_provenance(),
    )


def _synthetic_bills(
    weather: WeatherSeries,
    fuel: str = "natural_gas",
    seed: int = FIXTURE_SEED + 1,
    noise_pct: float = FIXTURE_NOISE_PCT,
) -> UtilitySeries:
    """Monthly bills generated from the known change-point truth."""
    rng = random.Random(f"changepoint-bills:{seed}")
    monthly: dict[tuple[int, int], list[float]] = {}
    for point in weather.points:
        key = (point.timestamp_utc.year, point.timestamp_utc.month)
        monthly.setdefault(key, []).append(point.dry_bulb_c)

    points: list[EnergyPoint] = []
    for (year, month), temperatures in sorted(monthly.items()):
        mean_temp = sum(temperatures) / len(temperatures)
        days = len(temperatures)
        value = _fixture_truth(mean_temp) * days
        value *= 1.0 + rng.gauss(0.0, noise_pct / 100.0)
        start = datetime(year, month, 1, tzinfo=UTC)
        end = (
            datetime(year + 1, 1, 1, tzinfo=UTC)
            if month == 12
            else datetime(year, month + 1, 1, tzinfo=UTC)
        )
        points.append(
            EnergyPoint(
                start_utc=start,
                end_utc=end,
                value=max(0.0, value),
                unit="kWh",
            )
        )
    return UtilitySeries(
        fuel=fuel,  # type: ignore[arg-type]
        kind="import",
        granularity="billing_period",
        points=points,
        stamp_semantics="interval_start",
        provenance=_provenance(),
    )


def _house(
    *,
    dual_fuel: bool = False,
    has_pv: bool = False,
) -> House:
    return House(
        house_id="11111111-2222-3333-4444-555555555555",
        hpxml_ref=HPXMLRef(uri="synthetic/house.xml", sha256="0" * 64),
        timezone="America/New_York",
        location=LatLon(latitude=40.0, longitude=-75.0),
        systems=SystemInventory(
            heating_fuels=["natural_gas"],
            cooling_fuels=["electricity"],
            dhw_fuels=["natural_gas"],
            dual_fuel=dual_fuel,
            has_pv=has_pv,
        ),
        state_hash="synthetic-state-hash",
    )


def _calibrator(replicates: int = 40) -> ChangePointCalibrator:
    return ChangePointCalibrator(bootstrap_replicates=replicates, bootstrap_seed=3)


# --------------------------------------------------------------------------
# recovery of the known change-points and slopes
# --------------------------------------------------------------------------


def test_recovers_the_known_change_points_and_slopes() -> None:
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    report = _calibrator().calibrate_report(_house(), [bills], weather)

    heating_knot = report.optional_parameter(
        "natural_gas.heating_change_point_c"
    )
    cooling_knot = report.optional_parameter(
        "natural_gas.cooling_change_point_c"
    )
    heating_slope = report.optional_parameter(
        "natural_gas.heating_slope_kwh_per_kday"
    )
    cooling_slope = report.optional_parameter(
        "natural_gas.cooling_slope_kwh_per_kday"
    )
    assert heating_knot is not None
    assert cooling_knot is not None
    assert heating_slope is not None
    assert cooling_slope is not None

    assert (
        abs(heating_knot.value - TRUE_HEATING_CHANGE_POINT_C)
        <= CHANGE_POINT_TOLERANCE_C
    ), f"heating change-point {heating_knot.value} degC"
    assert (
        abs(cooling_knot.value - TRUE_COOLING_CHANGE_POINT_C)
        <= CHANGE_POINT_TOLERANCE_C
    ), f"cooling change-point {cooling_knot.value} degC"
    assert (
        abs(heating_slope.value - TRUE_HEATING_SLOPE) / abs(TRUE_HEATING_SLOPE)
        <= HEATING_SLOPE_TOLERANCE_PCT / 100.0
    ), f"heating slope {heating_slope.value}"
    assert (
        abs(cooling_slope.value - TRUE_COOLING_SLOPE) / abs(TRUE_COOLING_SLOPE)
        <= COOLING_SLOPE_TOLERANCE_PCT / 100.0
    ), f"cooling slope {cooling_slope.value}"


def test_recovered_ranges_cover_the_truth() -> None:
    """Range honesty: the reported interval covers the known truth."""
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    report = _calibrator().calibrate_report(_house(), [bills], weather)

    expectations = {
        "natural_gas.heating_change_point_c": TRUE_HEATING_CHANGE_POINT_C,
        "natural_gas.cooling_change_point_c": TRUE_COOLING_CHANGE_POINT_C,
        "natural_gas.heating_slope_kwh_per_kday": TRUE_HEATING_SLOPE,
        "natural_gas.cooling_slope_kwh_per_kday": TRUE_COOLING_SLOPE,
    }
    for name, truth in expectations.items():
        estimate = report.optional_parameter(name)
        assert estimate is not None, name
        assert estimate.low <= truth <= estimate.high, (
            f"{name}: reported range [{estimate.low}, {estimate.high}] "
            f"does not cover the truth {truth}"
        )


def test_report_carries_guideline14_metrics_and_their_thresholds() -> None:
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    report = _calibrator().calibrate_report(_house(), [bills], weather)

    assert report.metrics.granularity == "billing_period"
    assert report.thresholds.nmbe_abs_pct == 5.0
    assert report.thresholds.cvrmse_max_pct == 15.0
    assert abs(report.metrics.nmbe_pct) <= 5.0
    assert report.metrics.cvrmse_pct <= 15.0
    assert report.metrics.guideline14_pass is True
    assert report.metrics_basis == "change-point baseline fit"


def test_thresholds_object_states_the_monthly_targets() -> None:
    thresholds = Guideline14Thresholds(
        granularity="billing_period", nmbe_abs_pct=5.0, cvrmse_max_pct=15.0
    )
    assert "5" in thresholds.describe()
    assert "15" in thresholds.describe()


# --------------------------------------------------------------------------
# LAW-1 — ranges, never point-only figures
# --------------------------------------------------------------------------

_NUMBER = re.compile(r"-?\d+\.\d+")


def test_report_text_emits_no_point_only_figure() -> None:
    """Every parameter line carries exactly the two ends of its interval."""
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    report = _calibrator().calibrate_report(_house(), [bills], weather)
    text = report.to_text()

    lines = [line for line in text.splitlines() if line.startswith("  ")]
    parameter_lines = [
        line
        for line in lines
        if any(line.strip().startswith(name) for name in report.parameters)
    ]
    assert parameter_lines, "the report must render its parameters"

    for line in parameter_lines:
        numbers = _NUMBER.findall(line)
        assert len(numbers) == 2, f"a parameter line must show a range: {line!r}"

    for name, estimate in report.parameters.items():
        assert isinstance(estimate, ParameterEstimate)
        assert estimate.low <= estimate.value <= estimate.high
        expected = [f"{estimate.low:.3f}", f"{estimate.high:.3f}"]
        rendered = [
            line
            for line in parameter_lines
            if line.strip().startswith(name)
        ][0]
        assert _NUMBER.findall(rendered) == expected, name


def test_report_parameters_are_ranges_not_points() -> None:
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    report = _calibrator().calibrate_report(_house(), [bills], weather)
    assert report.parameters, "the fit must produce parameters"
    for name, estimate in report.parameters.items():
        assert estimate.low <= estimate.high, name
        assert estimate.unit, name
        assert estimate.method, name
    ranges = report.parameter_ranges()
    for name, (low, high) in ranges.items():
        assert low <= high, name


# --------------------------------------------------------------------------
# LAW-3 — degradation is a reported outcome, not an exception
# --------------------------------------------------------------------------


def test_too_few_billing_periods_degrades_with_a_reason() -> None:
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    short = UtilitySeries(
        fuel=bills.fuel,
        kind=bills.kind,
        granularity=bills.granularity,
        points=bills.points[:6],
        stamp_semantics=bills.stamp_semantics,
        provenance=bills.provenance,
    )
    report = _calibrator().calibrate_report(_house(), [short], weather)

    assert report.degraded is True
    assert report.has_degradation_reason("insufficient_data")
    assert "insufficient_data" in report.to_text()
    assert report.parameters == {}
    # LAW-2 still holds: the metrics are present even when degraded.
    assert report.metrics.granularity == "billing_period"


def test_missing_weather_degrades_with_a_reason() -> None:
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    empty_weather = WeatherSeries(
        points=[],
        source_kind="observed",
        station_or_cell="SYNTHETIC-STATION",
        distance_km=1.0,
        provenance=_provenance(),
    )
    report = _calibrator().calibrate_report(_house(), [bills], empty_weather)

    assert report.degraded is True
    assert report.has_degradation_reason("insufficient_data")
    assert report.parameters == {}


def test_tmy_weather_is_refused_for_calibration() -> None:
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    tmy = WeatherSeries(
        points=weather.points,
        source_kind="tmy",
        station_or_cell="SYNTHETIC-STATION",
        distance_km=1.0,
        provenance=_provenance(),
    )
    report = _calibrator().calibrate_report(_house(), [bills], tmy)

    assert report.degraded is True
    assert report.has_degradation_reason("law_10")


def test_confounded_dual_fuel_house_degrades_with_a_reason() -> None:
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    report = _calibrator().calibrate_report(
        _house(dual_fuel=True), [bills], weather
    )

    assert report.degraded is True
    assert report.has_degradation_reason("confounded_configuration")
    assert "confounded_configuration" in report.to_text()


def test_pv_house_degrades_with_a_reason() -> None:
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    report = _calibrator().calibrate_report(_house(has_pv=True), [bills], weather)

    assert report.degraded is True
    assert report.has_degradation_reason("confounded_configuration")


def test_degradation_never_raises() -> None:
    weather = _synthetic_weather()
    empty_weather = WeatherSeries(
        points=[],
        source_kind="observed",
        station_or_cell="SYNTHETIC-STATION",
        distance_km=1.0,
        provenance=_provenance(),
    )
    report = _calibrator().calibrate_report(_house(), [], empty_weather)
    assert report.degraded is True
    assert report.degradation_reasons


# --------------------------------------------------------------------------
# the Calibrator protocol surface
# --------------------------------------------------------------------------


def test_calibrate_returns_a_calibrated_model_with_ranges_and_metrics() -> None:
    from home_energy_twin.protocols import Calibrator

    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    calibrator = _calibrator()
    assert isinstance(calibrator, Calibrator)

    model = calibrator.calibrate(_house(), [bills], weather)

    assert model.calibrator_id == "changepoint-baseline"
    assert model.calibrator_version == "0.1"
    assert model.data_granularity == "billing_period"
    assert model.parameters, "the model must carry the fitted parameters"
    for name, estimate in model.parameters.items():
        assert estimate.low <= estimate.value <= estimate.high, name
    assert model.metrics.granularity == "billing_period"
    assert model.state_hash == _house().state_hash


def test_degraded_calibration_is_not_marked_valid() -> None:
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather)
    model = _calibrator().calibrate(_house(dual_fuel=True), [bills], weather)
    assert model.status != "valid"


# --------------------------------------------------------------------------
# metric helpers
# --------------------------------------------------------------------------


def test_nmbe_and_cvrmse_follow_the_guideline14_definitions() -> None:
    observed = [10.0, 20.0, 30.0]
    residuals = [1.0, -1.0, 0.0]
    assert math.isclose(nmbe_pct(residuals, observed), 0.0, abs_tol=1e-12)
    assert cvrmse_pct(residuals, observed, 1) > 0.0
    # A perfect fit has zero bias and zero CV(RMSE).
    assert nmbe_pct([0.0, 0.0, 0.0], observed) == 0.0
    assert cvrmse_pct([0.0, 0.0, 0.0], observed, 1) == 0.0


# --------------------------------------------------------------------------
# property tests (AGENTS.md: calibration code lands with property tests)
# --------------------------------------------------------------------------


@settings(max_examples=25, deadline=None)
@given(
    offsets=st.lists(
        st.floats(min_value=-3.0, max_value=3.0, allow_nan=False),
        min_size=12,
        max_size=12,
    )
)
def test_fit_reports_ranges_containing_the_point_value(offsets: list[float]) -> None:
    """Whatever the data, a reported range always brackets its centre.

    It is worth stating which invariants this asserts, because the
    *obvious* candidate is not one of them: **the three branch slopes are
    not ordered**.  Neither specs/SPEC.md nor the change-point model
    requires the heating slope to be below the middle slope, or the middle
    slope to be below the cooling slope: the baseline is an unconstrained
    least-squares fit of a piecewise-linear function, and on data whose
    true shape is a single straight line (as here) the branch slopes are
    whatever least squares returns — the cooling branch included, and it
    may legitimately tilt the other way.  An ordering assertion would
    therefore test a property the design does not have (and that floating
    point alone breaks: ``0.3000000000000001 <= 0.3`` is false).

    What the design *does* guarantee, and what is asserted here:

    * the two change-points are ordered — the knot search only accepts a
      cooling knot above the heating knot;
    * the reported parameters reproduce the fitted values through
      ``predict`` — the report and the Guideline 14 metrics cannot
      disagree about the model that was fitted;
    * the fit is the least-squares solution at its change-points (the
      residuals are orthogonal to every column of the design matrix), so
      the point estimate really is the optimum of the criterion the
      calibrator claims to minimise; and
    * the fit is never worse than the mean-only baseline it nests; and
    * every reported range brackets its own point value (LAW-1) — a range
      that excludes its own centre is not an honest range.
    """
    xs = [float(index) * 2.0 - 12.0 for index in range(12)]
    ys = [5.0 + 0.3 * x + offset for x, offset in zip(xs, offsets)]
    fit = fit_change_point_baseline(xs, ys)
    assert fit is not None, "twelve distinct temperatures are always fittable"
    parameters = fit.parameters

    # The knot search only accepts a cooling knot above the heating knot.
    assert parameters.heating_knot_c < parameters.cooling_knot_c

    # The reported parameters reproduce the fitted values.
    for x, fitted in zip(xs, fit.fitted):
        assert math.isclose(
            predict(parameters, x), fitted, rel_tol=1e-9, abs_tol=1e-9
        ), f"predict disagrees with the fit at {x}"

    # The fit is the least-squares solution at its knots: the residuals are
    # orthogonal to the design matrix (constant, x - k1, (x - k1)+, (x - k2)+).
    heating_knot = parameters.heating_knot_c
    cooling_knot = parameters.cooling_knot_c
    columns = (
        [1.0] * len(xs),
        [x - heating_knot for x in xs],
        [max(0.0, x - heating_knot) for x in xs],
        [max(0.0, x - cooling_knot) for x in xs],
    )
    for column in columns:
        assert math.isclose(
            math.fsum(
                value * residual
                for value, residual in zip(column, fit.residuals)
            ),
            0.0,
            abs_tol=1e-6,
        ), "the fit is not the least-squares solution at its knots"

    # The model nests the mean-only baseline, so it cannot fit worse.
    mean = math.fsum(ys) / len(ys)
    mean_only_sse = math.fsum((y - mean) ** 2 for y in ys)
    assert fit.sse <= mean_only_sse + 1e-9

    # LAW-1: every reported range brackets its own point value.
    ranges = bootstrap_parameter_ranges(fit, xs, ys, replicates=3, seed=11)
    point_values = {
        "heating_change_point_c": parameters.heating_knot_c,
        "cooling_change_point_c": parameters.cooling_knot_c,
        "heating_slope_kwh_per_kday": parameters.heating_slope,
        "middle_slope_kwh_per_kday": parameters.middle_slope,
        "cooling_slope_kwh_per_kday": parameters.cooling_slope,
        "load_at_heating_knot_kwh_per_day": (
            parameters.load_at_heating_knot_kwh_per_day
        ),
    }
    for name, value in point_values.items():
        low, high = ranges[name]
        assert low <= value <= high, f"{name}: [{low}, {high}] excludes {value}"


@settings(max_examples=10, deadline=None)
@given(
    noise=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
)
def test_report_never_emits_a_point_only_figure(noise: float) -> None:
    """LAW-1 property: the rendered report only ever shows intervals."""
    weather = _synthetic_weather()
    bills = _synthetic_bills(weather, noise_pct=noise)
    report = _calibrator(replicates=5).calibrate_report(_house(), [bills], weather)
    text = report.to_text()
    for line in text.splitlines():
        if line.startswith("  ") and any(
            line.strip().startswith(name) for name in report.parameters
        ):
            assert len(_NUMBER.findall(line)) == 2, line
