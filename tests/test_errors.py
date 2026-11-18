"""Tests for the typed errors that may cross a service boundary (SPEC §5)."""

from __future__ import annotations

import pytest

from home_energy_twin.errors import (
    EnergyTwinError,
    EngineUnavailable,
    InsufficientData,
    ProvenanceMissing,
    StaleCostData,
    UnidentifiableConfiguration,
)

TYPED_ERRORS = [
    UnidentifiableConfiguration,
    InsufficientData,
    ProvenanceMissing,
    StaleCostData,
    EngineUnavailable,
]


@pytest.mark.parametrize("error_type", TYPED_ERRORS)
def test_typed_errors_derive_from_the_common_base(error_type: type[EnergyTwinError]) -> None:
    error = error_type("something went wrong", reason="synthetic")
    assert isinstance(error, EnergyTwinError)
    assert isinstance(error, Exception)
    assert error.reason == "synthetic"


@pytest.mark.parametrize("error_type", TYPED_ERRORS)
def test_typed_errors_are_distinct(error_type: type[EnergyTwinError]) -> None:
    others = [other for other in TYPED_ERRORS if other is not error_type]
    for other in others:
        assert not issubclass(error_type, other)
        assert not issubclass(other, error_type)


def test_engine_unavailable_is_not_a_bare_value_error() -> None:
    with pytest.raises(EngineUnavailable):
        raise EngineUnavailable("engine binaries are not installed", reason="rd-7")
