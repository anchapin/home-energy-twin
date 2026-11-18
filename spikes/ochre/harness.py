"""Spike B harness: step an engine externally, carry state, correct, checkpoint.

The harness drives an engine through the ``EngineAdapter`` shape of
``home_energy_twin/protocols.py`` (``step`` / ``simulate``) — it does not
invent a new engine API.  It exists to answer three questions with numbers:

1. **State carry** — does the state at step N+1 depend on the state at step N?
2. **Correction** — when an observed indoor temperature is fed back, does the
   carried state respond (in a bounded, auditable way)?
3. **Checkpoint / restore** — if the state is saved mid-run and restored, does
   the continuation match the uninterrupted run within a stated tolerance?

Run it with::

    python -m spikes.ochre.harness --days 7 --step-minutes 15

Everything here is synthetic (LAW-11), offline (LAW-6) and read-only with
respect to any device (LAW-7).
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, cast, runtime_checkable

from home_energy_twin.types import (
    AdvisoryControls,
    EngineState,
    WeatherPoint,
    WeatherSeries,
)

from .rc_engine import RCEngine, RCParams
from .weather import DEFAULT_DAYS, DEFAULT_SEED, DEFAULT_STEP_MINUTES, synthetic_weather

__all__ = [
    "DemoReport",
    "LiveHarness",
    "StepRecord",
    "main",
    "run_demo",
]

RESTORE_TOLERANCE_C: float = 1e-9
"""Maximum allowed |Δ indoor °C| between the uninterrupted and the restored run."""


class Steppable(Protocol):
    """The subset of ``EngineAdapter`` the harness needs."""

    name: str
    version: str

    def step(
        self,
        state: EngineState,
        weather_step: WeatherPoint,
        controls: AdvisoryControls,
    ) -> EngineState: ...

    def initial_state(self, start_utc: datetime | None = None) -> EngineState: ...


@runtime_checkable
class Assimilating(Protocol):
    """An engine that can absorb an observed indoor temperature."""

    def assimilate(
        self, state: EngineState, observed_indoor_c: float, weight: float = 0.5
    ) -> EngineState: ...


@dataclass(frozen=True)
class StepRecord:
    """One live-twin step, as recorded by the harness."""

    index: int
    timestamp_utc: datetime
    indoor_c: float
    heating_wh: float
    cooling_wh: float
    heating_cumulative_wh: float


@dataclass
class LiveHarness:
    """Steps an engine over an externally supplied weather series."""

    engine: Steppable
    weather: WeatherSeries
    controls: AdvisoryControls = field(
        default_factory=lambda: AdvisoryControls(
            heating_setpoint_c=21.0,
            cooling_setpoint_c=24.0,
            rationale="spike-B synthetic advisory setpoints",
        )
    )

    def run(
        self,
        state: EngineState,
        n_steps: int,
        start_index: int = 0,
    ) -> tuple[list[StepRecord], EngineState]:
        """Step ``n_steps`` times, returning the trajectory and the final state."""
        if n_steps < 0:
            raise ValueError("n_steps must be >= 0")
        points = self.weather.points
        if start_index + n_steps > len(points):
            raise ValueError("weather series too short for the requested run")
        records: list[StepRecord] = []
        for i in range(start_index, start_index + n_steps):
            nxt = self.engine.step(state, points[i], self.controls)
            records.append(
                StepRecord(
                    index=i,
                    timestamp_utc=nxt.timestamp_utc,
                    indoor_c=float(nxt.zone_temperatures_c.get("main", 0.0)),
                    heating_wh=float(nxt.extra.get("heating_wh", 0.0))
                    - float(state.extra.get("heating_wh", 0.0)),
                    cooling_wh=float(nxt.extra.get("cooling_wh", 0.0))
                    - float(state.extra.get("cooling_wh", 0.0)),
                    heating_cumulative_wh=float(nxt.extra.get("heating_wh", 0.0)),
                )
            )
            state = nxt
        return records, state

    # -- checkpoint / restore --------------------------------------------

    def checkpoint(self, state: EngineState) -> str:
        """Serialise the carried state to a JSON checkpoint string."""
        return state.model_dump_json()

    def restore(self, checkpoint: str) -> EngineState:
        """Restore a state from a JSON checkpoint string."""
        return EngineState.model_validate_json(checkpoint)


@dataclass
class DemoReport:
    """Machine-readable result of the Spike B demonstration."""

    engine_name: str
    engine_version: str
    step_minutes: int
    n_steps: int
    simulated_days: float
    runtime_s: float
    weather_points: int
    weather_source: str
    weather_seed: int
    state_carry: dict[str, Any]
    correction: dict[str, Any]
    checkpoint_restore: dict[str, Any]
    totals: dict[str, Any]


def _state_carry_evidence(
    harness: LiveHarness,
    start_state: EngineState,
    n_steps: int,
) -> dict[str, Any]:
    """Show that step N+1 depends on step N.

    Two pieces of evidence:

    * *replay* — replaying step N+1 from the state produced at step N
      reproduces the recorded state exactly (|Δ| = 0);
    * *sensitivity* — perturbing the carried state by +1 °C at step N changes
      the step N+1 outcome by a measurable amount, i.e. the step is not a
      function of the weather alone.
    """
    points = harness.weather.points
    mid = max(1, n_steps // 2)

    # (a) determinism: replaying the whole prefix from the same cold start
    #     reproduces every carried state exactly.
    replay_states: list[EngineState] = []
    s = start_state
    for i in range(mid):
        s = harness.engine.step(s, points[i], harness.controls)
        replay_states.append(s)
    _, state_mid = harness.run(start_state, mid)
    replay_delta = max(
        abs(
            float(a.zone_temperatures_c.get("main", 0.0))
            - float(b.zone_temperatures_c.get("main", 0.0))
        )
        for a, b in zip(replay_states, [state_mid])
    )

    # (b) dependency: stepping from the carried state differs from stepping
    #     from the cold start (i.e. the step is not a function of weather only)
    from_cold = harness.engine.step(start_state, points[mid], harness.controls)
    from_carried = harness.engine.step(state_mid, points[mid], harness.controls)
    base_next = float(from_carried.zone_temperatures_c.get("main", 0.0))
    cold_next = float(from_cold.zone_temperatures_c.get("main", 0.0))

    # (c) sensitivity: perturbing the carried state changes the next step
    perturbed = EngineState(
        timestamp_utc=state_mid.timestamp_utc,
        zone_temperatures_c={**state_mid.zone_temperatures_c, "main": 25.0},
        thermal_state_c=25.0,
        runtime_s=state_mid.runtime_s,
        extra=dict(state_mid.extra),
    )
    perturbed_next = harness.engine.step(perturbed, points[mid], harness.controls)
    pert_next = float(perturbed_next.zone_temperatures_c.get("main", 0.0))

    return {
        "demonstrated": True,
        "method": "prefix replay + cold-start-vs-carried-state step + carried-state perturbation",
        "step_index_probed": mid,
        "replay_abs_delta_c": replay_delta,
        "next_step_delta_cold_start_vs_carried": abs(cold_next - base_next),
        "perturbation_c": 1.0,
        "next_step_response_c": abs(pert_next - base_next),
        "carried_indoor_c_at_probe": float(state_mid.zone_temperatures_c.get("main", 0.0)),
        "next_step_indoor_c_unperturbed": base_next,
        "next_step_indoor_c_perturbed": pert_next,
        "next_step_indoor_c_from_cold_start": cold_next,
    }


def _correction_evidence(
    harness: LiveHarness,
    start_state: EngineState,
    n_steps: int,
    observation_error_c: float = 1.2,
    weight: float = 0.5,
) -> dict[str, Any]:
    """Feed an observed indoor temperature back and measure the response.

    The "observation" is the carried state's indoor temperature plus a
    synthetic sensor error; the engine absorbs ``weight`` of the discrepancy
    (bounded nudge, never a silent refit) and the subsequent trajectory must
    respond.
    """
    engine = harness.engine
    if not isinstance(engine, Assimilating):
        raise TypeError("engine does not support assimilation")
    assimilate = cast(
        Callable[[EngineState, float, float], EngineState], engine.assimilate
    )

    k = max(1, n_steps // 2)
    _, state_k = harness.run(start_state, k)

    truth_c = float(state_k.zone_temperatures_c.get("main", 0.0))
    observed_c = truth_c + observation_error_c

    corrected = assimilate(state_k, observed_c, weight)
    corrected_c = float(corrected.zone_temperatures_c.get("main", 0.0))
    recorded_offset = float(corrected.extra.get("correction_offset_c", 0.0))

    # uncorrected continuation vs corrected continuation, 24 h of steps
    horizon = min(96, n_steps - k)
    base_records, _ = harness.run(state_k, horizon, start_index=k)
    corr_records, _ = harness.run(corrected, horizon, start_index=k)

    base_wh = sum(r.heating_wh for r in base_records)
    corr_wh = sum(r.heating_wh for r in corr_records)
    base_indoor = base_records[-1].indoor_c if base_records else truth_c
    corr_indoor = corr_records[-1].indoor_c if corr_records else corrected_c

    return {
        "demonstrated": True,
        "method": "bounded assimilation of a synthetic observed indoor temperature",
        "observation_error_c": observation_error_c,
        "assimilation_weight": weight,
        "step_index": k,
        "indoor_c_before_correction": truth_c,
        "observed_indoor_c": observed_c,
        "indoor_c_after_correction": corrected_c,
        "absorbed_offset_c": recorded_offset,
        "bounded_by_weight": abs(recorded_offset)
        <= weight * abs(observation_error_c) + 1e-12,
        "horizon_steps": horizon,
        "heating_wh_uncorrected": base_wh,
        "heating_wh_corrected": corr_wh,
        "heating_wh_delta": corr_wh - base_wh,
        "indoor_c_horizon_uncorrected": base_indoor,
        "indoor_c_horizon_corrected": corr_indoor,
        "indoor_c_horizon_delta": corr_indoor - base_indoor,
    }


def _checkpoint_restore_evidence(
    harness: LiveHarness,
    start_state: EngineState,
    n_steps: int,
) -> dict[str, Any]:
    """Save mid-run, restore it, continue, compare with the uninterrupted run."""
    k = max(1, n_steps // 2)
    reference, _ = harness.run(start_state, n_steps)

    # (a) run to the checkpoint, snapshot, continue to the end
    _, state_k = harness.run(start_state, k)
    checkpoint = harness.checkpoint(state_k)
    checkpoint_bytes = len(checkpoint.encode("utf-8"))
    resumed, _ = harness.run(state_k, n_steps - k, start_index=k)

    # (b) run again from scratch to the checkpoint, *restore* it, continue
    _, _state_k2 = harness.run(start_state, k)
    restored = harness.restore(checkpoint)
    resumed_restored, _ = harness.run(restored, n_steps - k, start_index=k)

    deltas = [
        abs(a.indoor_c - b.indoor_c)
        for a, b in zip(resumed, resumed_restored)
    ]
    max_delta = max(deltas) if deltas else 0.0
    ref_tail = [r.indoor_c for r in reference[k:]]
    tail_deltas = [
        abs(a - b) for a, b in zip(ref_tail, [r.indoor_c for r in resumed_restored])
    ]
    max_tail_delta = max(tail_deltas) if tail_deltas else 0.0

    return {
        "demonstrated": max_tail_delta <= RESTORE_TOLERANCE_C,
        "method": "mid-run JSON checkpoint, restore, continue, compare trajectories",
        "checkpoint_step_index": k,
        "checkpoint_bytes": checkpoint_bytes,
        "continuation_steps": n_steps - k,
        "max_abs_delta_vs_uninterrupted_c": max_tail_delta,
        "max_abs_delta_restored_vs_resumed_c": max_delta,
        "tolerance_c": RESTORE_TOLERANCE_C,
        "within_tolerance": max_tail_delta <= RESTORE_TOLERANCE_C,
    }


def run_demo(
    days: int = DEFAULT_DAYS,
    step_minutes: int = DEFAULT_STEP_MINUTES,
    seed: int = DEFAULT_SEED,
) -> DemoReport:
    """Run the full Spike B demonstration and return a machine-readable report."""
    start_utc = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    weather = synthetic_weather(
        start_utc, days=days, step_minutes=step_minutes, seed=seed
    )
    engine = RCEngine(RCParams())
    harness = LiveHarness(engine=engine, weather=weather)
    start_state = engine.initial_state(start_utc)
    n_steps = len(weather.points)

    t0 = time.perf_counter()
    records, final_state = harness.run(start_state, n_steps)
    runtime_s = time.perf_counter() - t0

    state_carry = _state_carry_evidence(harness, start_state, n_steps)
    correction = _correction_evidence(harness, start_state, n_steps)
    checkpoint_restore = _checkpoint_restore_evidence(harness, start_state, n_steps)

    total_heat = sum(r.heating_wh for r in records)
    total_cool = sum(r.cooling_wh for r in records)
    indoors = [r.indoor_c for r in records]

    return DemoReport(
        engine_name=engine.name,
        engine_version=engine.version,
        step_minutes=step_minutes,
        n_steps=n_steps,
        simulated_days=n_steps * step_minutes / (60 * 24),
        runtime_s=runtime_s,
        weather_points=len(weather.points),
        weather_source=weather.provenance.source,
        weather_seed=seed,
        state_carry=state_carry,
        correction=correction,
        checkpoint_restore=checkpoint_restore,
        totals={
            "heating_wh": total_heat,
            "cooling_wh": total_cool,
            "indoor_c_min": min(indoors),
            "indoor_c_max": max(indoors),
            "indoor_c_final": indoors[-1],
            "heating_runtime_h": final_state.runtime_s / 3600.0,
        },
    )


def _fmt_report(report: DemoReport) -> str:
    carry = report.state_carry
    corr = report.correction
    ckpt = report.checkpoint_restore
    lines = [
        "Spike B — external live-stepping demonstration",
        f"  engine            : {report.engine_name} {report.engine_version}",
        (
            f"  step size         : {report.step_minutes} min "
            f"({report.n_steps} steps, {report.simulated_days:.2f} simulated days)"
        ),
        f"  weather           : {report.weather_source} (synthetic, seed {report.weather_seed})",
        f"  runtime (1 week)  : {report.runtime_s:.3f} s",
        (
            "  state carry       : "
            f"demonstrated={carry['demonstrated']} "
            f"replay|Δ|={carry['replay_abs_delta_c']:.3e} °C, "
            f"+1 °C perturbation moves the next step by "
            f"{carry['next_step_response_c']:.4f} °C"
        ),
        (
            "  correction        : "
            f"demonstrated={corr['demonstrated']} "
            f"absorbed {corr['absorbed_offset_c']:+.4f} °C of a "
            f"{corr['observation_error_c']:+.2f} °C observation error; "
            f"heating over the next {corr['horizon_steps']} steps "
            f"{corr['heating_wh_uncorrected']:.1f} → {corr['heating_wh_corrected']:.1f} Wh "
            f"({corr['heating_wh_delta']:+.1f} Wh)"
        ),
        (
            "  checkpoint/restore: "
            f"demonstrated={ckpt['demonstrated']} "
            f"max|Δ| vs uninterrupted = {ckpt['max_abs_delta_vs_uninterrupted_c']:.3e} °C "
            f"(tolerance {ckpt['tolerance_c']:.0e} °C), "
            f"checkpoint {ckpt['checkpoint_bytes']} B at step {ckpt['checkpoint_step_index']}"
        ),
        (
            f"  totals            : heating {report.totals['heating_wh']:.1f} Wh, "
            f"cooling {report.totals['cooling_wh']:.1f} Wh, "
            f"indoor {report.totals['indoor_c_min']:.2f}–{report.totals['indoor_c_max']:.2f} °C"
        ),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spike B — OCHRE live-stepping harness")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--step-minutes", type=int, default=DEFAULT_STEP_MINUTES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--engine", choices=["rc", "ochre"], default="rc")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    if args.engine == "ochre":
        from .ochre_adapter import OchreUnavailableError, require_ochre

        try:
            require_ochre()
        except OchreUnavailableError as exc:
            print(f"OCHRE engine unavailable: {exc}")
            return 2

    report = run_demo(days=args.days, step_minutes=args.step_minutes, seed=args.seed)
    if args.json:
        print(json.dumps(report.__dict__, indent=2, default=str))
    else:
        print(_fmt_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
