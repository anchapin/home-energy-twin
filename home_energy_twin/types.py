"""Domain types for home-energy-twin (SPEC §2).

All models are pydantic v2.  Every timestamp is UTC and is enforced at the
type level (LAW-5); local time exists only at ingest/display edges, derived
from a house's stored IANA timezone.

Range discipline (LAW-1): every user-facing calibrated parameter, savings
figure and payback is a range — never a bare point.  ``ParameterEstimate``
keeps a ``value`` (the centre of the reported range), but it must lie inside
``[low, high]``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AdvisoryControls",
    "AmountRange",
    "CalibratedModel",
    "CalibrationMetrics",
    "CostEstimate",
    "DataCapabilities",
    "DeviceState",
    "EngineState",
    "EnergyPoint",
    "FaultEvent",
    "Fuel",
    "Granularity",
    "HPXMLRef",
    "House",
    "Incentive",
    "LatLon",
    "MeterKind",
    "ModelStatus",
    "ParameterEstimate",
    "Provenance",
    "RCParameters",
    "RetrofitScenario",
    "StampSemantics",
    "SystemInventory",
    "TimeWindow",
    "TwinState",
    "UtilitySeries",
    "WeatherPoint",
    "WeatherSeries",
    "WeatherSourceKind",
    "UTCDateTime",
]


# --------------------------------------------------------------------------
# Primitive aliases
# --------------------------------------------------------------------------

Fuel = Literal["electricity", "natural_gas", "oil", "propane", "district"]
MeterKind = Literal["import", "export", "net", "delivered"]
Granularity = Literal["billing_period", "daily", "hourly", "subhourly"]
StampSemantics = Literal["interval_start", "interval_end"]
WeatherSourceKind = Literal["observed", "reanalysis", "forecast", "tmy"]
ModelStatus = Literal["valid", "stale", "invalidated_by_retrofit", "mv_in_progress"]
FaultSignature = Literal[
    "baseload_step",
    "heating_slope_change",
    "cooling_slope_change",
    "aux_heat_overuse",
    "short_cycling",
    "drift_suspected",
]
FaultConfidence = Literal["low", "medium", "high"]
FaultStatus = Literal["open", "acknowledged", "dismissed_not_a_fault", "confirmed"]


def _require_utc(value: datetime) -> datetime:
    """Reject naive or non-UTC timestamps (LAW-5)."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("timestamp must be timezone-aware")
    offset = value.utcoffset()
    if offset != timedelta(0):
        raise ValueError("timestamp must be UTC (offset 0), got offset " f"{offset}")
    return value.astimezone(timezone.utc)


UTCDateTime = Annotated[datetime, AfterValidator(_require_utc)]


class _Model(BaseModel):
    """Base for domain models: extra fields are rejected, values are validated."""

    model_config = ConfigDict(extra="forbid", frozen=False)


# --------------------------------------------------------------------------
# Supporting types
# --------------------------------------------------------------------------


class LatLon(_Model):
    """WGS84 position, with the precision it was recorded at."""

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    precision_m: Optional[float] = Field(default=None, gt=0.0)


class Provenance(_Model):
    """Where a number came from (LAW-12 — provenance everywhere)."""

    source: str = Field(min_length=1)
    retrieved_at_utc: UTCDateTime
    terms_tag: str = Field(min_length=1)


class HPXMLRef(_Model):
    """Reference to a canonical HPXML document plus its SHA-256."""

    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: Optional[str] = None

    @model_validator(mode="after")
    def _check_hash(self) -> "HPXMLRef":
        if len(self.sha256) != 64:
            raise ValueError("sha256 must be a 64 character hex digest")
        return self


class SystemInventory(_Model):
    """Declared systems and confounder flags for a house (RD-5)."""

    heating_fuels: list[Fuel] = Field(default_factory=list)
    cooling_fuels: list[Fuel] = Field(default_factory=list)
    dhw_fuels: list[Fuel] = Field(default_factory=list)
    has_pv: bool = False
    has_battery: bool = False
    has_ev_charging: bool = False
    dual_fuel: bool = False
    aux_heat: bool = False
    secondary_unmetered_fuel: bool = False


class TimeWindow(_Model):
    """A half-open ``[start_utc, end_utc)`` window."""

    start_utc: UTCDateTime
    end_utc: UTCDateTime

    @model_validator(mode="after")
    def _check_order(self) -> "TimeWindow":
        if self.end_utc <= self.start_utc:
            raise ValueError("TimeWindow is [start, end); end_utc must be after start_utc")
        return self

    @property
    def duration(self) -> timedelta:
        return self.end_utc - self.start_utc

    def contains(self, moment: datetime) -> bool:
        """True when ``moment`` lies in ``[start, end)``."""
        moment_utc = _require_utc(moment)
        return self.start_utc <= moment_utc < self.end_utc

    def overlaps(self, other: "TimeWindow") -> bool:
        return self.start_utc < other.end_utc and other.start_utc < self.end_utc

    def intersection(self, other: "TimeWindow") -> Optional["TimeWindow"]:
        start = max(self.start_utc, other.start_utc)
        end = min(self.end_utc, other.end_utc)
        if end <= start:
            return None
        return TimeWindow(start_utc=start, end_utc=end)


