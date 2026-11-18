"""Monthly recovery tests for the golden-house harness (SPEC §6).

These are the Phase 1 gate inputs of SPEC §6 (monthly granularity):

* weather-normalised annual consumption (NAC) within +/-10 % of the truth on
  >= 90 % of the generated houses, and
* the recovered UA is reported as a *range* that covers the truth on
  >= 90 % of the generated houses (range honesty is the monthly bar, not
  point accuracy).

The numbers asserted here are the SPEC's numbers; they are never weakened.
If the harness could not meet them, the measured rate would be recorded
here instead — it is not silently relaxed.
"""

from __future__ import annotations

import pytest

from home_energy_twin.golden import (
    ForwardConfig,
    archetype_by_name,
    generate_houses,
    monthly_energy,
    recover_monthly,
    simulate_monthly,
    synthetic_weather,
)
from home_energy_twin.golden.forward import annual_truth_kwh

#: The pre-registered monthly bars of SPEC §6.
NAC_TOLERANCE = 0.10
NAC_MIN_RATE = 0.90
UA_MIN_RATE = 0.90

#: The house set the bar is measured on.
SEED = 7
HOUSE_COUNT = 24
NOISE_PCT = 2.0


def _case(index: int, house):
    weather = synthetic_weather(
        archetype_by_name(house.archetype), seed=SEED * 100 + index
    )
    truth_rows = monthly_energy(house, weather)
    truth_nac = annual_truth_kwh(truth_rows)
    series = simulate_monthly(
        house, weather, ForwardConfig(noise_pct=NOISE_PCT, seed=index)
    )
    recovery = recover_monthly(house, series, weather)
    return truth_nac, recovery


def _measure() -> tuple[int, int, int]:
    nac_ok = 0
    ua_covered = 0
    total = 0
    for index, house in enumerate(generate_houses(SEED, HOUSE_COUNT)):
        truth_nac, recovery = _case(index, house)
        assert recovery.nac_kwh is not None
        error = abs(recovery.nac_kwh.value - truth_nac) / truth_nac
        if error <= NAC_TOLERANCE:
            nac_ok += 1
        estimate = recovery.ua_w_per_k
        if (
            estimate is not None
            and estimate.low <= house.truth.ua_w_per_k <= estimate.high
        ):
            ua_covered += 1
        total += 1
    return nac_ok, ua_covered, total


def test_monthly_nac_is_within_ten_percent_on_at_least_ninety_percent() -> None:
    nac_ok, _, total = _measure()
    rate = nac_ok / total
    print(
        f"NAC within +/-10%: {nac_ok}/{total} ({rate:.1%}), "
        f"seed={SEED}, houses={HOUSE_COUNT}, noise={NOISE_PCT}%"
    )
    assert total >= 20
    assert rate >= NAC_MIN_RATE


def test_monthly_ua_range_covers_truth_on_at_least_ninety_percent() -> None:
    _, ua_covered, total = _measure()
    rate = ua_covered / total
    print(
        f"UA range covers truth: {ua_covered}/{total} ({rate:.1%}), "
        f"seed={SEED}, houses={HOUSE_COUNT}, noise={NOISE_PCT}%"
    )
    assert total >= 20
    assert rate >= UA_MIN_RATE


# --------------------------------------------------------------------------
# honesty of the reported recovery (LAW-1, LAW-3)
# --------------------------------------------------------------------------


def test_recovered_values_are_ranges_not_points() -> None:
    for index, house in enumerate(generate_houses(SEED, 8)):
        _, recovery = _case(index, house)
        assert recovery.nac_kwh is not None
        assert recovery.nac_kwh.low <= recovery.nac_kwh.value <= recovery.nac_kwh.high
        assert recovery.nac_kwh.unit
        assert recovery.nac_kwh.method
        estimate = recovery.ua_w_per_k
        assert estimate is not None
        assert estimate.low <= estimate.value <= estimate.high
        assert estimate.unit == "W/K"
        assert estimate.method


def test_confounded_houses_are_reported_as_degraded() -> None:
    houses = generate_houses(SEED, 24)
    confounded = [house for house in houses if house.confounded]
    assert confounded
    for index, house in enumerate(houses):
        if not house.confounded:
            continue
        _, recovery = _case(index, house)
        assert recovery.degraded
        assert recovery.degradation_reason
        assert "LAW-3" in recovery.degradation_reason


def test_too_few_months_degrade_instead_of_guessing() -> None:
    house = generate_houses(SEED, 1)[0]
    weather = synthetic_weather(archetype_by_name(house.archetype), seed=1)
    series = simulate_monthly(house, weather)
    truncated = [
        one.model_copy(update={"points": one.points[:3]}) for one in series
    ]
    recovery = recover_monthly(house, truncated, weather)
    assert recovery.degraded
    assert recovery.degradation_reason is not None
    assert "insufficient_data" in recovery.degradation_reason
    assert recovery.nac_kwh is None
    assert recovery.ua_w_per_k is None


@pytest.mark.parametrize("seed", [3, 11, 23])
def test_the_bars_hold_across_seeds(seed: int) -> None:
    nac_ok = 0
    ua_covered = 0
    houses = generate_houses(seed, 20)
    for index, house in enumerate(houses):
        truth_nac, recovery = _case(index, house)
        assert recovery.nac_kwh is not None
        if abs(recovery.nac_kwh.value - truth_nac) / truth_nac <= NAC_TOLERANCE:
            nac_ok += 1
        estimate = recovery.ua_w_per_k
        if (
            estimate is not None
            and estimate.low <= house.truth.ua_w_per_k <= estimate.high
        ):
            ua_covered += 1
    print(
        f"seed={seed}: NAC {nac_ok}/{len(houses)}, UA {ua_covered}/{len(houses)}"
    )
    assert nac_ok / len(houses) >= NAC_MIN_RATE
    assert ua_covered / len(houses) >= UA_MIN_RATE
