# Plan — home-energy-twin

Companion to `specs/SPEC.md` (the normative contract) and `docs/ROADMAP.md` (phasing and
gates). This document is descriptive: if it and the spec disagree, the spec wins.
Adversarial context: `docs/RUBBER-DUCK-REVIEW.md` (RD-n references below).

## 1. Architecture

Five layers, dependencies point downward only. The core (layers 1–3) imports no engine
binaries, no Home Assistant, and no network client for telemetry (there is none).

```
┌──────────────────────────────────────────────────────────────────┐
│ 5. PRESENTATION / HOSTING                                        │
│    Home Assistant add-on (separate container) · CLI · reports    │
├──────────────────────────────────────────────────────────────────┤
│ 4. ADAPTERS (each behind a Protocol in specs/SPEC.md)            │
│    Engines:  OCHRE · OpenStudio-HPXML/EnergyPlus (out-of-process)│
│    Calib.:   HPXML-Calibration (out-of-process)                   │
│    Data:     Green Button DMD/CMD · UtilityAPI · CSV · HA bridge │
│    Weather:  Open-Meteo API · NWS · NSRDB/EPW files              │
│    Costs:    local cost/incentive DB files                       │
├──────────────────────────────────────────────────────────────────┤
│ 3. SERVICES (new code)                                           │
│    Twin loop (step → predict → residual → assimilate)            │
│    Retrofit ranking (bounded deltas × cost ranges)               │
│    Fault engine (sustained-residual detectors)                   │
│    Recalibration / post-retrofit M&V                             │
├──────────────────────────────────────────────────────────────────┤
│ 2. DOMAIN CORE (new code, pure, fully testable offline)          │
│    Calibrators: change-point baseline · grey-box RC              │
│    Guideline 14 metrics · uncertainty propagation                │
│    Residual computation · HPXML read/write + onboarding builder  │
├──────────────────────────────────────────────────────────────────┤
│ 1. TYPES + STORAGE                                               │
│    Pydantic domain types (spec §2) · time-series store           │
│    (UTC, interval-semantics aware) · house/model registry        │
└──────────────────────────────────────────────────────────────────┘
```

### Data flow

**Calibration (batch):**
`house info → HPXML` + `UtilitySeries (billing periods or intervals)` +
`WeatherSeries (actual weather sliced to the same bounds)` → calibrator →
`CalibratedModel { parameters + ranges, G14 metrics, validity metadata }` → registry.

**Twin (continuous):**
scheduler steps the live engine (OCHRE adapter) with observed + forecast weather and the
latest device state → predicted series → aligned against incoming meter series →
residuals → (a) stored for fault engine, (b) fed to assimilation (bounded state/parameter
nudge, never a silent refit), (c) drift check → recalibration prompt when warranted.

**Retrofit (on demand):**
`CalibratedModel` + measure (HPXML transform) → authoritative engine run →
bounded delta vs baseline → × `CostProvider` ranges → ranked `RetrofitScenario` list
with inputs and as-of dates displayed. Recorded retrofit → baseline invalidated →
M&V window → recalibration (RD-4).

**Faults (continuous, batch over residuals):**
residual stream + thermostat runtime → detectors (baseload step, heating/cooling slope
change, aux-heat overuse, short-cycling) → sustained + unexplained → `FaultEvent` with a
physical explanation and a dismissal/feedback affordance.

## 2. Module boundaries (planned package layout)

```
src/home_energy_twin/
  types/          # pydantic models from spec §2 — no I/O, no engine imports
  core/
    changepoint/  # PRISM-lineage regression baseline
    rc/           # grey-box RC calibrator (Phase 2)
    metrics/      # Guideline 14 NMBE / CV(RMSE)
    residuals/    # alignment + residual computation
    hpxml/        # HPXML read/write, onboarding → HPXML generator
  services/
    twin/  retrofits/  faults/  recalibration/
  adapters/
    engines/      # ochre/, openstudio_hpxml/ (subprocess/container)
    calibrators/  # hpxml_calibration/ (subprocess/container)
    data/         # greenbutton/, utilityapi/, csv/, homeassistant/
    weather/      # openmeteo/, nws/, epw/
    costs/        # file-backed cost/incentive DB
  hosting/
    cli/          # calibrate / twin / report commands
    ha_addon/     # add-on packaging (separate process, talks core API)
tests/
  property/       # synthetic golden-house harness (spec §6)
  fixtures/       # SYNTHETIC ONLY — no real user data, ever
specs/            # not editable by implementation workers (see AGENTS.md)
```

Rules: `types/` and `core/` never import from `adapters/` or `hosting/`. Adapters are
selected by configuration, not by conditional imports in core code. Any module that needs
a heavy binary talks to it out-of-process (RD-7).

## 3. Adapter interfaces