# --------------------------------------------------------------------------
# §2.1 House
# --------------------------------------------------------------------------


class House(_Model):
    """The physical house: HPXML is the single source of truth for the envelope."""

    house_id: UUID
    hpxml_ref: HPXMLRef
    timezone: str = Field(min_length=1)
    location: LatLon
    systems: SystemInventory = Field(default_factory=SystemInventory)
    state_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_timezone(self) -> "House":
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError(f"unknown IANA timezone: {self.timezone!r}") from exc
        return self


# --------------------------------------------------------------------------
# §2.2 UtilitySeries
# --------------------------------------------------------------------------


class EnergyPoint(_Model):
    """One metered energy amount over the half-open interval ``[start, end)``."""

    start_utc: UTCDateTime
    end_utc: UTCDateTime
    value: float
    unit: str = Field(min_length=1)
    estimated: bool = False

    @model_validator(mode="after")
    def _check_interval(self) -> "EnergyPoint":
        if self.end_utc <= self.start_utc:
            raise ValueError("EnergyPoint is [start, end); end_utc must be after start_utc")
        return self

    @property
    def duration(self) -> timedelta:
        return self.end_utc - self.start_utc


class UtilitySeries(_Model):
    """Energy for one ``(house, fuel, meter)``."""

    fuel: Fuel
    kind: MeterKind
    granularity: Granularity
    points: list[EnergyPoint] = Field(default_factory=list)
    stamp_semantics: StampSemantics
    provenance: Provenance
    meter_id: Optional[str] = None

    @model_validator(mode="after")
    def _check_ordered(self) -> "UtilitySeries":
        for previous, current in zip(self.points, self.points[1:]):
            if current.start_utc < previous.start_utc:
                raise ValueError("UtilitySeries points must be ordered by start_utc")
        return self


# --------------------------------------------------------------------------
# §2.3 WeatherSeries
# --------------------------------------------------------------------------


class WeatherPoint(_Model):
    """Weather observation at an instant (or short cadence bucket)."""

    timestamp_utc: UTCDateTime
    dry_bulb_c: float
    humidity_ratio: Optional[float] = Field(default=None, ge=0.0)
    rh: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    wind_mps: float = 0.0
    ghi_wm2: float = Field(default=0.0, ge=0.0)
    dni_wm2: Optional[float] = Field(default=None, ge=0.0)
    dhi_wm2: Optional[float] = Field(default=None, ge=0.0)


class WeatherSeries(_Model):
    """Weather over a declared cadence, with representativeness recorded."""

    points: list[WeatherPoint] = Field(default_factory=list)
    source_kind: WeatherSourceKind
    station_or_cell: str = Field(min_length=1)
    distance_km: float = Field(ge=0.0)
    provenance: Provenance

    @model_validator(mode="after")
    def _check_ordered(self) -> "WeatherSeries":
        for previous, current in zip(self.points, self.points[1:]):
            if current.timestamp_utc < previous.timestamp_utc:
                raise ValueError("WeatherSeries points must be ordered by timestamp_utc")
        return self


# --------------------------------------------------------------------------
# §2.4 CalibratedModel
# --------------------------------------------------------------------------


class ParameterEstimate(_Model):
    """A calibrated parameter reported as a range (LAW-1)."""

    value: float
    low: float
    high: float
    unit: str = Field(min_length=1)
    method: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_range(self) -> "ParameterEstimate":
        if self.low > self.high:
            raise ValueError("ParameterEstimate requires low <= high")
        if not (self.low <= self.value <= self.high):
            raise ValueError("ParameterEstimate value must lie inside [low, high]")
        return self


class CalibrationMetrics(_Model):
    """Guideline 14 metrics stored with every calibration (LAW-2)."""

    nmbe_pct: float
    cvrmse_pct: float
    granularity: Granularity
    guideline14_pass: bool


class RCParameters(_Model):
    """Grey-box RC results, reported as ranges (LAW-1)."""

    ua: ParameterEstimate
    capacitance: ParameterEstimate
    tau: ParameterEstimate
    per_zone: dict[str, ParameterEstimate] = Field(default_factory=dict)


