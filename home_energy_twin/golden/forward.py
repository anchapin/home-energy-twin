"""The v1 forward model for golden houses (SPEC §6).

This is a small, deterministic, in-repo model — **not** an OCHRE or
OpenStudio integration.  At monthly granularity the building is treated as
quasi-steady: monthly energy is a function of the monthly degree days, the
house's known truth, and its equipment.

    heating load (kWh_th) = UA [W/K] * (HDD + k * HDD^2 / 30) * 24 h / 1000
    delivered energy      = load / efficiency   (fuel: AFUE, heat pump: COP)
    cooling load (kWh_th) = UA * CDD * 24 h / 1000 * (1 + latent ratio)
    base load (kWh)       = base load [W] * hours / 1000
    DHW (kWh)             = litres/day * 4.186 kJ/litre / 3600 / efficiency * days

The small quadratic term in the heating load is a deliberate, mild
non-linearity (UA depends on temperature difference) so that the monthly
degree-day recovery is not an exactly-invertible exercise.

The model is deliberately *not* a heavy engine: it never shells out, needs
no engine binary, and runs in milliseconds (RD-7).
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional, Sequence

from ..types import EnergyPoint, Provenance, UtilitySeries, WeatherSeries
from .types import ForwardConfig, GoldenHouse, MonthlyEnergy
from .weather import monthly_degree_days

__all__ = [
    "HEAT_LOAD_NONLINEARITY",
    "annual_truth_kwh",
    "monthly_energy",
    "simulate_monthly",
]

UTC = timezone.utc

#: Fraction of the degree-day load added by the quadratic term at 30 K.day.
HEAT_LOAD_NONLINEARITY = 0.10

#: DHW monthly draw variation (multiplicative, zero-mean, seeded).
_DHW_VARIATION = 0.08

_WATER_KJ_PER_LITRE = 4.186


def _provenance(year: int) -> Provenance:
    return Provenance(
        source="synthetic-golden-house-forward-model",
        retrieved_at_utc=datetime(year, 1, 1, tzinfo=UTC),
        terms_tag="CC0-1.0 (synthetic)",
    )


def _month_window(year: int, month: int) -> tuple[datetime, datetime]:
    """The half-open UTC window ``[month start, next month start)``."""
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC)
    return start, end


def monthly_energy(
    house: GoldenHouse,
    weather: WeatherSeries,
    seed: Optional[int] = None,
) -> list[MonthlyEnergy]:
    """Noise-free monthly energy per fuel for one golden house."""
    truth = house.truth
    systems = house.house.systems
    rng = random.Random(
        f"golden-forward:{house.seed}:{seed if seed is not None else 0}:{house.house.house_id}"
    )

    results: dict[tuple[str, int, int], float] = {}

    def add(fuel: str, year: int, month: int, kwh: float) -> None:
        key = (fuel, year, month)
        results[key] = results.get(key, 0.0) + kwh

    for degree_days in monthly_degree_days(weather):
        year = degree_days.year
        month = degree_days.month
        hours = degree_days.days * 24.0
        hdd = degree_days.hdd_kk
        cdd = degree_days.cdd_kk

        # --- base (plug / lighting) load: seasonally constant by design, so
        # that a degree-day regression is not confounded with a lighting cycle.
        add("electricity", year, month, truth.base_load_w * hours / 1000.0)

        # --- heating
        # Mild non-linearity: the effective UA grows with the temperature
        # difference, so the delivered load is slightly convex in HDD.  The
        # factor is driven by the *daily mean* degree days, which keeps it
        # bounded for cold months.
        hdd_mean = hdd / degree_days.days
        heat_term = hdd * (1.0 + HEAT_LOAD_NONLINEARITY * hdd_mean / 30.0)
        heat_load_kwh = truth.ua_w_per_k * heat_term * 24.0 / 1000.0
        # Monthly capacity: the equipment could in principle run continuously.
        # Anything above that is not served (the house would be cold), which
        # keeps energy conserved rather than inventing backup heat.
        available_kwh = truth.heating_capacity_kw * hours
        met_kwh = min(heat_load_kwh, available_kwh)
        delivered_kwh = met_kwh * (1.0 + truth.duct_loss_factor)
        primary_heat_fuel = systems.heating_fuels[0]
        add(
            primary_heat_fuel,
            year,
            month,
            delivered_kwh / truth.heating_efficiency,
        )

        # --- cooling
        cool_load_kwh = truth.ua_w_per_k * cdd * 24.0 / 1000.0 * (1.0 + truth.latent_ratio)
        available_cool_kwh = truth.cooling_capacity_kw * hours
        cool_served_kwh = min(cool_load_kwh, available_cool_kwh)
        add(
            "electricity",
            year,
            month,
            cool_served_kwh / truth.cooling_efficiency * (1.0 + truth.duct_loss_factor),
        )

        # --- domestic hot water
        dhw_base_kwh = (
            truth.dhw_litres_per_day
            * _WATER_KJ_PER_LITRE
            * truth.dhw_delta_t_k
            / 3600.0
            / truth.dhw_efficiency
            * degree_days.days
        )
        dhw_kwh = dhw_base_kwh * (1.0 + rng.uniform(-_DHW_VARIATION, _DHW_VARIATION))
        add(systems.dhw_fuels[0], year, month, dhw_kwh)

    return [
        MonthlyEnergy(fuel=fuel, year=year, month=month, kwh=max(0.0, kwh))  # type: ignore[arg-type]
        for (fuel, year, month), kwh in sorted(results.items())
    ]


def simulate_monthly(
    house: GoldenHouse,
    weather: WeatherSeries,
    config: Optional[ForwardConfig] = None,
) -> list[UtilitySeries]:
    """Run the forward model and return one billing-period series per fuel.

    ``config`` switches the injected realism: multiplicative measurement
    noise, missing months (gaps) and estimated reads.
    """
    settings = config or ForwardConfig()
    years = sorted({point.timestamp_utc.year for point in weather.points})
    year = years[0] if years else 2024

    truth_rows = monthly_energy(house, weather, seed=settings.seed)
    rng = random.Random(f"golden-sim:{house.seed}:{settings.seed}:{house.house.house_id}")

    by_fuel: dict[str, list[EnergyPoint]] = {}
    for row in truth_rows:
        if rng.random() < settings.gap_pct:
            continue
        value = row.kwh
        if settings.noise_pct > 0.0:
            value *= 1.0 + rng.gauss(0.0, settings.noise_pct / 100.0)
        value = max(0.0, value)
        estimated = rng.random() < settings.estimated_read_pct
        start, end = _month_window(row.year, row.month)
        by_fuel.setdefault(row.fuel, []).append(
            EnergyPoint(
                start_utc=start,
                end_utc=end,
                value=value,
                unit="kWh",
                estimated=estimated,
            )
        )

    series: list[UtilitySeries] = []
    for fuel in sorted(by_fuel):
        points = sorted(by_fuel[fuel], key=lambda point: point.start_utc)
        series.append(
            UtilitySeries(
                fuel=fuel,  # type: ignore[arg-type]
                kind="import",
                granularity="billing_period",
                points=points,
                stamp_semantics="interval_start",
                provenance=_provenance(year),
            )
        )
    return series


def annual_truth_kwh(rows: Sequence[MonthlyEnergy]) -> float:
    """Total annual energy of the noise-free forward model."""
    return sum(row.kwh for row in rows)
