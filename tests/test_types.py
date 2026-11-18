"""Tests for the SPEC §2 domain types (LAW-1, LAW-2, LAW-5, LAW-12)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from home_energy_twin.types import (
    AdvisoryControls,
    AmountRange,
    CalibratedModel,
    CalibrationMetrics,
    CostEstimate,
    DataCapabilities,
    DeviceState,
    EnergyPoint,
    FaultEvent,
    Granularity,
    HPXMLRef,
    House,
    Incentive,
    LatLon,
    ParameterEstimate,
    Provenance,
    RetrofitScenario,
    SystemInventory,
    TimeWindow,
    UtilitySeries,
    WeatherPoint,
    WeatherSeries,
)

UTC = timezone.utc
SHA = "a" * 64


def _provenance() -> Provenance:
    return Provenance(
        source="synthetic-fixture",
        retrieved_at_utc=datetime(2024, 1, 1, tzinfo=UTC),
        terms_tag="CC0-1.0",
    )


def _point(start: datetime, hours: int = 1) -> EnergyPoint:
    return EnergyPoint(
        start_utc=start,
        end_utc=start + timedelta(hours=hours),
        value=1.5,
        unit="kWh",
        estimated=False,
    )


def _house() -> House:
    return House(
        house_id=uuid.uuid4(),
        hpxml_ref=HPXMLRef(uri="file:///synthetic/home.xml", sha256=SHA),
        timezone="America/New_York",
        location=LatLon(latitude=43.0, longitude=-70.0),
        systems=SystemInventory(has_pv=False, dual_fuel=False),
        state_hash="state-1",
    )


def test_house_requires_known_iana_timezone() -> None:
    house = _house()
    assert house.timezone == "America/New_York"
    with pytest.raises(ValidationError):
        house.model_copy(update={"timezone": "Mars/Olympus"}).model_validate(
            {**house.model_dump(), "timezone": "Mars/Olympus"}
        )


def test_parameter_estimate_must_be_a_range() -> None:
    estimate = ParameterEstimate(value=2.0, low=1.0, high=3.0, unit="W/K", method="bootstrap")
    assert estimate.value == 2.0
    with pytest.raises(ValidationError):
        ParameterEstimate(value=9.0, low=1.0, high=3.0, unit="W/K", method="bootstrap")
    with pytest.raises(ValidationError):
        ParameterEstimate(value=2.0, low=3.0, high=1.0, unit="W/K", method="bootstrap")


def test_amount_and_cost_ranges_are_ordered() -> None:
    with pytest.raises(ValidationError):
        AmountRange(low=10.0, high=1.0, currency="USD")
    with pytest.raises(ValidationError):
        CostEstimate(
            low=10.0,
            high=1.0,
            currency="USD",
            source="synthetic",
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            region="US-NY",
        )


def test_non_utc_timestamps_are_rejected() -> None:
    eastern = timezone(timedelta(hours=-5), name="EST")
    with pytest.raises(ValidationError):
        EnergyPoint(
            start_utc=datetime(2024, 1, 1, 5, tzinfo=eastern),
            end_utc=datetime(2024, 1, 1, 6, tzinfo=eastern),
            value=1.0,
            unit="kWh",
        )
    with pytest.raises(ValidationError):
        Provenance(
            source="synthetic",
            retrieved_at_utc=datetime(2024, 1, 1, 5),
            terms_tag="CC0-1.0",
        )


def test_energy_point_interval_is_half_open() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    point = _point(start)
    assert point.duration == timedelta(hours=1)
    with pytest.raises(ValidationError):
        EnergyPoint(start_utc=start, end_utc=start, value=1.0, unit="kWh")
    with pytest.raises(ValidationError):
        EnergyPoint(start_utc=start, end_utc=start - timedelta(seconds=1), value=1.0, unit="kWh")


def test_time_window_contains_is_half_open() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    window = TimeWindow(start_utc=start, end_utc=start + timedelta(hours=1))
    assert window.contains(start)
    assert not window.contains(start + timedelta(hours=1))
    assert window.duration == timedelta(hours=1)
    with pytest.raises(ValidationError):
        TimeWindow(start_utc=start, end_utc=start)


def test_utility_series_rejects_unordered_points() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    series = UtilitySeries(
        fuel="electricity",
        kind="import",
        granularity="hourly",
        points=[_point(start), _point(start + timedelta(hours=1))],
        stamp_semantics="interval_start",
        provenance=_provenance(),
    )
    assert len(series.points) == 2
    with pytest.raises(ValidationError):
        UtilitySeries(
            fuel="electricity",
            kind="import",
            granularity="hourly",
            points=[_point(start + timedelta(hours=1)), _point(start)],
            stamp_semantics="interval_start",
            provenance=_provenance(),
        )


def test_utility_series_never_mixes_meter_kinds() -> None:
    with pytest.raises(ValidationError):
        UtilitySeries(
            fuel="electricity",
            kind="mixed",
            granularity="daily",
            points=[],
            stamp_semantics="interval_start",
            provenance=_provenance(),
        )


def test_weather_series_records_representativeness() -> None:
    series = WeatherSeries(
        points=[
            WeatherPoint(timestamp_utc=datetime(2024, 2, 29, tzinfo=UTC), dry_bulb_c=3.5)
        ],
        source_kind="observed",
        station_or_cell="SYNTHETIC-STATION-1",
        distance_km=2.5,
        provenance=_provenance(),
    )
    assert series.source_kind == "observed"
    assert series.distance_km == 2.5


def test_calibrated_model_carries_metrics_and_window() -> None:
    model = CalibratedModel(
        house_id=uuid.uuid4(),
        state_hash="state-1",
        engine_ref="ochre/0.1",
        hpxml_ref=HPXMLRef(uri="file:///synthetic/calibrated.xml", sha256=SHA),
        parameters={
            "ua": ParameterEstimate(
                value=150.0, low=120.0, high=190.0, unit="W/K", method="residual-bootstrap"
            )
        },
        metrics=CalibrationMetrics(
            nmbe_pct=1.2, cvrmse_pct=8.0, granularity="billing_period", guideline14_pass=True
        ),
        calibration_window=TimeWindow(
            start_utc=datetime(2023, 1, 1, tzinfo=UTC),
            end_utc=datetime(2023, 12, 31, tzinfo=UTC),
        ),
        data_granularity="billing_period",
        calibrator_id="changepoint",
        calibrator_version="0.1",
        status="valid",
    )
    assert model.metrics.guideline14_pass is True
    assert model.status == "valid"


def test_retrofit_scenario_savings_are_ranges_and_payback_may_be_none() -> None:
    scenario = RetrofitScenario(
        measure_id="resstock:air_sealing_1",
        description="synthetic measure",
        hpxml_transform_ref=HPXMLRef(uri="file:///synthetic/transform.xml", sha256=SHA),
        baseline_model_ref="model-1",
        savings={
            "natural_gas": ParameterEstimate(
                value=4.0, low=2.0, high=6.0, unit="MMBtu/yr", method="bounded-delta"
            )
        },
        cost=CostEstimate(
            low=1000.0,
            high=2500.0,
            currency="USD",
            source="synthetic-cost-db",
            as_of=datetime(2024, 6, 1, tzinfo=UTC),
            region="US-NY",
        ),
        incentives=[
            Incentive(
                program="synthetic-program",
                amount_or_range=AmountRange(low=0.0, high=500.0, currency="USD"),
                source="synthetic-cost-db",
                as_of=datetime(2024, 6, 1, tzinfo=UTC),
            )
        ],
        payback_years=None,
    )
    assert scenario.payback_years is None
    assert scenario.savings["natural_gas"].low == 2.0


def test_fault_event_carries_window_and_status() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    event = FaultEvent(
        house_id=uuid.uuid4(),
        detector_id="baseload-step",
        detector_version="0.1",
        detected_at_utc=start + timedelta(days=3),
        window=TimeWindow(start_utc=start, end_utc=start + timedelta(days=3)),
        signature="baseload_step",
        explanation="synthetic baseload rose 20% over the window",
        confidence="medium",
        status="open",
    )
    assert event.signature == "baseload_step"


def test_capabilities_and_device_state_are_typed() -> None:
    capabilities = DataCapabilities(
        granularities={"billing_period", "daily"},
        fuels={"electricity"},
        latency_seconds=900.0,
        history_depth_days=730.0,
    )
    assert capabilities.granularities == {"billing_period", "daily"}
    device = DeviceState(
        device_id="synthetic-thermostat",
        device_kind="thermostat",
        timestamp_utc=datetime(2024, 1, 1, tzinfo=UTC),
        readings={"indoor_temp_c": 20.5},
    )
    assert device.readings["indoor_temp_c"] == 20.5
    advisory = AdvisoryControls(heating_setpoint_c=19.0, rationale="advisory only")
    assert advisory.heating_setpoint_c == 19.0


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        LatLon(latitude=0.0, longitude=0.0, surprise=1)


def test_granularity_literal_is_closed() -> None:
    assert Granularity.__args__ == ("billing_period", "daily", "hourly", "subhourly")
