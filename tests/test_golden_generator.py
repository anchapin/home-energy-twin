"""Tests for the seeded golden-house generator (SPEC §6, LAW-11).

Everything here is synthetic: no real user data is involved (LAW-11) and no
engine binary is needed (RD-7).
"""

from __future__ import annotations

import json

import pytest

from home_energy_twin.golden import (
    ARCHETYPES,
    ForwardConfig,
    archetype_by_name,
    generate_house,
    generate_houses,
    monthly_energy,
    simulate_monthly,
    synthetic_weather,
)
from home_energy_twin.golden.generator import INFILTRATION_UA_PER_ACH_M2

SEEDS = (1, 7, 42)


def _weather_for(house, seed: int):
    return synthetic_weather(archetype_by_name(house.archetype), seed=seed)


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_same_seed_gives_byte_identical_houses(seed: int) -> None:
    first = generate_houses(seed, 6)
    second = generate_houses(seed, 6)
    payloads = [[house.model_dump_json() for house in batch] for batch in (first, second)]
    assert payloads[0] == payloads[1]
    assert payloads[0][0].encode("utf-8") == payloads[1][0].encode("utf-8")


@pytest.mark.parametrize("seed", SEEDS)
def test_same_seed_gives_byte_identical_series(seed: int) -> None:
    config = ForwardConfig(noise_pct=3.0, estimated_read_pct=10.0, gap_pct=5.0, seed=5)
    for index, house in enumerate(generate_houses(seed, 4)):
        weather = _weather_for(house, seed * 100 + index)
        first = [s.model_dump_json() for s in simulate_monthly(house, weather, config)]
        second = [s.model_dump_json() for s in simulate_monthly(house, weather, config)]
        assert first == second


def test_different_seeds_give_different_houses() -> None:
    first = generate_houses(11, 5)
    second = generate_houses(12, 5)
    assert [house.model_dump_json() for house in first] != [
        house.model_dump_json() for house in second
    ]
    assert [house.house.house_id for house in first] != [
        house.house.house_id for house in second
    ]


def test_house_ids_are_unique_within_a_batch() -> None:
    houses = generate_houses(3, 25)
    ids = [house.house.house_id for house in houses]
    assert len(set(ids)) == len(ids)


# --------------------------------------------------------------------------
# archetypes and truth invariants
# --------------------------------------------------------------------------


def test_at_least_three_archetype_variants_are_generated() -> None:
    houses = generate_houses(5, 15)
    names = {house.archetype for house in houses}
    assert len(names) >= 3
    assert len(ARCHETYPES) >= 3


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("index", range(5))
def test_truth_fields_are_positive_and_consistent(seed: int, index: int) -> None:
    house = generate_house(seed, index=index)
    truth = house.truth
    assert truth.ua_w_per_k > 0.0
    assert truth.envelope_ua_w_per_k > 0.0
    assert truth.infiltration_ach50 >= 0.0
    assert truth.capacitance_j_per_k > 0.0
    assert truth.heating_capacity_kw > 0.0
    assert truth.heating_efficiency > 0.0
    assert truth.cooling_capacity_kw > 0.0
    assert truth.cooling_efficiency > 0.0
    assert truth.base_load_w > 0.0
    assert truth.dhw_litres_per_day > 0.0
    assert truth.floor_area_m2 > 0.0
    assert 1800 < truth.vintage_year < 2100
    assert truth.ua_w_per_k == pytest.approx(
        truth.envelope_ua_w_per_k + truth.infiltration_ua_w_per_k
    )
    assert truth.infiltration_ua_w_per_k == pytest.approx(
        INFILTRATION_UA_PER_ACH_M2 * truth.infiltration_ach50 * truth.floor_area_m2
    )


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("index", range(5))
def test_truth_lies_inside_the_archetype_ranges(seed: int, index: int) -> None:
    house = generate_house(seed, index=index)
    archetype = archetype_by_name(house.archetype)
    truth = house.truth

    low, high = archetype.envelope_ua_w_per_k
    assert low <= truth.envelope_ua_w_per_k <= high
    low, high = archetype.infiltration_ach50
    assert low <= truth.infiltration_ach50 <= high
    low, high = archetype.capacitance_j_per_k
    assert low <= truth.capacitance_j_per_k <= high
    low, high = archetype.heating_efficiency
    assert low <= truth.heating_efficiency <= high
    low, high = archetype.cooling_efficiency
    assert low <= truth.cooling_efficiency <= high
    low, high = archetype.floor_area_m2
    assert low <= truth.floor_area_m2 <= high
    low, high = archetype.base_load_w
    assert low <= truth.base_load_w <= high
    low, high = archetype.dhw_litres_per_day
    assert low <= truth.dhw_litres_per_day <= high
    low, high = archetype.vintage_year
    assert low <= truth.vintage_year < high


def test_confounder_flags_exist_on_the_confounded_archetypes() -> None:
    houses = generate_houses(9, len(ARCHETYPES))
    confounded = [house for house in houses if house.confounded]
    assert confounded, "the generator must produce confounder variants (SPEC §6)"
    flags = {
        "has_pv": any(house.house.systems.has_pv for house in houses),
        "has_battery": any(house.house.systems.has_battery for house in houses),
        "has_ev_charging": any(house.house.systems.has_ev_charging for house in houses),
        "dual_fuel": any(house.house.systems.dual_fuel for house in houses),
        "aux_heat": any(house.house.systems.aux_heat for house in houses),
    }
    assert all(flags.values())


# --------------------------------------------------------------------------
# domain-type reuse
# --------------------------------------------------------------------------


def test_generated_house_uses_the_spec_domain_types() -> None:
    house = generate_house(2, index=0)
    payload = json.loads(house.house.model_dump_json())
    assert payload["systems"]["heating_fuels"]
    assert house.house.timezone
    assert len(house.house.hpxml_ref.sha256) == 64
    assert house.house.state_hash
    weather = synthetic_weather(archetype_by_name(house.archetype), seed=2)
    rows = monthly_energy(house, weather)
    assert rows, "the forward model must produce monthly energy"
    assert all(row.kwh >= 0.0 for row in rows)
