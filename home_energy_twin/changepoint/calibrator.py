"""The pure-core change-point baseline calibrator.

This is the offline cross-check / fallback calibrator of the project: a
PRISM-lineage (ASHRAE Guideline 14) change-point regression over monthly
billing data, implemented in the core with **no engine binary, no network
call and no telemetry** (RD-7, LAW-6).

It satisfies :class:`home_energy_twin.protocols.Calibrator` — core code
never imports it; selection between this calibrator and the out-of-process
HPXML-Calibration adapter is by configuration (AGENTS.md).

Honesty rules implemented here:

* **LAW-1** — every calibrated parameter is reported as a *range*
  (residual bootstrap, SPEC §5); no point-only figure is emitted.
* **LAW-2** — Guideline 14 metrics (monthly NMBE and CV(RMSE)) are always
  stored and always reported *with* their thresholds.
* **LAW-3** — when the data cannot support a fit (too few billing periods,
  confounded or dual-fuel signals, missing weather, TMY weather) the run
  **degrades with a named reason**; degradation is a reported outcome, not an
  exception, and never a silent best-effort result.
* **LAW-10** — only observed/reanalysis weather is used for calibration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from ..types import (
    CalibrationMetrics,
    CalibratedModel,
    Granularity,
    House,
    ParameterEstimate,
    TimeWindow,
    UtilitySeries,
    WeatherSeries,
)
from .fit import (
    DEFAULT_BOOTSTRAP_REPLICATES,
    MIN_PERIODS_FOR_FIT,
    FitParameters,
    PiecewiseFit,
    bootstrap_parameter_ranges,
    fit_change_point_baseline,
    predict,
)
from .metrics import (
    MONTHLY_G14_THRESHOLDS,
    cvrmse_pct,
    guideline14_pass,
    nmbe_pct,
)
from .report import CalibrationReport
from .weather import series_period_features

__all__ = ["ChangePointCalibrator"]


_METHOD = "change-point (PRISM-lineage) baseline regression with residual bootstrap"

#: Parameter names (relative to the fuel key) and their units.
_PARAMETER_UNITS: dict[str, str] = {
    "heating_change_point_c": "degC",
    "cooling_change_point_c": "degC",
    "heating_slope_kwh_per_kday": "kWh/(K*day)",
    "middle_slope_kwh_per_kday": "kWh/(K*day)",
    "cooling_slope_kwh_per_kday": "kWh/(K*day)",
    "load_at_heating_knot_kwh_per_day": "kWh/day",
}

#: The window reported when there is no billing period at all.
_EMPTY_WINDOW = TimeWindow(
    start_utc=datetime(1970, 1, 1, tzinfo=timezone.utc),
    end_utc=datetime(1970, 1, 2, tzinfo=timezone.utc),
)


class _Group:
    """One (fuel, meter kind) group of billing-period series."""

    def __init__(self, fuel: str, kind: str) -> None:
        self.fuel = fuel
        self.kind = kind
        self.series: list[UtilitySeries] = []
        self.disambiguated = False

    @property
    def name(self) -> str:
        """The parameter key prefix of this group."""
        return f"{self.fuel}_{self.kind}" if self.disambiguated else self.fuel


class _GroupData:
    """The per-period data of one group, as used by the fit and the metrics."""

    def __init__(self) -> None:
        self.observed: dict[tuple[int, int], float] = {}
        self.temperature: dict[tuple[int, int], float] = {}
        self.days: dict[tuple[int, int], int] = {}

    @property
    def keys(self) -> list[tuple[int, int]]:
        """The period keys, in chronological order."""
        return sorted(self.observed)


class ChangePointCalibrator:
    """Pure-core change-point baseline calibrator (monthly billing data).

    The class satisfies the ``home_energy_twin.protocols.Calibrator``
    Protocol: ``id``, ``version``, ``supported_granularities`` and
    ``calibrate(house, utility, weather)``.
    """

    id = "changepoint-baseline"
    version = "0.1"
    supported_granularities: set[Granularity] = {"billing_period"}

    def __init__(
        self,
        *,
        bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
        bootstrap_seed: int = 0,
    ) -> None:
        self._bootstrap_replicates = bootstrap_replicates
        self._bootstrap_seed = bootstrap_seed

    # -- the report ------------------------------------------------------

    def calibrate_report(
        self,
        house: House,
        utility: Sequence[UtilitySeries],
        weather: WeatherSeries,
    ) -> CalibrationReport:
        """Calibrate and return the full report (ranges, metrics, reasons)."""
        reasons: list[str] = []
        reasons.extend(self._weather_reasons(weather))
        reasons.extend(self._confounder_reasons(house))

        groups = self._group_series(utility, reasons)
        fitted: list[
            tuple[_Group, PiecewiseFit, _GroupData, dict[str, tuple[float, float]]]
        ] = []
        periods_used = 0

        for group in groups:
            data = self._group_data(group, weather)
            fit, ranges = self._fit_group(group, data, reasons)
            if fit is None:
                continue
            fitted.append((group, fit, data, ranges))
            periods_used = max(periods_used, fit.n_periods)

        parameters: dict[str, ParameterEstimate] = {}
        for group, fit, _, ranges in fitted:
            self._add_parameters(parameters, group, fit, ranges)

        metrics, metrics_by_fuel, metrics_basis, used_periods = self._metrics(
            fitted, groups, reasons
        )
        periods_used = max(periods_used, used_periods)

        window = self._calibration_window(groups)
        return CalibrationReport(
            house_id=house.house_id,
            calibrator_id=self.id,
            calibrator_version=self.version,
            calibration_window=window,
            data_granularity="billing_period",
            parameters=parameters,
            metrics=metrics,
            thresholds=MONTHLY_G14_THRESHOLDS,
            metrics_by_fuel=metrics_by_fuel,
            metrics_basis=metrics_basis,
            degradation_reasons=reasons,
            degraded=bool(reasons),
            method=_METHOD,
            periods_used=periods_used,
        )

    # -- the protocol ----------------------------------------------------

    def calibrate(
        self,
        house: House,
        utility: list[UtilitySeries],
        weather: WeatherSeries,
    ) -> CalibratedModel:
        """Calibrate a model, or degrade per LAW-3 (never raise for bad data).

        The returned :class:`~home_energy_twin.types.CalibratedModel` carries
        the parameter ranges, the Guideline 14 metrics and the calibrator
        identity.  A degraded run is marked ``stale`` so that it is not used
        as a baseline for retrofit ranking (LAW-4).
        """
        report = self.calibrate_report(house, utility, weather)
        return CalibratedModel(
            house_id=house.house_id,
            state_hash=house.state_hash,
            engine_ref=f"{self.id} {self.version}",
            hpxml_ref=house.hpxml_ref,
            parameters=dict(report.parameters),
            metrics=report.metrics,
            calibration_window=report.calibration_window,
            data_granularity="billing_period",
            calibrator_id=self.id,
            calibrator_version=self.version,
            status="stale" if report.degraded else "valid",
        )

    # -- helpers ---------------------------------------------------------

    def _weather_reasons(self, weather: WeatherSeries) -> list[str]:
        """LAW-3 / LAW-10 reasons coming from the weather series."""
        if not weather.points:
            return [
                "insufficient_data: the weather series is empty - no actual "
                "weather to regress the bills against (LAW-3, LAW-10)"
            ]
        if weather.source_kind in {"tmy", "forecast"}:
            return [
                f"law_10: weather source {weather.source_kind!r} is not valid for "
                "calibration - actual (observed/reanalysis) weather is required"
            ]
        return []

    def _confounder_reasons(self, house: House) -> list[str]:
        """LAW-3 reasons coming from the house's declared systems (RD-5)."""
        systems = house.systems
        reasons: list[str] = []
        if systems.dual_fuel:
            reasons.append(
                "confounded_configuration: dual-fuel heating/cooling is not "
                "separable from monthly billing data (LAW-3)"
            )
        if systems.has_pv or systems.has_battery or systems.has_ev_charging:
            flags = [
                name
                for name, present in (
                    ("PV", systems.has_pv),
                    ("battery", systems.has_battery),
                    ("EV charging", systems.has_ev_charging),
                )
                if present
            ]
            reasons.append(
                "confounded_configuration: on-site generation or storage "
                f"({', '.join(flags)}) degrades the confidence of a monthly "
                "baseline (LAW-3)"
            )
        if systems.secondary_unmetered_fuel:
            reasons.append(
                "confounded_configuration: an unmetered secondary fuel cannot "
                "be separated from the metered bills (LAW-3)"
            )
        if systems.aux_heat:
            reasons.append(
                "confounded_configuration: auxiliary heat usage is not visible "
                "in the bills and biases the heating slope (LAW-3)"
            )
        return reasons

    def _group_series(
        self,
        utility: Sequence[UtilitySeries],
        reasons: list[str],
    ) -> list[_Group]:
        """Group the utility series by ``(fuel, kind)`` and refuse bad kinds."""
        groups: dict[tuple[str, str], _Group] = {}
        for series in utility:
            if series.granularity != "billing_period":
                reasons.append(
                    "unsupported_data: the change-point baseline consumes monthly "
                    f"billing periods only; got {series.granularity!r} (LAW-3)"
                )
                continue
            if series.kind in {"export", "net"}:
                reasons.append(
                    f"unsupported_data: {series.kind!r} meter data is not "
                    "consumption and cannot calibrate a baseline (LAW-3)"
                )
                continue
            key = (series.fuel, series.kind)
            group = groups.get(key)
            if group is None:
                group = _Group(fuel=series.fuel, kind=series.kind)
                groups[key] = group
            group.series.append(series)

        fuel_counts: dict[str, int] = {}
        for group in groups.values():
            fuel_counts[group.fuel] = fuel_counts.get(group.fuel, 0) + 1
        for group in groups.values():
            group.disambiguated = fuel_counts.get(group.fuel, 0) > 1
        return list(groups.values())

    def _group_data(
        self,
        group: _Group,
        weather: WeatherSeries,
    ) -> _GroupData:
        """Collect the usable per-period data of one group."""
        data = _GroupData()
        for series in group.series:
            pairs, features = series_period_features(series, weather)
            for (key, energy), feature in zip(pairs, features):
                if not feature.has_weather:
                    continue
                data.observed[key] = data.observed.get(key, 0.0) + energy
                data.temperature[key] = feature.mean_temp_c
                data.days[key] = feature.days
        return data

    def _fit_group(
        self,
        group: _Group,
        data: _GroupData,
        reasons: list[str],
    ) -> tuple[Optional[PiecewiseFit], dict[str, tuple[float, float]]]:
        """Fit one group, adding a named reason when it cannot be fitted."""
        keys = data.keys
        if len(keys) < MIN_PERIODS_FOR_FIT:
            reasons.append(
                f"insufficient_data: {group.name} has {len(keys)} monthly billing "
                f"periods with usable weather, {MIN_PERIODS_FOR_FIT} required "
                "for a change-point fit (LAW-3)"
            )
            return None, {}

        xs = [data.temperature[key] for key in keys]
        ys = [data.observed[key] / data.days[key] for key in keys]
        fit = fit_change_point_baseline(xs, ys)
        if fit is None:
            reasons.append(
                "unidentifiable_configuration: the monthly bills of "
                f"{group.name} do not vary enough with temperature to identify "
                "the heating and cooling change-points (LAW-3)"
            )
            return None, {}

        self._identifiability_reasons(group, fit, xs, reasons)

        ranges = bootstrap_parameter_ranges(
            fit,
            xs,
            ys,
            replicates=self._bootstrap_replicates,
            seed=self._bootstrap_seed,
        )
        if not ranges:
            reasons.append(
                "uncertainty_unavailable: the residual bootstrap produced no "
                f"usable replicate for {group.name}; the reported interval is "
                "degenerate and the run is degraded (LAW-3)"
            )
        return fit, ranges

    @staticmethod
    def _identifiability_reasons(
        group: _Group,
        fit: PiecewiseFit,
        xs: Sequence[float],
        reasons: list[str],
    ) -> None:
        """Name the branches the observed months cannot identify (LAW-3).

        A change-point slope is only identified when the observed months
        actually reach past the change-point; otherwise the slope is an
        extrapolation and the run is degraded.
        """
        margin_c = 2.0
        lowest = min(xs)
        highest = max(xs)
        if lowest > fit.parameters.heating_knot_c + margin_c:
            reasons.append(
                "unidentifiable_configuration: the observed months of "
                f"{group.name} never reach below the heating change-point "
                f"({fit.parameters.heating_knot_c:.1f} degC, lowest month mean "
                f"{lowest:.1f} degC) - the heating slope is an extrapolation "
                "(LAW-3)"
            )
        if highest < fit.parameters.cooling_knot_c - margin_c:
            reasons.append(
                "unidentifiable_configuration: the observed months of "
                f"{group.name} never reach above the cooling change-point "
                f"({fit.parameters.cooling_knot_c:.1f} degC, highest month mean "
                f"{highest:.1f} degC) - the cooling slope is an extrapolation "
                "(LAW-3)"
            )

    def _add_parameters(
        self,
        parameters: dict[str, ParameterEstimate],
        group: _Group,
        fit: PiecewiseFit,
        ranges: dict[str, tuple[float, float]],
    ) -> None:
        """Add one fuel's parameter ranges to the report."""
        for name, value in self._point_values(fit.parameters).items():
            low, high = ranges.get(name, (value, value))
            low = min(low, value)
            high = max(high, value)
            method = (
                f"{_METHOD}; change-point search over monthly billing periods; "
                "95 % residual-bootstrap interval"
            )
            parameters[f"{group.name}.{name}"] = ParameterEstimate(
                value=value,
                low=low,
                high=high,
                unit=_PARAMETER_UNITS[name],
                method=method,
            )

    @staticmethod
    def _point_values(parameters: FitParameters) -> dict[str, float]:
        """The point fit, used only as the centre of the reported range."""
        return {
            "heating_change_point_c": parameters.heating_knot_c,
            "cooling_change_point_c": parameters.cooling_knot_c,
            "heating_slope_kwh_per_kday": parameters.heating_slope,
            "middle_slope_kwh_per_kday": parameters.middle_slope,
            "cooling_slope_kwh_per_kday": parameters.cooling_slope,
            "load_at_heating_knot_kwh_per_day": (
                parameters.load_at_heating_knot_kwh_per_day
            ),
        }

    def _metrics(
        self,
        fitted: Sequence[
            tuple[_Group, PiecewiseFit, _GroupData, dict[str, tuple[float, float]]]
        ],
        groups: Sequence[_Group],
        reasons: list[str],
    ) -> tuple[CalibrationMetrics, dict[str, CalibrationMetrics], str, int]:
        """Guideline 14 metrics for the calibration data (LAW-2)."""
        thresholds = MONTHLY_G14_THRESHOLDS
        metrics_by_fuel: dict[str, CalibrationMetrics] = {}

        observed_total: dict[tuple[int, int], float] = {}
        predicted_total: dict[tuple[int, int], float] = {}

        if fitted:
            common: Optional[set[tuple[int, int]]] = None
            for _, _, data, _ in fitted:
                periods = set(data.observed)
                common = periods if common is None else (common & periods)
            shared = sorted(common or set())
            for key in shared:
                observed_total[key] = 0.0
                predicted_total[key] = 0.0
                for group, fit, data, _ in fitted:
                    observed_total[key] += data.observed[key]
                    predicted_total[key] += (
                        predict(fit.parameters, data.temperature[key]) * data.days[key]
                    )
            for group, fit, data, _ in fitted:
                keys = data.keys
                residuals = [
                    data.observed[key]
                    - predict(fit.parameters, data.temperature[key]) * data.days[key]
                    for key in keys
                ]
                values = [data.observed[key] for key in keys]
                nmbe = nmbe_pct(residuals, values)
                cvrmse = cvrmse_pct(residuals, values, 6)
                metrics_by_fuel[group.name] = CalibrationMetrics(
                    nmbe_pct=nmbe,
                    cvrmse_pct=cvrmse,
                    granularity="billing_period",
                    guideline14_pass=guideline14_pass(nmbe, cvrmse, thresholds),
                )

        if observed_total:
            keys = sorted(observed_total)
            residuals = [observed_total[key] - predicted_total[key] for key in keys]
            values = [observed_total[key] for key in keys]
            nmbe = nmbe_pct(residuals, values)
            cvrmse = cvrmse_pct(residuals, values, 6 * len(fitted))
            metrics = CalibrationMetrics(
                nmbe_pct=nmbe,
                cvrmse_pct=cvrmse,
                granularity="billing_period",
                guideline14_pass=guideline14_pass(nmbe, cvrmse, thresholds),
            )
            return metrics, metrics_by_fuel, "change-point baseline fit", len(keys)

        # No fit was possible: report the metrics of the mean-only baseline so
        # that LAW-2 still holds, and say so in the report.
        residuals, values = self._mean_only_baseline(groups)
        if not values:
            metrics = CalibrationMetrics(
                nmbe_pct=0.0,
                cvrmse_pct=0.0,
                granularity="billing_period",
                guideline14_pass=False,
            )
            reasons.append(
                "insufficient_data: no monthly billing period could be used - "
                "no Guideline 14 metrics could be computed (LAW-2, LAW-3)"
            )
            return metrics, metrics_by_fuel, "no data - no baseline", 0
        nmbe = nmbe_pct(residuals, values)
        cvrmse = cvrmse_pct(residuals, values, 1)
        metrics = CalibrationMetrics(
            nmbe_pct=nmbe,
            cvrmse_pct=cvrmse,
            granularity="billing_period",
            guideline14_pass=guideline14_pass(nmbe, cvrmse, thresholds),
        )
        return (
            metrics,
            metrics_by_fuel,
            "mean-only baseline (no change-point fit was possible)",
            len(values),
        )

    @staticmethod
    def _mean_only_baseline(
        groups: Sequence[_Group],
    ) -> tuple[list[float], list[float]]:
        """Residuals and values of the mean-only (no fit) baseline."""
        totals: dict[tuple[int, int], float] = {}
        for group in groups:
            for series in group.series:
                for point in series.points:
                    key = (point.start_utc.year, point.start_utc.month)
                    totals[key] = totals.get(key, 0.0) + point.value
        values = [totals[key] for key in sorted(totals)]
        if not values:
            return [], []
        mean = sum(values) / len(values)
        return [value - mean for value in values], values

    @staticmethod
    def _calibration_window(groups: Sequence[_Group]) -> TimeWindow:
        """The window spanned by the billing periods that were considered."""
        starts: list[datetime] = []
        ends: list[datetime] = []
        for group in groups:
            for series in group.series:
                for point in series.points:
                    starts.append(point.start_utc)
                    ends.append(point.end_utc)
        if not starts:
            return _EMPTY_WINDOW
        return TimeWindow(start_utc=min(starts), end_utc=max(ends))
