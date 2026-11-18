"""Tests for the onboarding builder: homeowner facts → HPXML (issue #9).

Covered:

* the generated document is well-formed XML and carries the declared
  envelope/system facts,
* every value that came from a prior (rather than from the homeowner) is
  flagged as a prior, machine-visibly, in the metadata *and* in the XML,
* the returned ``HPXMLRef`` carries the SHA-256 of the generated content,
* the house's ``state_hash`` follows the physical description.
"""

from __future__ import annotations

import hashlib
from xml.etree import ElementTree as ET

from home_energy_twin.onboarding import (
    PRIOR_NS,
    PRIOR_SOURCE,
    HomeownerFacts,
    generate_hpxml,
)
from home_energy_twin.types import LatLon

LOCATION = LatLon(latitude=41.0, longitude=-74.0, precision_m=50.0)


def _facts(**overrides: object) -> HomeownerFacts:
    payload: dict[str, object] = {
        "timezone": "America/New_York",
        "location": LOCATION,
        "year_built": 1998,
        "floor_area_m2": 140.0,
        "stories": 2,
        "bedrooms": 3,
        "climate_zone": "5",
        "heating_fuel": "natural_gas",
        "heating_system": "furnace",
        "cooling_fuel": "electricity",
        "cooling_system": "central_ac",
        "dhw_fuel": "natural_gas",
        "dhw_system": "tank",
    }
    payload.update(overrides)
    return HomeownerFacts(**payload)  # type: ignore[arg-type]


def test_generated_document_is_well_formed_and_carries_the_facts() -> None:
    result = generate_hpxml(_facts())
    root = ET.fromstring(result.hpxml)

    assert root.tag.endswith("HPXML")
    texts = {element.tag.rsplit("}", 1)[-1]: element.text for element in root.iter()}
    assert texts["YearBuilt"] == "1998"
    assert texts["FloorArea"] == "140"
    assert texts["NumberOfConditionedFloors"] == "2"
    assert texts["NumberOfBedrooms"] == "3"
    assert texts["Zone"] == "5"
    assert texts["HeatingFuel"] == "natural_gas"
    assert texts["CoolingFuel"] == "electricity"
    assert texts["WaterHeatingFuel"] == "natural_gas"


def test_hash_is_sha256_of_the_generated_content() -> None:
    result = generate_hpxml(_facts())
    digest = hashlib.sha256(result.hpxml.encode("utf-8")).hexdigest()
    assert result.hpxml_ref.sha256 == digest
    assert result.sha256 == digest
    assert result.house.hpxml_ref.sha256 == digest
    assert result.house.hpxml_ref.uri.endswith(f"{result.house.house_id}.xml")


def test_priors_are_flagged_machine_visibly_in_the_metadata() -> None:
    result = generate_hpxml(_facts())

    priors = {record.fact: record for record in result.priors}
    assert priors["enclosure/walls/u_value"].is_prior is True
    assert priors["enclosure/walls/u_value"].basis == "prior"
    assert priors["enclosure/walls/u_value"].source == PRIOR_SOURCE
    assert priors["enclosure/infiltration/ach50"].is_prior is True
    assert priors["systems/heating/capacity"].is_prior is True
    assert priors["systems/heating/capacity"].basis == "derived"

    # Homeowner-supplied facts are recorded, but are *not* priors.
    assert priors["building/year_built"].is_prior is False
    assert priors["building/year_built"].basis == "homeowner"

    assert "enclosure/walls/u_value" in result.prior_facts()
    assert "building/year_built" not in result.prior_facts()


def test_priors_are_flagged_machine_visibly_in_the_xml() -> None:
    result = generate_hpxml(_facts())
    root = ET.fromstring(result.hpxml)

    prior_records = [
        element
        for element in root.iter()
        if element.tag == f"{{{PRIOR_NS}}}prior"
    ]
    assert prior_records
    flagged = {element.get("fact") for element in prior_records}
    assert "enclosure/walls/u_value" in flagged
    for element in prior_records:
        if element.get("is-prior") == "true":
            assert element.get("source")

    # The affected elements carry the flag in place as well.
    u_value = [
        element
        for element in root.iter()
        if element.tag.endswith("UValue") and element.get("prior") == "true"
    ]
    assert u_value

    year_built = [
        element
        for element in root.iter()
        if element.tag.endswith("YearBuilt")
    ]
    assert year_built and year_built[0].get("prior") is None


def test_homeowner_overrides_are_not_flagged_as_priors() -> None:
    result = generate_hpxml(
        _facts(wall_u_value_w_m2_k=0.045, infiltration_ach50=0.25)
    )
    priors = {record.fact: record for record in result.priors}
    assert priors["enclosure/walls/u_value"].is_prior is False
    assert priors["enclosure/infiltration/ach50"].is_prior is False

    root = ET.fromstring(result.hpxml)
    u_value = [
        element
        for element in root.iter()
        if element.tag.endswith("UValue") and element.get("prior") == "true"
    ]
    # Only the roof remains prior-derived in this house.
    assert len(u_value) == 1


def test_systems_inventory_follows_the_declared_facts() -> None:
    result = generate_hpxml(_facts(secondary_heating_fuel="electricity"))
    systems = result.house.systems
    assert systems.heating_fuels == ["natural_gas", "electricity"]
    assert systems.dual_fuel is True
    assert systems.cooling_fuels == ["electricity"]
    assert systems.dhw_fuels == ["natural_gas"]


def test_state_hash_changes_with_the_physical_description() -> None:
    first = generate_hpxml(_facts())
    second = generate_hpxml(_facts(year_built=1999))
    assert first.house.state_hash != second.house.state_hash

    same = generate_hpxml(_facts())
    assert same.house.state_hash == first.house.state_hash


def test_generation_is_deterministic() -> None:
    first = generate_hpxml(_facts())
    second = generate_hpxml(_facts())
    assert first.hpxml == second.hpxml
    assert first.hpxml_ref.sha256 == second.hpxml_ref.sha256
