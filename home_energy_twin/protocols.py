"""Service Protocols for home-energy-twin (SPEC §3).

Every adapter boundary is a PEP 544 ``Protocol``.  Core code never imports
adapters; selection is by configuration (AGENTS.md).

No Protocol in v1 has a method that actuates a device (LAW-7): the closest
thing is :meth:`DeviceBridge.publish`, which writes twin *state* (sensors and
alerts) and never device commands.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import (
    AdvisoryControls,
    CalibratedModel,
    CostEstimate,
    DataCapabilities,
    DeviceState,
    EngineState,
    Granularity,
    HPXMLRef,
    House,
    LatLon,
    TimeWindow,
    TwinState,
    UtilitySeries,
    WeatherPoint,
    WeatherSeries,
)

__all__ = [
    "Calibrator",
    "CostProvider",
    "DataSource",
    "DeviceBridge",
    "EngineAdapter",
    "WeatherProvider",
]


@runtime_checkable
class EngineAdapter(Protocol):
    """Simulation / live-stepping engine (OCHRE, OpenStudio-HPXML, ...)."""

    name: str
    version: str

    def simulate(
        self,
        house: House,
        weather: WeatherSeries,
        params: CalibratedModel | HPXMLRef,
        window: TimeWindow,
    ) -> UtilitySeries:
        """Simulate energy use for ``window`` under ``params``."""
        ...

    def step(
        self,
        state: EngineState,
        weather_step: WeatherPoint,
        controls: AdvisoryControls,
    ) -> EngineState:
        """Advance the live twin by one step.  Advisory only — no actuation."""
        ...


@runtime_checkable
class Calibrator(Protocol):
    """Calibration algorithm (change-point baseline, grey-box RC, ...)."""

    id: str
    version: str
    supported_granularities: set[Granularity]

    def calibrate(
        self,
        house: House,
        utility: list[UtilitySeries],
        weather: WeatherSeries,
    ) -> CalibratedModel:
        """Calibrate a model, or refuse/degrade per LAW-3."""
        ...


@runtime_checkable
class DataSource(Protocol):
    """Utility data source (Green Button, UtilityAPI, CSV, HA bridge)."""

    id: str
    capabilities: DataCapabilities

    def fetch(self, house: House, window: TimeWindow) -> list[UtilitySeries]:
        """Fetch energy series for ``window``."""
        ...


@runtime_checkable
class WeatherProvider(Protocol):
    """Weather provider (Open-Meteo hosted API, NWS, EPW files)."""

    id: str

    def observed(self, location: LatLon, window: TimeWindow) -> WeatherSeries:
        """Observed/reanalysis weather for ``window`` (AMY, LAW-10)."""
        ...

    def forecast(self, location: LatLon, horizon_hours: int) -> WeatherSeries:
        """Forecast weather for forward simulation only."""
        ...


@runtime_checkable
class DeviceBridge(Protocol):
    """Read-only device access (thermostat, whole-home meter, CT monitor)."""

    id: str

    def read_state(self, house: House) -> DeviceState:
        """Read the current device state."""
        ...

    def publish(self, house: House, twin_state: TwinState) -> None:
        """Publish twin state/alerts.  Never a device command (LAW-7)."""
        ...


@runtime_checkable
class CostProvider(Protocol):
    """Cost/incentive data provider."""

    id: str

    def estimate(self, measure_id: str, house: House, region: str) -> CostEstimate:
        """Estimate the cost of a measure — always with source and as-of."""
        ...
