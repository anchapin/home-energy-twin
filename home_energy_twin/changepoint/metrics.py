"""Guideline 14 metrics for the change-point baseline (SPEC §2.4, LAW-2).

Guideline 14 (ASHRAE Guideline 14 / Fels et al.) is a **reporting floor**,
not a truth claim: the numbers are always stored with every calibration and
are always reported next to the parameter ranges, whichever way they fall
(docs/PLAN.md §5.2).

Reference thresholds (AGENTS.md, SPEC §2.4):

* monthly billing periods — NMBE within ±5 %, CV(RMSE) ≤ 15 %
* hourly — NMBE within ±10 %, CV(RMSE) ≤ 30 %

BPI-2400 acceptance numbers are a *different* standard and are never mixed
into these (SPEC §2.4).

Definitions used here (monthly aggregation, the standard G14 form):

* ``NMBE %``  = 100 · Σ(residual) / Σ(observed)
* ``CV(RMSE) %`` = 100 · sqrt( Σ(residual²) / (n − p) ) / mean(observed)

with ``p`` the number of estimated model parameters (coefficients plus
change-points).  Residuals are ``observed − predicted``.
"""

from __future__ import annotations

import math
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "HOURLY_G14_THRESHOLDS",
    "MONTHLY_G14_THRESHOLDS",
    "Guideline14Thresholds",
    "cvrmse_pct",
    "guideline14_pass",
    "nmbe_pct",
    "thresholds_for_granularity",
]


class Guideline14Thresholds(BaseModel):
    """The Guideline 14 acceptance thresholds a metric set is judged against.

    The thresholds are carried *with* the metrics so a report never leaves
    the reader to remember which number applies (LAW-2).
    """

    model_config = ConfigDict(extra="forbid")

    granularity: str
    nmbe_abs_pct: float = Field(gt=0.0)
    cvrmse_max_pct: float = Field(gt=0.0)

    def describe(self) -> str:
        """One-line human description of the thresholds."""
        return (
            f"NMBE within ±{self.nmbe_abs_pct:g} %, "
            f"CV(RMSE) ≤ {self.cvrmse_max_pct:g} %"
        )


MONTHLY_G14_THRESHOLDS = Guideline14Thresholds(
    granularity="billing_period",
    nmbe_abs_pct=5.0,
    cvrmse_max_pct=15.0,
)

HOURLY_G14_THRESHOLDS = Guideline14Thresholds(
    granularity="hourly",
    nmbe_abs_pct=10.0,
    cvrmse_max_pct=30.0,
)


def thresholds_for_granularity(granularity: str) -> Guideline14Thresholds:
    """The G14 thresholds that apply to ``granularity``."""
    if granularity == "hourly":
        return HOURLY_G14_THRESHOLDS
    return MONTHLY_G14_THRESHOLDS


def nmbe_pct(residuals: Sequence[float], observed: Sequence[float]) -> float:
    """Normalised mean bias error in percent (observed − predicted residuals)."""
    if len(residuals) != len(observed):
        raise ValueError("residuals and observed must have the same length")
    total_observed = math.fsum(observed)
    if total_observed == 0.0:
        return 0.0
    return 100.0 * math.fsum(residuals) / total_observed


def cvrmse_pct(
    residuals: Sequence[float],
    observed: Sequence[float],
    n_parameters: int,
) -> float:
    """Coefficient of variation of the RMSE in percent (G14 form)."""
    if len(residuals) != len(observed):
        raise ValueError("residuals and observed must have the same length")
    count = len(residuals)
    if count == 0:
        return 0.0
    degrees_of_freedom = count - n_parameters
    if degrees_of_freedom <= 0:
        degrees_of_freedom = 1
    mean_observed = math.fsum(observed) / count
    if mean_observed == 0.0:
        return 0.0
    rmse = math.sqrt(math.fsum(r * r for r in residuals) / degrees_of_freedom)
    return 100.0 * rmse / mean_observed


def guideline14_pass(
    nmbe: float,
    cvrmse: float,
    thresholds: Guideline14Thresholds,
) -> bool:
    """True when both G14 criteria hold for the given thresholds."""
    return abs(nmbe) <= thresholds.nmbe_abs_pct and cvrmse <= thresholds.cvrmse_max_pct
