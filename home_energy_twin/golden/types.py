"""Types for the synthetic golden-house harness (SPEC §6).

Everything here is **synthetic** (LAW-11): the generator fabricates houses
with *known* ground truth, the forward model turns that truth plus a synthetic
weather series into monthly (billing-period) energy series, and the recovery
module reads those series back and reports what it can (and cannot) recover.

The harness reuses the SPEC §2 domain types (``House``, ``UtilitySeries``,
``WeatherSeries``, ``ParameterEstimate``) rather than inventing parallel ones.
Recovered quantities are always ranges (LAW-1).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..types import Fuel, House, ParameterEstimate

__all__ = [
    "Archetype",
    "ForwardConfig",
    "GoldenHouse",
    "GroundTruth",
    "MonthlyDegreeDays",
    "MonthlyEnergy",
    "MonthlyRecovery",
]


class _Model(BaseModel):
    """Base for harness models: extra fields are rejected, values validated."""

    model_config = ConfigDict(extra="forbid")


class Archetype(_Model):
    """A house archetype: climate, vintage, size, systems, and truth ranges.

    The ranges are the *draw bounds* used by the generator, so a generated
    house's truth always lies inside the archetype's stated ranges (this is
    asserted by the harness property tests).
    """

    name: str = Field(min_length=1)
    climate_zone: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)

    heating_fuels: list[Fuel] = Field(min_length=1)
    cooling_fuels: list[Fuel] = Field(min_length=1)
    dhw_fuels: list[Fuel] = Field(min_length=1)

    floor_area_m2: tuple[float, float]
    vintage_year: tuple[int, int]

    # Envelope conduction UA (W/K) and infiltration (ACH50) are drawn
    # independently; the total UA is their sum (see :class:`GroundTruth`).
    envelope_ua_w_per_k: tuple[float, float]
    infiltration_ach50: tuple[float, float]
    capacitance_j_per_k: tuple[float, float]

    heating_efficiency: tuple[float, float]
    cooling_efficiency: tuple[float, float]

    # Equipment is sized from the house: the nameplate capacity follows the
    # design temperature difference (indoor setpoint vs outdoor design) and
    # an oversizing factor.
    heating_design_delta_t_k: tuple[float, float]
    cooling_design_delta_t_k: tuple[float, float]
    equipment_oversize: tuple[float, float]

    duct_loss_factor: tuple[float, float]
    base_load_w: tuple[float, float]
    dhw_litres_per_day: tuple[float, float]
    dhw_efficiency: tuple[float, float]
    dhw_delta_t_k: tuple[float, float]
    latent_ratio: tuple[float, float]

    annual_mean_c: float
    seasonal_amplitude_c: float = Field(gt=0.0)

    confounded: bool = False

    @model_validator(mode="after")
    def _check_bounds(self) -> "Archetype":
        for name in (
            "floor_area_m2",
            "envelope_ua_w_per_k",
            "infiltration_ach50",
            "capacitance_j_per_k",
            "heating_efficiency",
            "cooling_efficiency",
            "heating_design_delta_t_k",
            "cooling_design_delta_t_k",
            "equipment_oversize",
            "duct_loss_factor",
            "base_load_w",
            "dhw_litres_per_day",
            "dhw_efficiency",
            "dhw_delta_t_k",
            "latent_ratio",
        ):
            low, high = getattr(self, name)
            if not low < high:
                raise ValueError(f"Archetype {name} bounds must be increasing")
        if self.vintage_year[0] >= self.vintage_year[1]:
            raise ValueError("Archetype vintage_year bounds must be increasing")
        return self


class GroundTruth(_Model):
    """The known truth of one synthetic house (SPEC §6 generator)."""

    ua_w_per_k: float = Field(gt=0.0)
    envelope_ua_w_per_k: float = Field(gt=0.0)
    infiltration_ach50: float = Field(ge=0.0)
    infiltration_ua_w_per_k: float = Field(ge=0.0)
    capacitance_j_per_k: float = Field(gt=0.0)

    heating_capacity_kw: float = Field(gt=0.0)
    heating_efficiency: float = Field(gt=0.0, le=6.0)
    cooling_capacity_kw: float = Field(gt=0.0)
    cooling_efficiency: float = Field(gt=0.0, le=8.0)

    duct_loss_factor: float = Field(ge=0.0, le=0.6)
    base_load_w: float = Field(ge=0.0)
    dhw_litres_per_day: float = Field(ge=0.0)
    dhw_efficiency: float = Field(gt=0.0, le=1.0)
    dhw_delta_t_k: float = Field(gt=0.0, le=80.0)
    latent_ratio: float = Field(ge=0.0, le=1.5)

    floor_area_m2: float = Field(gt=0.0)
    vintage_year: int = Field(ge=1800, le=2100)
    climate_zone: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_consistency(self) -> "GroundTruth":
        total = self.envelope_ua_w_per_k + self.infiltration_ua_w_per_k
        if abs(total - self.ua_w_per_k) > 1e-6:
            raise ValueError(
                "ua_w_per_k must equal envelope_ua_w_per_k + infiltration_ua_w_per_k"
            )
        return self


class GoldenHouse(_Model):
    """A synthetic house plus its known truth."""

    house: House
    truth: GroundTruth
    archetype: str = Field(min_length=1)
    seed: int
    confounded: bool = False

    @property
    def heating_fuel(self) -> Fuel:
        fuels = self.house.systems.heating_fuels
        if not fuels:  # pragma: no cover - guarded by the generator
            raise ValueError("golden house has no declared heating fuel")
        return fuels[0]

    @property
    def cooling_fuel(self) -> Fuel:
        fuels = self.house.systems.cooling_fuels
        if not fuels:  # pragma: no cover - guarded by the generator
            raise ValueError("golden house has no declared cooling fuel")
        return fuels[0]

    @property
    def dhw_fuel(self) -> Fuel:
        fuels = self.house.systems.dhw_fuels
        if not fuels:  # pragma: no cover - guarded by the generator
            raise ValueError("golden house has no declared DHW fuel")
        return fuels[0]


class ForwardConfig(_Model):
    """Switches for the v1 forward model (noise, gaps, estimated reads)."""

    noise_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    estimated_read_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    gap_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    seed: int = 0


class MonthlyDegreeDays(_Model):
    """Degree days for one calendar month of the synthetic weather year."""

    year: int
    month: int = Field(ge=1, le=12)
    hdd_kk: float = Field(ge=0.0)
    cdd_kk: float = Field(ge=0.0)
    days: int = Field(ge=1, le=31)


class MonthlyEnergy(_Model):
    """Noise-free monthly energy for one fuel (the forward model's truth)."""

    fuel: Fuel
    year: int
    month: int = Field(ge=1, le=12)
    kwh: float = Field(ge=0.0)


class MonthlyRecovery(_Model):
    """What a monthly degree-day recovery could recover (LAW-1: ranges).

    ``nac_kwh`` is the weather-normalised annual consumption range and
    ``ua_w_per_k`` the recovered UA range.  ``degraded`` is the LAW-3 flag:
    when the monthly data (or the house configuration) cannot identify the
    requested parameters, the reason is named rather than silently guessed.
    """

    nac_kwh: Optional[ParameterEstimate] = None
    ua_w_per_k: Optional[ParameterEstimate] = None
    months_used: int = Field(ge=0)
    heating_slope_kwh_per_hdd: Optional[float] = None
    cooling_slope_kwh_per_cdd: Optional[float] = None
    degraded: bool = False
    degradation_reason: Optional[str] = None
    method: str = Field(min_length=1)
