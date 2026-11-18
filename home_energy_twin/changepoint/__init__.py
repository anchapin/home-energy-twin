"""Change-point baseline calibrator and calibration report (issue #10).

The pure-core, PRISM-lineage change-point calibration: a piecewise-linear
(PRISM-style balance-point) regression of monthly billing energy on the
period's mean outdoor temperature, with residual-bootstrap parameter ranges
(LAW-1), Guideline 14 metrics (LAW-2) and named degradation reasons when the
data cannot support a fit (LAW-3).

Nothing here needs an engine binary, a network connection or a device
(RD-7, LAW-6, LAW-7).  The calibrator is selected by configuration; core
code never imports it (AGENTS.md).

Typical use::

    from home_energy_twin.changepoint import ChangePointCalibrator

    calibrator = ChangePointCalibrator()
    report = calibrator.calibrate_report(house, utility_series, weather_series)
    print(report.to_text())
"""

from __future__ import annotations

from .calibrator import ChangePointCalibrator
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
    HOURLY_G14_THRESHOLDS,
    MONTHLY_G14_THRESHOLDS,
    Guideline14Thresholds,
    cvrmse_pct,
    guideline14_pass,
    nmbe_pct,
    thresholds_for_granularity,
)
from .report import CalibrationReport
from .weather import BALANCE_POINT_C, PeriodFeatures, daily_mean_temperatures

__all__ = [
    "BALANCE_POINT_C",
    "DEFAULT_BOOTSTRAP_REPLICATES",
    "HOURLY_G14_THRESHOLDS",
    "MIN_PERIODS_FOR_FIT",
    "MONTHLY_G14_THRESHOLDS",
    "CalibrationReport",
    "ChangePointCalibrator",
    "FitParameters",
    "Guideline14Thresholds",
    "PeriodFeatures",
    "PiecewiseFit",
    "bootstrap_parameter_ranges",
    "cvrmse_pct",
    "daily_mean_temperatures",
    "fit_change_point_baseline",
    "guideline14_pass",
    "nmbe_pct",
    "predict",
    "thresholds_for_granularity",
]
