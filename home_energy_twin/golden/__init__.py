"""Synthetic golden-house harness (SPEC §6, RD-8).

A seeded generator produces synthetic houses with *known* ground truth
(UA, capacitance, infiltration, equipment capacity and efficiency); a small
deterministic forward model turns truth + synthetic weather into monthly
billing-period energy series; and a monthly degree-day recovery reads those
series back and reports NAC and UA as ranges (LAW-1).

Everything in this package is synthetic (LAW-11).  No heavy engine is
involved (RD-7): the forward model is a small in-repo degree-day model, not
an OCHRE or OpenStudio integration.

Typical use::

    from home_energy_twin.golden import (
        ForwardConfig, generate_houses, monthly_energy, recover_monthly,
        simulate_monthly, synthetic_weather,
    )

    houses = generate_houses(seed=7, count=24)
    weather = synthetic_weather(archetype, seed=7)
    series = simulate_monthly(houses[0], weather, ForwardConfig(noise_pct=2.0))
    recovery = recover_monthly(houses[0], series, weather)
"""

from __future__ import annotations

from .forward import annual_truth_kwh, monthly_energy, simulate_monthly
from .generator import (
    ARCHETYPES,
    archetype_by_name,
    generate_house,
    generate_houses,
)
from .recovery import EFFICIENCY_BANDS, recover_monthly
from .types import (
    Archetype,
    ForwardConfig,
    GoldenHouse,
    GroundTruth,
    MonthlyDegreeDays,
    MonthlyEnergy,
    MonthlyRecovery,
)
from .weather import monthly_degree_days, synthetic_weather

__all__ = [
    "ARCHETYPES",
    "EFFICIENCY_BANDS",
    "Archetype",
    "ForwardConfig",
    "GoldenHouse",
    "GroundTruth",
    "MonthlyDegreeDays",
    "MonthlyEnergy",
    "MonthlyRecovery",
    "annual_truth_kwh",
    "archetype_by_name",
    "generate_house",
    "generate_houses",
    "monthly_degree_days",
    "monthly_energy",
    "recover_monthly",
    "simulate_monthly",
    "synthetic_weather",
]
