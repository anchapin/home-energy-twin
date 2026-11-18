# Spike B — OCHRE live-stepping feasibility

Issue: <https://github.com/anchapin/home-energy-twin/issues/4>
Roadmap gate: `docs/ROADMAP.md` Phase 0, Spike B (gate item 4) · RD-7 · SPEC §3.

## Question

Can **OCHRE** be driven *externally*, one step at a time (≤15-minute steps), for
≥1 simulated week, with weather supplied from outside the engine, such that the
twin loop of Phase 2 can (i) carry state across steps, (ii) absorb a correction
from an observed reading, and (iii) checkpoint and restore state mid-run?

The harness targets the shape of `EngineAdapter` in
`home_energy_twin/protocols.py` (`simulate()` / `step()`); it does **not** invent
a new engine API.

## Scope and hard stops

* Synthetic fixtures only (LAW-11): seeded synthetic weather and a synthetic
  house. No real user, meter or thermostat data.
* No telemetry (LAW-6), no device actuation (LAW-7), no network calls in the
  delivered code.
* OCHRE is *never* a core dependency: the core package and its test suite must
  import and pass with no engine installed (RD-7). Anything OCHRE-specific lives
  behind an optional import in this spike directory only.
* Calibrated figures are reported as **ranges**, never bare points (LAW-1).

## Plan

1. Read `AGENTS.md`, `specs/SPEC.md` §2–§4, `docs/PLAN.md`, `docs/ROADMAP.md`.
2. Attempt **one** install of OCHRE into a scratch virtualenv under `/tmp`
   (never into the repo, never vendored, never a core dependency).
3. If the install succeeds: build the OCHRE-backed stepping harness and run the
   ≥1-week demonstration.
   If it fails: stop, record the failure as the blocker, and deliver a written
   No-go with a fallback.
4. Demonstrate, on whichever engine is available:
   * **state carry** — state at step N+1 depends on state at step N;
   * **correction** — an observed indoor temperature / meter value is fed back
     and the subsequent state responds;
   * **checkpoint/restore** — a mid-run checkpoint is saved, restored, and the
     continuation matches the uninterrupted run within a stated tolerance.
5. Record `REPORT.md`: pinned version, install weight (disk + time), runtime,
   step size, weather source, per-capability evidence, verdict, fallback.

## What counts as Go / No-go

**Go** — all of:

* OCHRE installed and pinned (exact version string recorded);
* ≥1 simulated week (≥ 672 steps) stepped externally at a step size ≤ 15 min
  with externally supplied synthetic weather;
* state carry, correction and checkpoint/restore each demonstrated with
  numbers (checkpoint/restore continuation within a stated tolerance, e.g.
  |Δ| ≤ 1e-9 °C on the restored trajectory, and |Δ| ≤ 0.05 °C on the
  post-correction trajectory);
* install weight and runtime recorded as ranges.

**No-go** — any of:

* the single install attempt fails (network, license, size, runtime), or the
  package is unreasonably heavy/slow to install;
* OCHRE cannot be stepped externally at ≤15-min steps with external weather;
* state cannot be carried, corrected, or checkpointed/restored;
* OCHRE would have to become a core dependency to work (breaks RD-7).

A No-go is a **successful delivery** when it quotes the exact failure and
names the fallback the live-twin loop would use instead.

## Layout

| Path | Purpose |
| --- | --- |
| `spikes/ochre/README.md` | this plan and the Go/No-go bar |
| `spikes/ochre/REPORT.md` | the verdict and the evidence |
| `spikes/ochre/weather.py` | deterministic synthetic weather generator (seeded) |
| `spikes/ochre/rc_engine.py` | pure-Python RC live-stepping engine (fallback) behind the `EngineAdapter` shape |
| `spikes/ochre/ochre_adapter.py` | optional OCHRE adapter — import guarded, skipped when OCHRE is absent |
| `spikes/ochre/harness.py` | the stepping harness (state carry / correction / checkpoint-restore) |
| `tests/test_ochre_*.py` | tests for the harness's own logic (no OCHRE needed) |
