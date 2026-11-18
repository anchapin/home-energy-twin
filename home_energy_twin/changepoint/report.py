"""The calibration report surface of the change-point baseline.

The report is the *user-facing* outcome of a calibration run (docs/ROADMAP.md
Phase 1: "Calibration report: parameters with ranges, G14 metrics (monthly
targets NMBE ±5 %, CV(RMSE) ≤ 15 % — reported pass/fail, both facts shown),
degradation reasons (LAW-3)").

It is built on the domain types the rest of the core already uses
(:class:`~home_energy_twin.types.ParameterEstimate` and
:class:`~home_energy_twin.types.CalibrationMetrics`), so there is exactly one
report shape in the project rather than a parallel one.

Invariants carried by this surface:

* **LAW-1** — every parameter is a range; the rendered text never emits a
  point-only figure for a calibrated parameter (the centre value is only
  the midpoint of the reported interval, as SPEC §2.4 allows).
* **LAW-2** — the Guideline 14 metrics are always present, always with the
  thresholds they are judged against, and the pass/fail is reported either
  way.
* **LAW-3** — degradation reasons are named in the report; a degraded run is
  a *reported outcome*, never an exception and never a silent best-effort
  result.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..types import CalibrationMetrics, ParameterEstimate, TimeWindow
from .metrics import Guideline14Thresholds

__all__ = ["CalibrationReport"]


def _format_number(value: float) -> str:
    """Format a number for the rendered report (stable, locale-free)."""
    return f"{value:.3f}"


class CalibrationReport(BaseModel):
    """The report of one change-point baseline calibration run."""

    model_config = ConfigDict(extra="forbid")

    house_id: UUID
    calibrator_id: str = Field(min_length=1)
    calibrator_version: str = Field(min_length=1)
    calibration_window: TimeWindow
    data_granularity: str = Field(min_length=1)

    #: Calibrated parameters, each a *range* (LAW-1).
    parameters: dict[str, ParameterEstimate] = Field(default_factory=dict)

    #: Guideline 14 metrics of the calibration data (LAW-2).
    metrics: CalibrationMetrics

    #: The thresholds the metrics above are judged against (LAW-2).
    thresholds: Guideline14Thresholds

    #: Per-fuel Guideline 14 metrics, when more than one fuel was fitted.
    metrics_by_fuel: dict[str, CalibrationMetrics] = Field(default_factory=dict)

    #: What the metrics refer to (e.g. "change-point fit" or, when no fit
    #: was possible, "mean-only baseline").
    metrics_basis: str = Field(min_length=1)

    #: The LAW-3 degradation reasons, empty when the run was clean.
    degradation_reasons: list[str] = Field(default_factory=list)

    #: True when at least one degradation reason applies.
    degraded: bool = False

    #: The method recorded on every parameter (provenance of the numbers).
    method: str = Field(min_length=1)

    #: Number of billing periods that took part in the fit.
    periods_used: int = Field(ge=0)

    def degradation_reason_text(self) -> str:
        """The degradation reasons as one human-readable block."""
        if not self.degradation_reasons:
            return "  (none — the run met the calibrator's data requirements)"
        return "\n".join(f"  - {reason}" for reason in self.degradation_reasons)

    def parameter_lines(self) -> list[str]:
        """One rendered line per parameter — always a *range*, never a point."""
        lines: list[str] = []
        for name in sorted(self.parameters):
            estimate = self.parameters[name]
            lines.append(
                f"  {name}: {_format_number(estimate.low)} - "
                f"{_format_number(estimate.high)} {estimate.unit}"
            )
        return lines

    def to_text(self) -> str:
        """Render the report as plain text.

        The text is the artifact a homeowner reads; it carries the parameter
        ranges, the Guideline 14 metrics *with* their thresholds, and the
        degradation reasons.  It never prints a calibrated parameter as a
        bare number (LAW-1).
        """
        status = "DEGRADED" if self.degraded else "VALID"
        lines: list[str] = [
            "Calibration report - change-point baseline (pure core)",
            f"House: {self.house_id}",
            f"Calibrator: {self.calibrator_id} {self.calibrator_version}",
            (
                f"Window: {self.calibration_window.start_utc.isoformat()} .. "
                f"{self.calibration_window.end_utc.isoformat()}"
            ),
            f"Granularity: {self.data_granularity}",
            f"Billing periods used: {self.periods_used}",
            f"Status: {status}",
            "",
            "Parameters (ranges - no point-only figures, LAW-1)",
        ]
        lines.extend(self.parameter_lines())
        lines.extend(
            [
                "",
                (
                    "Guideline 14 metrics "
                    f"({self.thresholds.granularity} thresholds: "
                    f"{self.thresholds.describe()})"
                ),
                (
                    f"  NMBE: {_format_number(self.metrics.nmbe_pct)} % "
                    f"(threshold ±{_format_number(self.thresholds.nmbe_abs_pct)} %)"
                ),
                (
                    f"  CV(RMSE): {_format_number(self.metrics.cvrmse_pct)} % "
                    f"(threshold ≤{_format_number(self.thresholds.cvrmse_max_pct)} %)"
                ),
                f"  Guideline 14: {'PASS' if self.metrics.guideline14_pass else 'FAIL'}",
                f"  Metrics basis: {self.metrics_basis}",
                "",
                "Degradation reasons (LAW-3)",
                self.degradation_reason_text(),
                "",
                f"Method: {self.method}",
            ]
        )
        return "\n".join(lines)

    def parameter_ranges(self) -> dict[str, tuple[float, float]]:
        """The reported intervals, keyed by parameter name."""
        return {
            name: (estimate.low, estimate.high)
            for name, estimate in self.parameters.items()
        }

    def has_degradation_reason(self, needle: str) -> bool:
        """True when a degradation reason mentions ``needle``."""
        return any(needle in reason for reason in self.degradation_reasons)

    def optional_parameter(self, name: str) -> Optional[ParameterEstimate]:
        """One parameter estimate, if the run produced it."""
        return self.parameters.get(name)