Defined normatively as Protocols in `specs/SPEC.md` §3: `EngineAdapter`, `Calibrator`,
`DataSource`, `WeatherProvider`, `DeviceBridge`, `CostProvider`. Design notes:

- Every adapter declares `capabilities` (granularity, fuels, latency, license/terms tag)
  so services can degrade honestly instead of failing obscurely — e.g. a monthly-only
  `DataSource` cannot feed the fault engine, and the UI says so.
- Every adapter that touches external data stamps provenance (source, retrieved-at,
  terms/attribution string) onto the series it returns (RD-11).
- Engine adapters are swappable without changing the calibration output schema (LAW-9).

## 4. Build vs reuse

| Layer / need | Decision | Component | License / terms |
|---|---|---|---|
| House description | Reuse format | HPXML schema; ResStock priors for missing inputs | Open schema / NLR open |
| Monthly calibration | Reuse | OpenStudio-HPXML-Calibration (BPI-2400, v0.1, incomplete impl.) | BSD-3-like |
| Authoritative simulation | Reuse, out-of-process | OpenStudio-HPXML + EnergyPlus | BSD-3-like |
| Live twin stepping | Reuse + new harness | OCHRE (Python RC, HPXML in, HELICS-capable) | BSD-3-like |
| Change-point baseline | Build (thin) | PRISM / ASHRAE change-point regression | Literature (Fels; G14) |
| Interval/RC calibration | **Build (novel)** | Grey-box RC + runtime→UA methods | Literature (Bacher–Madsen et al.) |
| Retrofit measures | Reuse definitions | ResStock upgrades, BCL measures | Open |
| Retrofit search logic | Build | BEopt-*style* sequential search — **BEopt itself is closed source, never used** | — |
| Cost / incentive data | **Build + curate forever** | Project cost DB (source + as-of + region per entry) | Project data |
| Fault detection | **Build (novel)** | Model-residual engine; NILMTK is reference only, not a dependency | — |
| Utility data | Reuse standards + build parsers | Green Button DMD (file) / CMD (ESPI/OAuth); UtilityAPI (paid, optional) | ESPI open std; aggregator contracts |
| Weather | Reuse via API | Open-Meteo (hosted API only), NWS, NSRDB | CC BY 4.0 data, non-commercial free tier / US public domain / free key |
| Smart home | Reuse as host | Home Assistant (add-on = separate container), EMHASS (separate service) | Apache-2.0 / MIT |
| Optimisation/control | Defer | EMHASS link in Phase 5, advisory only | MIT |
| Explicitly excluded | — | BEopt (closed), OpenHEMS (GPL-3.0), Open-Meteo server code (AGPLv3), REM/Rate / Snugg Pro / Sense / Bidgely (commercial) | — |

Project license: **Apache-2.0 (planned)** — compatible with every reused component above
as integrated here (separate-process boundaries for engines and HA).

## 5. Validation strategy

1. **Synthetic golden houses first (RD-8).** A generator emits houses with known UA,
   capacitance, infiltration, equipment capacity/efficiency, schedules, and confounders
   (PV, EV, dual-fuel variants). A forward model produces bills / intervals / thermostat
   series. Calibrators are scored on *parameter recovery* against the known truth, with
   tolerances pre-registered per data granularity in `specs/SPEC.md` §6 — not on refit
   error alone (equifinality makes refit error a vanity metric).
2. **Guideline 14 as a reporting floor, not a truth claim.** Every calibration stores
   NMBE and CV(RMSE) (monthly targets: ±5% / ≤15%; hourly: ±10% / ≤30%) and reports when
   it passes G14 yet has wide parameter ranges — both facts, side by side.
3. **Property tests** for the plumbing that actually kills projects (RD-6): DST days,
   leap day, billing-period slicing, interval start/end stamps, unit conversions.
4. **Real data** (author's own house, volunteers') is for parser robustness and
   plausibility only; it never becomes a committed fixture.
5. **Post-retrofit M&V** closes the loop in production (RD-4/RD-12c): weather-normalised
   before/after on every recorded retrofit.

## 6. Privacy & security posture (summary; RD-2/RD-3 are normative)

- Local-first reference deployment; no project-operated cloud; no telemetry by default.
- Encryption at rest for stored series; secrets in OS secret store; retention +
  downsampling controls; one-action house deletion.
- v1 read-only: no actuation path exists in the code, not merely a disabled one.
- Threat model document required before the live twin (Phase 2 gate).
- Clean-room / employer-overlap rules and the owner's public-release gate: Phase 0 (RD-1).

## 7. Phasing summary

Phase 0 Spec & spikes → Phase 1 Calibration MVP (monthly) → Phase 2 Interval/RC + live
twin → Phase 3 Retrofit ranking → Phase 4 Fault detection → Phase 5 HA integration
(advisory only). Gates and numbers: `docs/ROADMAP.md`. Nothing in Phase 1+ is dispatched
until the owner approves `specs/SPEC.md` (see `AGENTS.md`).
