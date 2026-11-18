"""Fallback live-stepping engine for Spike B: a 1R1C grey-box RC model.

This is the *fallback* engine named in ``spikes/ochre/REPORT.md``: if OCHRE
cannot be stepped externally (or cannot be installed at all), the live-twin
loop of Phase 2 needs *some* engine that satisfies the ``EngineAdapter``
shape of ``home_energy_twin/protocols.py``.  This module provides exactly
that, in pure Python, with no third-party dependency and no binary.

Design constraints honoured here:

* the engine implements the ``EngineAdapter`` shape (``name``, ``version``,
  ``simulate``, ``step``) — it does not invent a new engine API;
* everything it consumes is *supplied* (weather points, advisory setpoints) —
  it never calls out to a weather service (LAW-6);
* it never actuates a device (LAW-7): the setpoints it honours are the
  advisory suggestions carried in ``AdvisoryControls``;
* all quantities are plain floats with explicit unit suffixes; no calibrated
  parameter is ever reported as a bare point value in user-facing output
  (LAW-1) — the spike reports ranges only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from home_energy_twin.types import (
    AdvisoryControls,
    CalibratedModel,
    EnergyPoint,
    EngineState,
    House,
    HPXMLRef,
    Provenance,
    TimeWindow,
    UtilitySeries,
    WeatherPoint,
    WeatherSeries,
)

__all__ = ["RCEngine", "RCParams"]

ZONE = "main"


@dataclass(frozen=True)
class RCParams:
    """Grey-box RC parameters (SI units, deterministic)."""

    ua_w_per_k: float = 120.0
    """Envelope conductance (W/K)."""
    capacitance_j_per_k: float = 6.0e6
    """Effective building capacitance (J/K)."""
    heating_max_w: float = 4000.0
    cooling_max_w: float = 2500.0
    heating_gain_w_per_k: float = 900.0
    cooling_gain_w_per_k: float = 700.0
    deadband_c: float = 0.5
    initial_indoor_c: float = 20.0

    def tau_hours(self) -> float:
        """Thermal time constant in hours (C / UA)."""
        return self.capacitance_j_per_k / self.ua_w_per_k / 3600.0


class RCEngine:
    """A one-zone RC engine exposing the ``EngineAdapter`` shape."""

    name = "rc-1r1c"
    version = "0.1.0-spikeB"

    def __init__(self, params: RCParams | None = None, dt_s: float = 900.0) -> None:
        if dt_s <= 0:
            raise ValueError("dt_s must be positive")
        self.params = params if params is not None else RCParams()
        self.dt_s = dt_s

    # -- state helpers ----------------------------------------------------

    def initial_state(self, start_utc: datetime | None = None) -> EngineState:
        """A cold-start state for the synthetic house."""
        ts = start_utc if isinstance(start_utc, datetime) else datetime(2026, 1, 5, tzinfo=UTC)
        return EngineState(
            timestamp_utc=ts,
            zone_temperatures_c={ZONE: self.params.initial_indoor_c},
            thermal_state_c=self.params.initial_indoor_c,
            runtime_s=0.0,
            extra={"heating_wh": 0.0, "cooling_wh": 0.0, "outdoor_c": 0.0},
        )

    # -- EngineAdapter ----------------------------------------------------

    def step(
        self,
        state: EngineState,
        weather_step: WeatherPoint,
        controls: AdvisoryControls,
    ) -> EngineState:
        """Advance the live twin by one step.  Advisory only — no actuation."""
        p = self.params
        t_in = float(state.zone_temperatures_c.get(ZONE, p.initial_indoor_c))
        t_out = weather_step.dry_bulb_c
        dt = self.dt_s

        q_w = 0.0
        heat_set = controls.heating_setpoint_c
        cool_set = controls.cooling_setpoint_c
        if heat_set is not None and t_in < heat_set - p.deadband_c:
            q_w += min(p.heating_max_w, p.heating_gain_w_per_k * (heat_set - t_in))
        if cool_set is not None and t_in > cool_set + p.deadband_c:
            q_w -= min(p.cooling_max_w, p.cooling_gain_w_per_k * (t_in - cool_set))

        # 1R1C: C dT/dt = UA (T_out - T_in) + Q
        d_t = (p.ua_w_per_k * (t_out - t_in) + q_w) / p.capacitance_j_per_k * dt
        t_next = t_in + d_t

        heat_wh = max(q_w, 0.0) * dt / 3600.0
        cool_wh = max(-q_w, 0.0) * dt / 3600.0
        heating_runtime_s = dt if q_w > 0.0 else 0.0

        extra = dict(state.extra)
        extra["heating_wh"] = float(extra.get("heating_wh", 0.0)) + heat_wh
        extra["cooling_wh"] = float(extra.get("cooling_wh", 0.0)) + cool_wh
        extra["outdoor_c"] = t_out
        extra.setdefault("correction_offset_c", 0.0)

        return EngineState(
            timestamp_utc=weather_step.timestamp_utc,
            zone_temperatures_c={ZONE: t_next},
            thermal_state_c=t_next,
            runtime_s=state.runtime_s + heating_runtime_s,
            extra=extra,
        )

    def simulate(
        self,
        house: House,
        weather: WeatherSeries,
        params: CalibratedModel | HPXMLRef,
        window: TimeWindow,
    ) -> UtilitySeries:
        """Batch simulation over ``window`` — the non-live twin path."""
        state = self.initial_state(window.start_utc)
        points: list[EnergyPoint] = []
        for wp in weather.points:
            if wp.timestamp_utc < window.start_utc or wp.timestamp_utc >= window.end_utc:
                continue
            nxt = self.step(state, wp, AdvisoryControls())
            heat = float(nxt.extra["heating_wh"]) - float(state.extra.get("heating_wh", 0.0))
            cool = float(nxt.extra["cooling_wh"]) - float(state.extra.get("cooling_wh", 0.0))
            points.append(
                EnergyPoint(
                    start_utc=wp.timestamp_utc,
                    end_utc=wp.timestamp_utc + timedelta(seconds=self.dt_s),
                    value=round(heat + cool, 6),
                    unit="Wh",
                    estimated=False,
                )
            )
            state = nxt
        return UtilitySeries(
            fuel="electricity",
            kind="import",
            granularity="subhourly",
            points=points,
            stamp_semantics="interval_start",
            provenance=Provenance(
                source=f"{self.name}-{self.version}",
                retrieved_at_utc=window.start_utc,
                terms_tag="synthetic-spike-b",
            ),
        )

    # -- assimilation (bounded nudge, never a silent refit) ---------------

    def assimilate(
        self,
        state: EngineState,
        observed_indoor_c: float,
        weight: float = 0.5,
    ) -> EngineState:
        """Fold an observed indoor temperature into the carried state.

        The nudge is bounded: at most ``weight`` of the observed discrepancy
        is absorbed, so a single bad reading cannot run the twin away.  The
        absorbed offset is recorded in ``extra["correction_offset_c"]`` so the
        correction is auditable (LAW-12) and reversible.
        """
        if not 0.0 <= weight <= 1.0:
            raise ValueError("weight must be in [0, 1]")
        t_in = float(state.zone_temperatures_c.get(ZONE, self.params.initial_indoor_c))
        offset = weight * (observed_indoor_c - t_in)
        t_next = t_in + offset
        extra = dict(state.extra)
        extra["correction_offset_c"] = float(extra.get("correction_offset_c", 0.0)) + offset
        extra["observed_indoor_c"] = observed_indoor_c
        zone = dict(state.zone_temperatures_c)
        zone[ZONE] = t_next
        return EngineState(
            timestamp_utc=state.timestamp_utc,
            zone_temperatures_c=zone,
            thermal_state_c=t_next,
            runtime_s=state.runtime_s,
            extra=extra,
        )
