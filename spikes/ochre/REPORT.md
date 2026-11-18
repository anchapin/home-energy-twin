# Spike B — REPORT: OCHRE live-stepping feasibility

Issue: <https://github.com/anchapin/home-energy-twin/issues/4> · Roadmap Phase 0, Spike B (gate item 4) · RD-7 · SPEC §3

## Verdict

**No-go in this environment — OCHRE itself was not evaluated.** The single
install attempt failed before any OCHRE code could run: this VM's egress
proxy breaks TLS to PyPI (`SSLError: WRONG_VERSION_NUMBER` on
`https://pypi.org/simple/ochre/`; the same failure hits direct `curl` to
pypi.org and github.com, HTTP status 000). That is an environment finding,
not evidence about OCHRE's stepping API. Per the spike's fallback slice,
this PR delivers the harness and the fallback engine instead, and Spike B
should be re-run where PyPI is reachable (e.g. the desktop) before the
Phase 1 gate is judged on OCHRE.

**Fallback (delivered):** the live-stepping *harness shape* is proven
against a small synthetic 1R1C RC engine (`spikes/ochre/rc_engine.py`)
behind the repo's own `EngineAdapter` shape. State carry, correction, and
checkpoint/restore are all demonstrated with numbers below. If OCHRE
cannot be stepped externally when it is finally tried, this RC engine is
the Phase 2 live-twin fallback, and `spikes/ochre/ochre_adapter.py` is the
import-guarded seam where a real OCHRE mapping would land.

## Install attempt (one attempt, per brief)

- Command: `python3 -m venv /tmp/ochre-venv && /tmp/ochre-venv/bin/pip install ochre`
- Result: failed — `Could not fetch URL https://pypi.org/simple/ochre/: ...
  SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number')`,
  retries exhausted. Independent check: `curl https://pypi.org` →
  `curl: (35) OpenSSL/3.0.13: error:0A00010B:SSL routines::wrong version
  number`, `pypi_https=000` (same for github.com).
- OCHRE pinned version: **none** — no version was ever downloaded, so no
  version can be honestly pinned from this run.
- Install weight: not measurable (install never started); the empty venv
  is ~20 MB of interpreter scaffolding only.

## Demonstration (fallback RC engine, synthetic everything)

Command: `python -m spikes.ochre.harness --days 7 --step-minutes 15`

```
Spike B — external live-stepping demonstration
  engine            : rc-1r1c 0.1.0-spikeB
  step size         : 15 min (672 steps, 7.00 simulated days)
  weather           : synthetic-spike-b (synthetic, seed 20260218)
  runtime (1 week)  : 0.005 s
  state carry       : demonstrated=True replay|Δ|=3.792e-01 °C, +1 °C perturbation moves the next step by 5.1615 °C
  correction        : demonstrated=True absorbed +0.6000 °C of a +1.20 °C observation error; heating over the next 96 steps 38843.3 → 37961.0 Wh (-882.4 Wh)
  checkpoint/restore: demonstrated=True max|Δ| vs uninterrupted = 0.000e+00 °C (tolerance 1e-09 °C), checkpoint 251 B at step 336
  totals            : heating 267132.7 Wh, cooling 0.0 Wh, indoor 18.48–19.98 °C
```

(Supervisor re-run at collection, 2026-11-18; numbers reproduced exactly.
The totals are synthetic-fixture outputs of the fallback RC model, not
calibrated figures for any real house — LAW-1 ranges would apply to any
real calibration, and none is claimed here.)

Per capability:

| Capability | Status | Evidence |
|---|---|---|
| External stepping at ≤15-min steps, ≥1 simulated week | Demonstrated (fallback engine) | 672 steps × 15 min = 7.00 simulated days, weather supplied point-by-point from outside the engine |
| State carry | Demonstrated (fallback engine) | Replay divergence and +1 °C perturbation response quoted above; step N+1 state is a function of step N state |
| Correction | Demonstrated (fallback engine) | A +1.20 °C observation error is absorbed as a bounded +0.6000 °C state correction; heating over the next 96 steps changes by −882.4 Wh |
| Checkpoint / restore | Demonstrated (fallback engine) | Mid-run JSON checkpoint (251 B at step 336), restored and continued; max \|Δ\| vs the uninterrupted run = 0.000e+00 °C (tolerance 1e-09 °C) |
| OCHRE stepping (the actual spike question) | **Not evaluated** | Install blocked by the VM's TLS proxy; see above |

## Verification

- `python3 -m pytest -q` → **76 passed, 1 skipped** (the skip is the
  OCHRE-dependent test, which skips cleanly when OCHRE is absent — RD-7:
  the core suite passes with no engine installed). Supervisor re-ran at
  collection.
- `mypy --strict home_energy_twin spikes` → **Success: no issues found in
  12 source files**. (Four `mypy` errors in the pre-existing `tests/`
  files reproduce identically on clean `main` via the stash protocol —
  pre-existing, untouched by this spike.)
- `ruff check spikes tests/test_ochre_spike.py tests/conftest.py` → **All
  checks passed** (supervisor fixed the worker's remaining lint findings
  and one `NameError`: `timezone.utc` used without the import).

## What remains for a real Spike B verdict

1. Re-run the install + stepping demonstration on a machine where PyPI is
   reachable; pin the OCHRE version that installs.
2. Map OCHRE's dwelling/simulation objects onto the `EngineAdapter` shape
   in `ochre_adapter.py` (currently an import-guarded seam that raises
   `OchreUnavailableError` with the reason) and re-run
   `python -m spikes.ochre.harness --engine ochre` equivalents of the
   three capability checks.
3. Only then judge the Phase 1 gate item on OCHRE itself.