class CalibratedModel(_Model):
    """The result of a calibration run."""

    house_id: UUID
    state_hash: str = Field(min_length=1)
    engine_ref: str = Field(min_length=1)
    hpxml_ref: HPXMLRef
    parameters: dict[str, ParameterEstimate] = Field(default_factory=dict)
    metrics: CalibrationMetrics
    calibration_window: TimeWindow
    data_granularity: Granularity
    calibrator_id: str = Field(min_length=1)
    calibrator_version: str = Field(min_length=1)
    status: ModelStatus
    rc_parameters: Optional[RCParameters] = None
    bpi2400_metrics: Optional[dict[str, float]] = None


# --------------------------------------------------------------------------
# §2.5 RetrofitScenario
# --------------------------------------------------------------------------


class AmountRange(_Model):
    """A monetary amount as a range (LAW-1)."""

    low: float
    high: float
    currency: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_range(self) -> "AmountRange":
        if self.low > self.high:
            raise ValueError("AmountRange requires low <= high")
        return self


class CostEstimate(_Model):
    """Cost of a measure, always with source and as-of date."""

    low: float
    high: float
    currency: str = Field(min_length=1)
    source: str = Field(min_length=1)
    as_of: UTCDateTime
    region: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_range(self) -> "CostEstimate":
        if self.low > self.high:
            raise ValueError("CostEstimate requires low <= high")
        return self


class Incentive(_Model):
    """A programme incentive, with its amount as a range."""

    program: str = Field(min_length=1)
    amount_or_range: AmountRange
    source: str = Field(min_length=1)
    as_of: UTCDateTime


class PaybackRange(_Model):
    """Payback in years, as a range (LAW-1)."""

    low: float
    high: float

    @model_validator(mode="after")
    def _check_range(self) -> "PaybackRange":
        if self.low > self.high:
            raise ValueError("PaybackRange requires low <= high")
        return self


class RetrofitScenario(_Model):
    """A retrofit measure evaluated against a valid calibrated baseline."""

    measure_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    hpxml_transform_ref: HPXMLRef
    baseline_model_ref: str = Field(min_length=1)
    savings: dict[Fuel, ParameterEstimate] = Field(default_factory=dict)
    cost: CostEstimate
    incentives: list[Incentive] = Field(default_factory=list)
    payback_years: Optional[PaybackRange] = None


# --------------------------------------------------------------------------
# §2.6 FaultEvent
# --------------------------------------------------------------------------


class FaultEvent(_Model):
    """A detected sustained-residual event."""

    house_id: UUID
    detector_id: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    detected_at_utc: UTCDateTime
    window: TimeWindow
    signature: FaultSignature
    explanation: str = Field(min_length=1)
    confidence: FaultConfidence
    status: FaultStatus


# --------------------------------------------------------------------------
# Supporting types named by the SPEC §3 Protocol signatures
# --------------------------------------------------------------------------


class EngineState(_Model):
    """Engine state carried between live-twin steps."""

    timestamp_utc: UTCDateTime
    zone_temperatures_c: dict[str, float] = Field(default_factory=dict)
    thermal_state_c: float = 0.0
    runtime_s: float = Field(default=0.0, ge=0.0)
    extra: dict[str, float] = Field(default_factory=dict)


class AdvisoryControls(_Model):
    """Advisory (non-actuating) control suggestions for display/hand-off.

    These are *suggested* setpoints only.  No v1 code path actuates a device
    (LAW-7).
    """

    heating_setpoint_c: Optional[float] = None
    cooling_setpoint_c: Optional[float] = None
    rationale: Optional[str] = None


class DeviceState(_Model):
    """Read-only state of a v1 device (thermostat, meter, CT monitor)."""

    device_id: str = Field(min_length=1)
    device_kind: Literal["thermostat", "meter", "ct_monitor"] = "meter"
    timestamp_utc: UTCDateTime
    readings: dict[str, float] = Field(default_factory=dict)
    runtime_s: float = Field(default=0.0, ge=0.0)


class TwinState(_Model):
    """The twin's published state (sensors/alerts out — never commands)."""

    house_id: UUID
    timestamp_utc: UTCDateTime
    predicted_value: float
    observed_value: Optional[float] = None
    unit: str = Field(min_length=1)
    advisory: Optional[AdvisoryControls] = None
    model_status: ModelStatus = "valid"


class DataCapabilities(_Model):
    """What a data source can deliver — used to degrade honestly."""

    granularities: set[Granularity] = Field(default_factory=set)
    fuels: set[Fuel] = Field(default_factory=set)
    latency_seconds: float = Field(default=0.0, ge=0.0)
    history_depth_days: float = Field(default=0.0, ge=0.0)
    stamp_semantics: StampSemantics = "interval_start"
