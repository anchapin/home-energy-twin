"""Seeded generator for synthetic golden houses (SPEC §6).

The generator samples house archetypes (climate, vintage, size, systems) and
draws a full set of *known ground truth* parameters for each house:

* UA (W/K) — envelope conduction plus infiltration,
* capacitance (J/K),
* infiltration (ACH50),
* equipment capacity (kW) and efficiency (COP / AFUE),
* duct loss factor, base load, DHW draw, schedules.

Every draw comes from a seeded :class:`random.Random`, so the same
``(seed, archetype, index)`` always produces a byte-identical house
(SPEC §6 "Determinism").  No real-world data is used anywhere (LAW-11).
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from typing import Optional, Union

from ..types import House, HPXMLRef, LatLon, SystemInventory
from .types import Archetype, GoldenHouse, GroundTruth

__all__ = [
    "ARCHETYPES",
    "INFILTRATION_UA_PER_ACH_M2",
    "archetype_by_name",
    "generate_house",
    "generate_houses",
]

#: Infiltration UA (W/K) per m2 of floor area and per ACH50.
#:
#: ``0.33 Wh/(m3.K)`` (air) x ``2.7 m`` (ceiling height) x ``1/3600`` (h->s)
#: = ``0.89 W/(K.m2.ACH)``.
INFILTRATION_UA_PER_ACH_M2 = 0.89

ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        name="cold_climate_heat_pump",
        climate_zone="6",
        timezone="America/Chicago",
        latitude=44.9778,
        longitude=-93.2650,
        heating_fuels=["electricity"],
        cooling_fuels=["electricity"],
        dhw_fuels=["electricity"],
        floor_area_m2=(140.0, 220.0),
        vintage_year=(1990, 2010),
        envelope_ua_w_per_k=(60.0, 165.0),
        infiltration_ach50=(0.25, 0.70),
        capacitance_j_per_k=(2.0e6, 9.0e6),
        heating_efficiency=(2.2, 3.4),
        cooling_efficiency=(2.8, 3.8),
        heating_design_delta_t_k=(30.0, 38.0),
        cooling_design_delta_t_k=(11.0, 15.0),
        equipment_oversize=(1.05, 1.30),
        duct_loss_factor=(0.02, 0.18),
        base_load_w=(250.0, 700.0),
        dhw_litres_per_day=(90.0, 220.0),
        dhw_efficiency=(0.75, 0.95),
        dhw_delta_t_k=(35.0, 50.0),
        latent_ratio=(0.10, 0.25),
        annual_mean_c=6.0,
        seasonal_amplitude_c=16.0,
    ),
    Archetype(
        name="temperate_gas_furnace",
        climate_zone="5",
        timezone="America/Denver",
        latitude=39.7392,
        longitude=-104.9903,
        heating_fuels=["natural_gas"],
        cooling_fuels=["electricity"],
        dhw_fuels=["natural_gas"],
        floor_area_m2=(120.0, 200.0),
        vintage_year=(1955, 1990),
        envelope_ua_w_per_k=(90.0, 210.0),
        infiltration_ach50=(0.35, 0.90),
        capacitance_j_per_k=(2.5e6, 1.1e7),
        heating_efficiency=(0.65, 0.92),
        cooling_efficiency=(2.8, 3.6),
        heating_design_delta_t_k=(28.0, 36.0),
        cooling_design_delta_t_k=(11.0, 16.0),
        equipment_oversize=(1.05, 1.30),
        duct_loss_factor=(0.05, 0.25),
        base_load_w=(200.0, 600.0),
        dhw_litres_per_day=(100.0, 240.0),
        dhw_efficiency=(0.70, 0.92),
        dhw_delta_t_k=(35.0, 50.0),
        latent_ratio=(0.15, 0.30),
        annual_mean_c=10.0,
        seasonal_amplitude_c=14.0,
    ),
    Archetype(
        name="hot_humid_central_ac",
        climate_zone="2",
        timezone="America/Chicago",
        latitude=29.7604,
        longitude=-95.3698,
        heating_fuels=["natural_gas"],
        cooling_fuels=["electricity"],
        dhw_fuels=["natural_gas"],
        floor_area_m2=(130.0, 220.0),
        vintage_year=(1970, 2000),
        envelope_ua_w_per_k=(35.0, 110.0),
        infiltration_ach50=(0.50, 1.10),
        capacitance_j_per_k=(1.5e6, 8.0e6),
        heating_efficiency=(0.70, 0.95),
        cooling_efficiency=(2.8, 3.8),
        heating_design_delta_t_k=(24.0, 32.0),
        cooling_design_delta_t_k=(11.0, 16.0),
        equipment_oversize=(1.05, 1.30),
        duct_loss_factor=(0.02, 0.15),
        base_load_w=(300.0, 800.0),
        dhw_litres_per_day=(110.0, 260.0),
        dhw_efficiency=(0.70, 0.92),
        dhw_delta_t_k=(35.0, 50.0),
        latent_ratio=(0.25, 0.45),
        annual_mean_c=21.0,
        seasonal_amplitude_c=9.0,
    ),
    Archetype(
        name="mild_coastal_dual_fuel",
        climate_zone="3",
        timezone="America/Los_Angeles",
        latitude=37.7749,
        longitude=-122.4194,
        heating_fuels=["electricity", "natural_gas"],
        cooling_fuels=["electricity"],
        dhw_fuels=["electricity"],
        floor_area_m2=(110.0, 190.0),
        vintage_year=(1980, 2005),
        envelope_ua_w_per_k=(45.0, 120.0),
        infiltration_ach50=(0.35, 0.80),
        capacitance_j_per_k=(1.5e6, 7.0e6),
        heating_efficiency=(2.2, 3.4),
        cooling_efficiency=(3.0, 3.8),
        heating_design_delta_t_k=(24.0, 32.0),
        cooling_design_delta_t_k=(9.0, 13.0),
        equipment_oversize=(1.05, 1.30),
        duct_loss_factor=(0.02, 0.15),
        base_load_w=(200.0, 550.0),
        dhw_litres_per_day=(80.0, 200.0),
        dhw_efficiency=(0.75, 0.95),
        dhw_delta_t_k=(35.0, 50.0),
        latent_ratio=(0.05, 0.20),
        annual_mean_c=13.0,
        seasonal_amplitude_c=8.0,
        confounded=True,
    ),
    Archetype(
        name="solar_ev_home",
        climate_zone="4",
        timezone="America/Denver",
        latitude=40.7608,
        longitude=-111.8910,
        heating_fuels=["natural_gas"],
        cooling_fuels=["electricity"],
        dhw_fuels=["electricity"],
        floor_area_m2=(150.0, 240.0),
        vintage_year=(2000, 2020),
        envelope_ua_w_per_k=(40.0, 110.0),
        infiltration_ach50=(0.20, 0.50),
        capacitance_j_per_k=(1.5e6, 7.5e6),
        heating_efficiency=(0.70, 0.95),
        cooling_efficiency=(3.0, 4.0),
        heating_design_delta_t_k=(30.0, 38.0),
        cooling_design_delta_t_k=(11.0, 16.0),
        equipment_oversize=(1.05, 1.30),
        duct_loss_factor=(0.02, 0.12),
        base_load_w=(250.0, 700.0),
        dhw_litres_per_day=(100.0, 240.0),
        dhw_efficiency=(0.80, 0.98),
        dhw_delta_t_k=(35.0, 50.0),
        latent_ratio=(0.15, 0.35),
        annual_mean_c=9.0,
        seasonal_amplitude_c=15.0,
        confounded=True,
    ),
)

_BY_NAME = {archetype.name: archetype for archetype in ARCHETYPES}


def archetype_by_name(name: str) -> Archetype:
    """Look an archetype up by name."""
    try:
        return _BY_NAME[name]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(f"unknown archetype: {name!r}") from exc


def _draw(rng: random.Random, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return rng.uniform(low, high)


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _house_id(seed: int, index: int, archetype_name: str) -> uuid.UUID:
    digest = hashlib.sha256(f"golden-house:{seed}:{index}:{archetype_name}".encode()).digest()
    return uuid.UUID(bytes=digest[:16], version=4)


def generate_house(
    seed: int,
    archetype: Optional[Union[str, Archetype]] = None,
    index: int = 0,
) -> GoldenHouse:
    """Generate one synthetic house with known truth.

    ``archetype`` selects the archetype (by name or object); when omitted the
    archetype is taken from ``index`` so that :func:`generate_houses` cycles
    through the whole family deterministically.
    """
    if archetype is None:
        chosen = ARCHETYPES[index % len(ARCHETYPES)]
    elif isinstance(archetype, str):
        chosen = archetype_by_name(archetype)
    else:
        chosen = archetype

    rng = random.Random(f"golden-house:{seed}:{index}:{chosen.name}")

    floor_area = _draw(rng, chosen.floor_area_m2)
    vintage = rng.randint(chosen.vintage_year[0], chosen.vintage_year[1] - 1)
    envelope_ua = _draw(rng, chosen.envelope_ua_w_per_k)
    ach50 = _draw(rng, chosen.infiltration_ach50)
    infiltration_ua = INFILTRATION_UA_PER_ACH_M2 * ach50 * floor_area
    total_ua = envelope_ua + infiltration_ua

    # Equipment is sized from the house: nameplate capacity follows the
    # design temperature difference and an oversizing factor, so that a
    # monthly aggregate never runs the equipment into a fictional limit.
    oversize = _draw(rng, chosen.equipment_oversize)
    heating_capacity = total_ua * _draw(rng, chosen.heating_design_delta_t_k) / 1000.0 * oversize
    cooling_capacity = total_ua * _draw(rng, chosen.cooling_design_delta_t_k) / 1000.0 * oversize

    truth = GroundTruth(
        ua_w_per_k=total_ua,
        envelope_ua_w_per_k=envelope_ua,
        infiltration_ach50=ach50,
        infiltration_ua_w_per_k=infiltration_ua,
        capacitance_j_per_k=_draw(rng, chosen.capacitance_j_per_k),
        heating_capacity_kw=heating_capacity,
        heating_efficiency=_draw(rng, chosen.heating_efficiency),
        cooling_capacity_kw=cooling_capacity,
        cooling_efficiency=_draw(rng, chosen.cooling_efficiency),
        duct_loss_factor=_draw(rng, chosen.duct_loss_factor),
        base_load_w=_draw(rng, chosen.base_load_w),
        dhw_litres_per_day=_draw(rng, chosen.dhw_litres_per_day),
        dhw_efficiency=_draw(rng, chosen.dhw_efficiency),
        dhw_delta_t_k=_draw(rng, chosen.dhw_delta_t_k),
        latent_ratio=_draw(rng, chosen.latent_ratio),
        floor_area_m2=floor_area,
        vintage_year=vintage,
        climate_zone=chosen.climate_zone,
    )

    systems = SystemInventory(
        heating_fuels=list(chosen.heating_fuels),
        cooling_fuels=list(chosen.cooling_fuels),
        dhw_fuels=list(chosen.dhw_fuels),
        has_pv=chosen.name == "solar_ev_home",
        has_battery=chosen.name == "solar_ev_home",
        has_ev_charging=chosen.name == "solar_ev_home",
        dual_fuel=len(chosen.heating_fuels) > 1,
        aux_heat=chosen.name == "mild_coastal_dual_fuel",
        secondary_unmetered_fuel=False,
    )

    house_id = _house_id(seed, index, chosen.name)
    truth_json = json.dumps(truth.model_dump(), sort_keys=True)
    state_hash = _sha256_hex(f"{house_id}:{truth_json}")

    house = House(
        house_id=house_id,
        hpxml_ref=HPXMLRef(
            uri=f"synthetic://golden-house/{house_id}/hpxml",
            sha256=_sha256_hex(f"hpxml:{house_id}"),
            version="synthetic-1",
        ),
        timezone=chosen.timezone,
        location=LatLon(latitude=chosen.latitude, longitude=chosen.longitude, precision_m=1000.0),
        systems=systems,
        state_hash=state_hash,
    )

    return GoldenHouse(
        house=house,
        truth=truth,
        archetype=chosen.name,
        seed=seed,
        confounded=chosen.confounded,
    )


def generate_houses(seed: int, count: int) -> list[GoldenHouse]:
    """Generate ``count`` golden houses, cycling through the archetypes."""
    if count < 0:
        raise ValueError("count must not be negative")
    return [generate_house(seed, index=index) for index in range(count)]
