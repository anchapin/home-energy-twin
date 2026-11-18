# home-energy-twin

**A calibrated energy model of your house, then a digital twin that keeps running.**

`anchapin/home-energy-twin` (planned) · Python · License: **Apache-2.0 (planned)**
Status: **SPEC PHASE — no implementation yet.** See `specs/SPEC.md` (awaiting owner approval),
`docs/PLAN.md`, `docs/ROADMAP.md`, and `docs/RUBBER-DUCK-REVIEW.md` before writing code.

## The pitch

Most homeowners cannot answer three simple questions: *Where does my energy actually go?
Which retrofit would pay back first? Is something in my house broken right now?* The data to
answer them exists — utility bills, smart-meter intervals, thermostat runtime, weather — and
the physics to interpret it exists. They have just never been joined in one open system for a
single house.

This project does four jobs, in dependency order:

1. **Calibrate** — fit a residential energy model (HPXML) to a house's own utility data
   (monthly bills first; interval and thermostat data when available), with uncertainty
   ranges and ASHRAE Guideline 14 metrics attached to every result.
2. **Twin** — run that calibrated model 24/7 against actual and forecast weather and the
   smallest-interval utility data the user can get (API where possible, file/manual entry
   where not), correcting itself against meter residuals.
3. **Retrofit ranking** — re-simulate candidate measures against the calibrated baseline
   (BPI-2400 bounded-delta style) and rank by payback **range**, never a single-number promise.
4. **Fault detection** — flag sustained model-vs-meter residuals in physical terms
   (aux-heat overuse, baseload step-up, heating-slope change), with explanations a
   homeowner can act on.

Smart-home integration is via Home Assistant: the twin consumes thermostat / meter / CT
entities and publishes its state, forecasts, and alerts back. In v1 the twin is
**read-only and advisory — it never actuates HVAC or any other device.**

## Why this doesn't already exist

Landscape research (2026-11-18, full report in the project notes) found **no end-to-end
open-source system** doing calibration + live twin + retrofit ranking + smart-home
integration. Every candidate covers at most two of the four jobs:

- **OpenStudio-HPXML-Calibration** (NLR, 2025, BSD-3-like) calibrates to monthly bills —
  batch only, no live feed, no twin, no faults.
- **OCHRE** (NLR, BSD-3-like) is a fast controllable RC simulator — not a calibrator, and
  it ingests no specific home's meter/thermostat stream.
- **OpenStudio-HPXML / EnergyPlus** (BSD-3-like) is the authoritative batch engine — no live mode.
- **Home Assistant + EMHASS** (Apache-2.0 / MIT) do live devices and optimisation — no
  calibrated envelope/HVAC physics, no retrofit payback.
- **BEopt** does cost-optimal retrofits but is **closed source** (OSTI) — its search logic
  is reimplemented here, its code is never used.

So this project is mostly composition, plus three genuinely new pieces: a grey-box RC
calibrator for interval/thermostat data, a model-residual fault engine, and a maintained
retrofit cost/incentive database.

## The composable stack

```
                        ┌─────────────────────────────────────────────┐
  House info ─────────► │ Onboarding → HPXML generator (NEW)          │
  Green Button file ──► │ DataSource adapters (NEW parsers/glue)      │
  UtilityAPI / CMD ───► │   Green Button DMD/CMD · UtilityAPI · CSV   │
  HA meter/CT entities ►│                                             │
                        └──────────────┬──────────────────────────────┘
                                       │ UtilitySeries + WeatherSeries (UTC)
  Open-Meteo / NWS ───► WeatherProvider adapters ─────────────┐
                                                              ▼
                        ┌─────────────────────────────────────────────┐
                        │ CALIBRATION                                 │
                        │  · monthly: OpenStudio-HPXML-Calibration    │
                        │    (reuse, BPI-2400)                        │
                        │  · change-point / PRISM baseline (NEW, thin)│
                        │  · interval/RC grey-box calibrator (NEW)    │
                        │  → CalibratedModel + G14 metrics + ranges   │
                        └──────┬───────────────────────┬──────────────┘
                               ▼                       ▼
                        ┌──────────────┐      ┌───────────────────────┐
                        │ LIVE TWIN    │      │ RETROFIT RANKING      │
                        │ OCHRE step-  │      │ OpenStudio-HPXML /    │
                        │ ping harness │      │ EnergyPlus (authori-  │
                        │ (NEW glue on │      │ tative) + ResStock /  │
                        │ OCHRE reuse) │      │ BCL measures + cost   │
                        │ residuals vs │      │ DB (NEW) → payback    │
                        │ meter        │      │ ranges                │
                        └──────┬───────┘      └───────────────────────┘
                               ▼
                        ┌──────────────┐      ┌───────────────────────┐
                        │ FAULT ENGINE │─────►│ Home Assistant add-on │
                        │ (NEW — the   │      │ (NEW glue) — state,   │
                        │ novel core)  │      │ forecasts, alerts;    │
                        │              │      │ EMHASS link; advisory │
                        └──────────────┘      │ only, no actuation v1 │
                                              └───────────────────────┘
```

## Principles (enforced in `specs/SPEC.md`)

- **Ranges, never point promises.** 12 monthly bills cannot identify 20+ physical
  parameters; a Guideline-14-passing model can still be physically wrong.
- **Local-first, no telemetry by default.** Interval data reveals occupancy and vacancy.
- **Heavy engines are optional.** The core runs without EnergyPlus/OpenStudio installed;
  they sit behind adapters.
- **Synthetic ground truth first.** No redistributable real-home truth set exists, so
  validation runs on generated houses with known UA / capacitance / equipment capacity.
- **Spec before code.** `specs/` is not edited by implementation workers without owner approval.

## Where to read next

| Doc | What it is |
|---|---|
| `docs/RUBBER-DUCK-REVIEW.md` | Adversarial review: 12 issues the first pass missed, each with an owner |
| `docs/PLAN.md` | Architecture, module boundaries, build-vs-reuse, validation, privacy posture |
| `specs/SPEC.md` | Domain types, Protocols, numbered LAWS, property-test harness design |
| `docs/ROADMAP.md` | Phases 0–5 with pre-registered Go/No-go gates |
| `docs/ISSUES.md` | Starter issue list (≈17), Phase 0 dispatchable, Phase 1+ gated |
| `AGENTS.md` | Steering for coding workers on this repo |
