"""Tests for the SPEC §3 Protocols (LAW-7, LAW-9)."""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from typing import get_type_hints

from home_energy_twin import protocols
from home_energy_twin.types import (
    AdvisoryControls,
    CalibratedModel,
    CostEstimate,
    DataCapabilities,
    DeviceState,
    EngineState,
    EnergyPoint,
    Granularity,
    HPXMLRef,
    House,
    LatLon,
    ParameterEstimate,
    Provenance,
    CalibrationMetrics,
    TimeWindow,
    TwinState,
    UtilitySeries,
    WeatherPoint,
    WeatherSeries,
)

UTC = timezone.utc
SHA = "b" * 64

PROTOCOL_NAMES = [
    "EngineAdapter",
    "Calibrator",
    "DataSource",
    "WeatherProvider",
    "DeviceBridge",
    "CostProvider",
]


def _provenance() -> Provenance:
    return Provenance(
        source="synthetic-fixture",
        retrieved_at_utc=datetime(2024, 1, 1, tzinfo=UTC),
        terms_tag="CC0-1.0",
    )


def _house() -> House:
    return House(
        house_id=uuid.uuid4(),
        hpxml_ref=HPXMLRef(uri="file:///synthetic/home.xml", sha256=SHA),
        timezone="America/New_York",
        location=LatLon(latitude=43.0, longitude=-70.0),
        state_hash="state-1",
    )


class _FakeEngine:
    name = "fake"
    version = "0.1"

    def simulate(
        self,
        house: House,
        weather: WeatherSeries,
        params: CalibratedModel | HPXMLRef,
        window: TimeWindow,
    ) -> UtilitySeries:
        return UtilitySeries(
            fuel="electricity",
            kind="net",
            granularity="hourly",
            points=[],
            stamp_semantics="interval_start",
            provenance=_provenance(),
        )

    def step(
        self,
        state: EngineState,
        weather_step: WeatherPoint,
        controls: AdvisoryControls,
    ) -> EngineState:
        return state


class _FakeCalibrator:
    id = "fake-calibrator"
    version = "0.1"
    supported_granularities: set[Granularity] = {"monthly"}

    def calibrate(
        self,
        house: House,
        utility: list[UtilitySeries],
        weather: WeatherSeries,
    ) -> CalibratedModel:
        raise NotImplementedError


class _FakeDataSource:
    id = "fake-source"
    capabilities = DataCapabilities(granularities={"billing_period"}, fuels={"electricity"})

    def fetch(self, house: House, window: TimeWindow) -> list[UtilitySeries]:
        return []


class _FakeWeatherProvider:
    id = "fake-weather"

    def observed(self, location: LatLon, window: TimeWindow) -> WeatherSeries:
        return WeatherSeries(
            points=[],
            source_kind="observed",
            station_or_cell="SYNTHETIC",
            distance_km=1.0,
            provenance=_provenance(),
        )

    def forecast(self, location: LatLon, horizon_hours: int) -> WeatherSeries:
        return WeatherSeries(
            points=[],
            source_kind="forecast",
            station_or_cell="SYNTHETIC",
            distance_km=1.0,
            provenance=_provenance(),
        )


class _FakeDeviceBridge:
    id = "fake-bridge"

    def read_state(self, house: House) -> DeviceState:
        return DeviceState(
            device_id="synthetic",
            timestamp_utc=datetime(2024, 1, 1, tzinfo=UTC),
        )

    def publish(self, house: House, twin_state: TwinState) -> None:
        return None


class _FakeCostProvider:
    id = "fake-cost"

    def estimate(self, measure_id: str, house: House, region: str) -> CostEstimate:
        return CostEstimate(
            low=0.0,
            high=1.0,
            currency="USD",
            source="synthetic",
            as_of=datetime(2024, 1, 1, tzinfo=UTC),
            region=region,
        )


def test_all_six_protocols_exist() -> None:
    for name in PROTOCOL_NAMES:
        assert hasattr(protocols, name), name


def test_protocols_are_runtime_checkable() -> None:
    assert isinstance(_FakeEngine(), protocols.EngineAdapter)
    assert isinstance(_FakeCalibrator(), protocols.Calibrator)
    assert isinstance(_FakeDataSource(), protocols.DataSource)
    assert isinstance(_FakeWeatherProvider(), protocols.WeatherProvider)
    assert isinstance(_FakeDeviceBridge(), protocols.DeviceBridge)
    assert isinstance(_FakeCostProvider(), protocols.CostProvider)


def test_incomplete_implementations_do_not_satisfy_protocols() -> None:
    class _NotAnEngine:
        name = "x"

    assert not isinstance(_NotAnEngine(), protocols.EngineAdapter)


def test_no_protocol_method_can_actuate_a_device() -> None:
    """LAW-7: no Protocol in v1 exposes a device-actuating method."""
    forbidden = {
        "set_setpoint",
        "set_temperature",
        "actuate",
        "write_setpoint",
        "send_command",
        "turn_on",
        "turn_off",
        "switch_on",
        "switch_off",
        "start_device",
        "stop_device",
    }
    for name in PROTOCOL_NAMES:
        protocol = getattr(protocols, name)
        members = list(protocol.__protocol_attrs__)
        for member in members:
            assert member not in forbidden, f"{name}.{member} would actuate a device"
        for member in members:
            attr = inspect.getattr_static(protocol, member, None)
            if isinstance(attr, property) or attr is None:
                continue
            function = getattr(protocol, member, None)
            if not callable(function):
                continue
            parameters = list(inspect.signature(function).parameters)
            # A command would need a device/actuator argument, not a twin state.
            assert "device_command" not in parameters
            assert "command" not in parameters


def test_device_bridge_publish_takes_twin_state_only() -> None:
    hints = get_type_hints(protocols.DeviceBridge.publish)
    assert hints["twin_state"] is TwinState
    assert "command" not in hints


def test_engine_step_takes_advisory_controls() -> None:
    hints = get_type_hints(protocols.EngineAdapter.step)
    assert hints["controls"] is AdvisoryControls
