"""Onboarding: homeowner facts → HPXML document (SPEC §2.1, RD-12b).

The manual path is the product (RD-12b): a homeowner supplies the facts they
actually know — year built, floor area, fuels, systems — and the onboarding
builder turns those facts into an HPXML document describing the house.

Every fact the homeowner did *not* supply is filled from a **ResStock-style
prior** and is **flagged as a prior**, machine-visibly, in two places:

* in the returned metadata (:class:`PriorFact`, whose ``is_prior`` flag is a
  boolean field, not a comment), and
* inside the generated XML — as a ``prior="true"`` attribute on the affected
  element and as machine-readable ``tw:prior`` records in a namespaced
  ``tw:priors`` block.

The prior tables below are *style-consistent* defaults written for this repo
(RESstock-style vintage tables for envelope U-values, infiltration, etc.).  No
RESstock distribution data is vendored or copied here; the priors are our own
synthetic defaults, labelled as such in every emitted record.

This module invents no calibrated figures: it writes descriptive facts only,
so nothing it emits is a point estimate of a physical parameter (LAW-1).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal, Optional
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .golden.generator import INFILTRATION_UA_PER_ACH_M2
from .types import Fuel, House, HPXMLRef, LatLon, SystemInventory

__all__ = [
    "CEILING_HEIGHT_M_PRIOR",
    "COOLING_DESIGN_DELTA_T_K_BY_ZONE",
    "DESIGN_DELTA_T_K_BY_ZONE",
    "HPXML_VERSION",
    "INFILTRATION_ACH50_PRIOR",
    "PRIOR_SOURCE",
    "WALL_U_VALUE_BY_VINTAGE",
    "WINDOW_U_FACTOR_BY_VINTAGE",
    "FactBasis",
    "GeneratedHPXML",
    "HomeownerFacts",
    "PriorFact",
    "generate_hpxml",
]

#: Namespace of the generated HPXML document (HPXML 4.x).
HPXML_NS = "http://hpxmlonline.com"

#: Namespace used for the machine-readable prior records.
PRIOR_NS = "http://home-energy-twin.org/prior/1.0"

#: HPXML version written by the generator.
HPXML_VERSION = "4.0"

#: Label recorded on every prior-derived value.  It says "style" on purpose:
#: these defaults are our own, not a copy of the RESstock distribution data.
PRIOR_SOURCE = "resstock-style-priors-2024"

#: Provenance of a fact inside the generated document.
FactBasis = Literal["homeowner", "prior", "derived"]

#: Exterior wall U-value (W/(m2.K)) by year built — vintage table.
WALL_U_VALUE_BY_VINTAGE: tuple[tuple[int, float], ...] = (
    (1600, 0.90),
    (1940, 0.55),
    (1960, 0.36),
    (1980, 0.19),
    (1996, 0.11),
    (2010, 0.045),
)

#: Attic roof U-value (W/(m2.K)) by year built — vintage table.
ROOF_U_VALUE_BY_VINTAGE: tuple[tuple[int, float], ...] = (
    (1600, 0.55),
    (1940, 0.36),
    (1960, 0.28),
    (1980, 0.17),
    (1996, 0.11),
    (2010, 0.045),
)

#: Window U-factor (W/(m2.K)) by year built — vintage table.
WINDOW_U_FACTOR_BY_VINTAGE: tuple[tuple[int, float], ...] = (
    (1600, 2.60),
    (1980, 1.80),
    (1996, 1.30),
    (2010, 0.90),
)

#: Default envelope infiltration (ACH50) when the homeowner does not know it.
INFILTRATION_ACH50_PRIOR = 0.60

#: Default conditioned ceiling height (m).
CEILING_HEIGHT_M_PRIOR = 2.40

#: Default window-to-floor area ratio.
WINDOW_TO_FLOOR_RATIO_PRIOR = 0.15

#: Exterior wall area as a fraction of (floor area x stories).
WALL_AREA_FRACTION = 0.62

#: Roof (sloped) area as a fraction of (floor area x stories).
ROOF_AREA_FRACTION = 0.12

#: Heating design temperature difference (K) by RESstock climate zone.
DESIGN_DELTA_T_K_BY_ZONE: dict[str, float] = {
    "1": 24.0,
    "2": 26.0,
    "3": 28.0,
    "4": 30.0,
    "5": 32.0,
    "6": 34.0,
    "7": 36.0,
    "8": 18.0,
}

#: Cooling design temperature difference (K) by RESstock climate zone.
COOLING_DESIGN_DELTA_T_K_BY_ZONE: dict[str, float] = {
    "1": 18.0,
    "2": 20.0,
    "3": 22.0,
    "4": 24.0,
    "5": 26.0,
    "6": 12.0,
    "7": 11.0,
    "8": 10.0,
}

#: Fallback design temperature difference when the climate zone is unknown.
DEFAULT_DESIGN_DELTA_T_K = 32.0

#: Fallback cooling design temperature difference when the zone is unknown.
DEFAULT_COOLING_DESIGN_DELTA_T_K = 24.0


# --------------------------------------------------------------------------
# Input / output models
# --------------------------------------------------------------------------


class HomeownerFacts(BaseModel):
    """The facts a homeowner can supply without any utility account.

    Everything here is optional except the handful of fields needed to
    describe the house at all.  Unsupplied envelope values are filled from
    priors and flagged as priors (see :class:`PriorFact`).
    """

    model_config = ConfigDict(extra="forbid")

    house_id: Optional[uuid.UUID] = None
    timezone: str = Field(min_length=1)
    location: LatLon
    year_built: int = Field(ge=1600, le=2100)
    floor_area_m2: float = Field(gt=0.0, le=5000.0)
    stories: int = Field(default=1, ge=1, le=6)
    bedrooms: int = Field(default=3, ge=0, le=30)
    climate_zone: str = Field(default="5", min_length=1, max_length=3)
    building_type: str = Field(default="single_family_detached", min_length=1)

    heating_fuel: Fuel
    heating_system: str = Field(default="furnace", min_length=1)
    secondary_heating_fuel: Optional[Fuel] = None
    cooling_fuel: Optional[Fuel] = None
    cooling_system: Optional[str] = None
    dhw_fuel: Optional[Fuel] = None
    dhw_system: Optional[str] = None

    has_pv: bool = False
    has_battery: bool = False
    has_ev_charging: bool = False
    aux_heat: bool = False
    secondary_unmetered_fuel: bool = False

    # Envelope overrides — when given, these are homeowner facts, not priors.
    wall_u_value_w_m2_k: Optional[float] = Field(default=None, gt=0.0)
    roof_u_value_w_m2_k: Optional[float] = Field(default=None, gt=0.0)
    window_u_factor_w_m2_k: Optional[float] = Field(default=None, gt=0.0)
    infiltration_ach50: Optional[float] = Field(default=None, ge=0.0)
    ceiling_height_m: Optional[float] = Field(default=None, gt=0.0)

    # Equipment overrides.
    heating_capacity_kw: Optional[float] = Field(default=None, gt=0.0)
    cooling_capacity_kw: Optional[float] = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def _check_timezone(self) -> "HomeownerFacts":
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError(f"unknown IANA timezone: {self.timezone!r}") from exc
        return self


class PriorFact(BaseModel):
    """One fact in the generated document and where it came from.

    ``is_prior`` is a boolean field, so a consumer can filter priors without
    parsing prose (the machine-visible flag required by issue #9).
    """

    model_config = ConfigDict(extra="forbid")

    fact: str = Field(min_length=1)
    value: str = Field(min_length=1)
    unit: Optional[str] = None
    source: str = Field(min_length=1)
    basis: FactBasis
    is_prior: bool
    note: str = ""


class GeneratedHPXML(BaseModel):
    """The result of an onboarding run."""

    model_config = ConfigDict(extra="forbid")

    hpxml: str = Field(min_length=1)
    hpxml_ref: HPXMLRef
    house: House
    priors: list[PriorFact] = Field(default_factory=list)

    @property
    def sha256(self) -> str:
        """SHA-256 of the generated HPXML content (same digest as the ref)."""
        return self.hpxml_ref.sha256

    def prior_facts(self) -> list[str]:
        """The facts whose values came from a prior rather than the homeowner."""
        return [record.fact for record in self.priors if record.is_prior]


# --------------------------------------------------------------------------
# Prior tables
# --------------------------------------------------------------------------


def _vintage_value(
    table: tuple[tuple[int, float], ...],
    year_built: int,
) -> float:
    """The vintage-table value that applies to ``year_built``."""
    value = table[0][1]
    for start_year, candidate in table:
        if year_built >= start_year:
            value = candidate
        else:
            break
    return value


class _Ledger:
    """Records where each fact in the generated document came from."""

    def __init__(self) -> None:
        self.records: list[PriorFact] = []

    def homeowner(self, fact: str, value: object, unit: Optional[str] = None) -> None:
        self.records.append(
            PriorFact(
                fact=fact,
                value=_render(value),
                unit=unit,
                source="homeowner",
                basis="homeowner",
                is_prior=False,
                note="supplied by the homeowner",
            )
        )

    def prior(
        self,
        fact: str,
        value: object,
        unit: Optional[str],
        note: str,
        source: str = PRIOR_SOURCE,
    ) -> None:
        self.records.append(
            PriorFact(
                fact=fact,
                value=_render(value),
                unit=unit,
                source=source,
                basis="prior",
                is_prior=True,
                note=note,
            )
        )

    def derived(
        self,
        fact: str,
        value: object,
        unit: Optional[str],
        note: str,
        source: str = PRIOR_SOURCE,
    ) -> None:
        self.records.append(
            PriorFact(
                fact=fact,
                value=_render(value),
                unit=unit,
                source=source,
                basis="derived",
                is_prior=True,
                note=note,
            )
        )


def _render(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


# --------------------------------------------------------------------------
# XML helpers
# --------------------------------------------------------------------------


def _sub(
    parent: ET.Element,
    tag: str,
    text: Optional[str] = None,
    *,
    prior: bool = False,
    prior_source: Optional[str] = None,
) -> ET.Element:
    """Create a child element, optionally flagged as prior-derived."""
    element = ET.SubElement(parent, tag)
    if prior:
        element.set("prior", "true")
        if prior_source is not None:
            element.set("prior-source", prior_source)
    if text is not None:
        element.text = text
    return element


def _prior_element(
    parent: ET.Element,
    tag: str,
    value: float,
    *,
    source: str,
) -> ET.Element:
    """A numeric element whose value came from a prior (flagged in-place)."""
    return _sub(
        parent,
        tag,
        _render(value),
        prior=True,
        prior_source=source,
    )


# --------------------------------------------------------------------------
# The generator
# --------------------------------------------------------------------------


def _house_id(facts: HomeownerFacts) -> uuid.UUID:
    if facts.house_id is not None:
        return facts.house_id
    key = (
        f"home-energy-twin:house:{facts.location.latitude:.6f}:"
        f"{facts.location.longitude:.6f}:{facts.year_built}:{facts.floor_area_m2:.3f}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, key)


def _systems(facts: HomeownerFacts) -> SystemInventory:
    heating_fuels: list[Fuel] = [facts.heating_fuel]
    if facts.secondary_heating_fuel is not None:
        heating_fuels.append(facts.secondary_heating_fuel)
    cooling_fuels: list[Fuel] = []
    if facts.cooling_fuel is not None and facts.cooling_system is not None:
        cooling_fuels.append(facts.cooling_fuel)
    dhw_fuels: list[Fuel] = []
    if facts.dhw_fuel is not None and facts.dhw_system is not None:
        dhw_fuels.append(facts.dhw_fuel)
    return SystemInventory(
        heating_fuels=heating_fuels,
        cooling_fuels=cooling_fuels,
        dhw_fuels=dhw_fuels,
        has_pv=facts.has_pv,
        has_battery=facts.has_battery,
        has_ev_charging=facts.has_ev_charging,
        dual_fuel=len(heating_fuels) > 1,
        aux_heat=facts.aux_heat,
        secondary_unmetered_fuel=facts.secondary_unmetered_fuel,
    )


def _build_document(
    facts: HomeownerFacts,
    ledger: _Ledger,
) -> ET.Element:
    """Build the HPXML element tree for ``facts``."""
    floor_area = facts.floor_area_m2
    gross_area = floor_area * facts.stories

    wall_area = gross_area * WALL_AREA_FRACTION
    roof_area = gross_area * ROOF_AREA_FRACTION
    window_area = gross_area * WINDOW_TO_FLOOR_RATIO_PRIOR

    if facts.wall_u_value_w_m2_k is None:
        wall_u = _vintage_value(WALL_U_VALUE_BY_VINTAGE, facts.year_built)
        ledger.prior(
            "enclosure/walls/u_value",
            wall_u,
            "W/m2K",
            "vintage table prior (year built)",
        )
    else:
        wall_u = facts.wall_u_value_w_m2_k
        ledger.homeowner("enclosure/walls/u_value", wall_u, "W/m2K")

    if facts.roof_u_value_w_m2_k is None:
        roof_u = _vintage_value(ROOF_U_VALUE_BY_VINTAGE, facts.year_built)
        ledger.prior(
            "enclosure/roof/u_value",
            roof_u,
            "W/m2K",
            "vintage table prior (year built)",
        )
    else:
        roof_u = facts.roof_u_value_w_m2_k
        ledger.homeowner("enclosure/roof/u_value", roof_u, "W/m2K")

    if facts.window_u_factor_w_m2_k is None:
        window_u = _vintage_value(WINDOW_U_FACTOR_BY_VINTAGE, facts.year_built)
        ledger.prior(
            "enclosure/windows/u_factor",
            window_u,
            "W/m2K",
            "vintage table prior (year built)",
        )
    else:
        window_u = facts.window_u_factor_w_m2_k
        ledger.homeowner("enclosure/windows/u_factor", window_u, "W/m2K")

    if facts.infiltration_ach50 is None:
        ach50 = INFILTRATION_ACH50_PRIOR
        ledger.prior(
            "enclosure/infiltration/ach50",
            ach50,
            "1/h",
            "default infiltration prior",
        )
    else:
        ach50 = facts.infiltration_ach50
        ledger.homeowner("enclosure/infiltration/ach50", ach50, "1/h")

    if facts.ceiling_height_m is None:
        ceiling_height = CEILING_HEIGHT_M_PRIOR
        ledger.prior(
            "building/ceiling_height",
            ceiling_height,
            "m",
            "default ceiling height prior",
        )
    else:
        ceiling_height = facts.ceiling_height_m
        ledger.homeowner("building/ceiling_height", ceiling_height, "m")

    ledger.prior(
        "building/wall_area",
        wall_area,
        "m2",
        "derived from floor area and stories",
    )
    ledger.prior(
        "building/roof_area",
        roof_area,
        "m2",
        "derived from floor area and stories",
    )
    ledger.prior(
        "building/window_area",
        window_area,
        "m2",
        "derived from floor area and stories (window-to-floor ratio prior)",
    )

    envelope_ua = (
        wall_area * wall_u + roof_area * roof_u + window_area * window_u
    )
    infiltration_ua = INFILTRATION_UA_PER_ACH_M2 * gross_area * ach50
    total_ua = envelope_ua + infiltration_ua
    ledger.derived(
        "enclosure/ua_total",
        total_ua,
        "W/K",
        "derived from envelope and infiltration values (priors unless stated)",
    )

    design_delta_t = DESIGN_DELTA_T_K_BY_ZONE.get(
        facts.climate_zone, DEFAULT_DESIGN_DELTA_T_K
    )
    cooling_delta_t = COOLING_DESIGN_DELTA_T_K_BY_ZONE.get(
        facts.climate_zone, DEFAULT_COOLING_DESIGN_DELTA_T_K
    )
    ledger.prior(
        "climate/design_delta_t",
        design_delta_t,
        "K",
        "climate-zone prior",
    )
    ledger.prior(
        "climate/cooling_design_delta_t",
        cooling_delta_t,
        "K",
        "climate-zone prior",
    )

    if facts.heating_capacity_kw is None:
        heating_capacity_kw = total_ua * design_delta_t / 1000.0
        ledger.derived(
            "systems/heating/capacity",
            heating_capacity_kw,
            "kW",
            "derived from UA and climate design delta T (both prior-based)",
        )
    else:
        heating_capacity_kw = facts.heating_capacity_kw
        ledger.homeowner("systems/heating/capacity", heating_capacity_kw, "kW")

    cooling_capacity_kw: Optional[float] = None
    if facts.cooling_system is not None and facts.cooling_fuel is not None:
        if facts.cooling_capacity_kw is None:
            cooling_capacity_kw = total_ua * cooling_delta_t / 1000.0
            ledger.derived(
                "systems/cooling/capacity",
                cooling_capacity_kw,
                "kW",
                "derived from UA and climate design delta T (both prior-based)",
            )
        else:
            cooling_capacity_kw = facts.cooling_capacity_kw
            ledger.homeowner("systems/cooling/capacity", cooling_capacity_kw, "kW")

    ledger.homeowner("building/year_built", facts.year_built)
    ledger.homeowner("building/floor_area", floor_area, "m2")
    ledger.homeowner("building/stories", facts.stories)
    ledger.homeowner("building/bedrooms", facts.bedrooms)
    ledger.homeowner("building/building_type", facts.building_type)
    ledger.homeowner("location/latitude", facts.location.latitude)
    ledger.homeowner("location/longitude", facts.location.longitude)
    ledger.homeowner("climate/zone", facts.climate_zone)
    ledger.homeowner("systems/heating/fuel", facts.heating_fuel)
    ledger.homeowner("systems/heating/type", facts.heating_system)

    root = ET.Element(f"{{{HPXML_NS}}}HPXML")
    root.set("id", str(_house_id(facts)))

    version = _sub(root, "HPXMLVersion")
    _sub(version, "major", "4")
    _sub(version, "minor", "0")

    priors_element = ET.SubElement(root, f"{{{PRIOR_NS}}}priors")
    for record in ledger.records:
        prior_element = ET.SubElement(priors_element, f"{{{PRIOR_NS}}}prior")
        prior_element.set("fact", record.fact)
        prior_element.set("value", record.value)
        if record.unit is not None:
            prior_element.set("unit", record.unit)
        prior_element.set("source", record.source)
        prior_element.set("basis", record.basis)
        prior_element.set("is-prior", "true" if record.is_prior else "false")

    building = _sub(root, "Building")
    _sub(building, "ID", str(_house_id(facts)))

    details = _sub(building, "BuildingDetails")
    _sub(details, "BuildingType", facts.building_type)
    _sub(details, "YearBuilt", str(facts.year_built))
    _sub(details, "NumberOfConditionedFloors", str(facts.stories))
    _sub(details, "NumberOfBedrooms", str(facts.bedrooms))
    if facts.ceiling_height_m is None:
        _prior_element(details, "CeilingHeight", ceiling_height, source=PRIOR_SOURCE)
    else:
        _sub(details, "CeilingHeight", _render(ceiling_height))
    _sub(details, "FloorArea", _render(floor_area))

    climate = _sub(building, "Climate")
    _sub(climate, "Zone", facts.climate_zone)
    _sub(climate, "Latitude", _render(facts.location.latitude))
    _sub(climate, "Longitude", _render(facts.location.longitude))

    enclosure = _sub(building, "Enclosure")

    wall = _sub(enclosure, "Wall")
    wall.set("id", "Wall1")
    _sub(wall, "Location", "exterior")
    _sub(wall, "Area", _render(wall_area))
    if facts.wall_u_value_w_m2_k is None:
        _prior_element(wall, "UValue", wall_u, source=PRIOR_SOURCE)
    else:
        _sub(wall, "UValue", _render(wall_u))

    roof = _sub(enclosure, "Roof")
    roof.set("id", "Roof1")
    _sub(roof, "Location", "roof")
    _sub(roof, "Area", _render(roof_area))
    if facts.roof_u_value_w_m2_k is None:
        _prior_element(roof, "UValue", roof_u, source=PRIOR_SOURCE)
    else:
        _sub(roof, "UValue", _render(roof_u))

    windows = _sub(enclosure, "Windows")
    window = _sub(windows, "Window")
    window.set("id", "Window1")
    _sub(window, "Area", _render(window_area))
    if facts.window_u_factor_w_m2_k is None:
        _prior_element(window, "UFactor", window_u, source=PRIOR_SOURCE)
    else:
        _sub(window, "UFactor", _render(window_u))

    infiltration = _sub(enclosure, "Infiltration")
    infiltration.set("id", "Infiltration1")
    if facts.infiltration_ach50 is None:
        _prior_element(infiltration, "AirChangesPerHour", ach50, source=PRIOR_SOURCE)
    else:
        _sub(infiltration, "AirChangesPerHour", _render(ach50))

    systems = _sub(building, "Systems")

    heating = _sub(systems, "HVACSystem")
    heating.set("id", "HeatingSystem1")
    heating.set("type", "heating")
    heating_equipment = _sub(heating, "HeatingEquipment")
    _sub(heating_equipment, "HeatingType", facts.heating_system)
    _sub(heating_equipment, "HeatingFuel", facts.heating_fuel)
    if facts.heating_capacity_kw is None:
        _prior_element(
            heating_equipment, "HeatingCapacity", heating_capacity_kw, source=PRIOR_SOURCE
        )
    else:
        _sub(heating_equipment, "HeatingCapacity", _render(heating_capacity_kw))
    if facts.secondary_heating_fuel is not None:
        secondary = _sub(heating_equipment, "SecondaryHeatingEquipment")
        _sub(secondary, "HeatingFuel", facts.secondary_heating_fuel)

    if facts.cooling_system is not None and facts.cooling_fuel is not None:
        cooling = _sub(systems, "HVACSystem")
        cooling.set("id", "CoolingSystem1")
        cooling.set("type", "cooling")
        cooling_equipment = _sub(cooling, "CoolingEquipment")
        _sub(cooling_equipment, "CoolingType", facts.cooling_system)
        _sub(cooling_equipment, "CoolingFuel", facts.cooling_fuel)
        if cooling_capacity_kw is not None:
            if facts.cooling_capacity_kw is None:
                _prior_element(
                    cooling_equipment,
                    "CoolingCapacity",
                    cooling_capacity_kw,
                    source=PRIOR_SOURCE,
                )
            else:
                _sub(cooling_equipment, "CoolingCapacity", _render(cooling_capacity_kw))

    if facts.dhw_system is not None and facts.dhw_fuel is not None:
        water_heating = _sub(systems, "WaterHeatingSystem")
        water_heating.set("id", "WaterHeatingSystem1")
        _sub(water_heating, "WaterHeatingType", facts.dhw_system)
        _sub(water_heating, "WaterHeatingFuel", facts.dhw_fuel)

    return root


def generate_hpxml(facts: HomeownerFacts) -> GeneratedHPXML:
    """Generate an HPXML document (and a matching :class:`House`) from facts.

    The returned document is well-formed XML.  Every value that came from a
    prior rather than from the homeowner is flagged, both in
    :attr:`GeneratedHPXML.priors` and inside the XML itself.  The
    :class:`HPXMLRef` carries the SHA-256 of the generated content.
    """
    ledger = _Ledger()
    root = _build_document(facts, ledger)

    ET.register_namespace("", HPXML_NS)
    ET.register_namespace("tw", PRIOR_NS)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    hpxml = ET.tostring(root, encoding="unicode", xml_declaration=True)

    digest = hashlib.sha256(hpxml.encode("utf-8")).hexdigest()
    house_id = _house_id(facts)
    systems = _systems(facts)
    inventory_json = json.dumps(systems.model_dump(mode="json"), sort_keys=True)
    state_hash = hashlib.sha256(f"{digest}:{inventory_json}".encode("utf-8")).hexdigest()

    house = House(
        house_id=house_id,
        hpxml_ref=HPXMLRef(
            uri=f"onboarding://hpxml/{house_id}.xml",
            sha256=digest,
            version=HPXML_VERSION,
        ),
        timezone=facts.timezone,
        location=facts.location,
        systems=systems,
        state_hash=state_hash,
    )

    return GeneratedHPXML(
        hpxml=hpxml,
        hpxml_ref=house.hpxml_ref,
        house=house,
        priors=ledger.records,
    )
