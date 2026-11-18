# AGENTS.md — steering for workers on home-energy-twin

Read `specs/SPEC.md`, `docs/PLAN.md`, and `docs/ROADMAP.md` before starting any issue.
This file is the short, binding version.

## Status gate — read first

**SPEC APPROVED by Alex on 2026-11-18.**
Phase 0 and Phase 1 issues are dispatchable (see `docs/ISSUES.md`). Phase 2+ issues
stay gated on their roadmap phase gates (Phase 1 gate before Phase 2 work, and so on).
Do not "get ahead" on a later phase.

## Hard rules

- **Spec-first.** `specs/` is **not editable** by implementation workers. If the spec
  looks wrong, file an issue with the evidence and stop that line of work. Never
  silently diverge from a LAW in `specs/SPEC.md` §4.
- **Never touch `.github/workflows/`** or any CI/workflow file, for any reason.
- **No telemetry.** No analytics, crash-reporting, or phone-home code, endpoints, or
  dependencies. (LAW-6.)
- **No real user data in fixtures, tests, logs, or issues. Synthetic data only.** (LAW-11.)
  If a task seems to need a real file (e.g. a Green Button spike), it stays on the
  owner's machine, uncommitted, and the deliverable is a written quirks report.
- **No actuation.** v1 is read-only/advisory. Do not add code that writes to a
  thermostat or any device, and do not add safety-related claims or wording. (LAW-7, LAW-8.)
- **No closed/copyleft contamination.** Do not use BEopt code (closed source), OpenHEMS
  (GPL-3.0), or Open-Meteo server code (AGPLv3). Open-Meteo is called via its hosted
  API only. When in doubt about a dependency's license, stop and flag it in the issue.
- **Heavy engines stay out of the core.** EnergyPlus/OpenStudio/HPXML-Calibration are
  invoked out-of-process behind adapters. Core code and core tests must run with none
  of them installed.
- **Ranges, not points.** Any PR that surfaces a calibrated parameter, savings figure,
  or payback as a bare number is wrong by construction. (LAW-1.)

## Conventions

- Python, **pydantic v2** for all domain types (per `specs/SPEC.md` §2), **`mypy --strict`
  must pass** on touched packages.
- All timestamps UTC internally; local time only at ingest/display edges, using the
  house's stored IANA timezone. (LAW-5.)
- Protocols (PEP 544) for every adapter boundary in SPEC §3; no conditional imports of
  adapters inside core code — selection is by configuration.
- Errors crossing a service boundary use the typed errors in SPEC §5.
- Small, single-purpose PRs against `main`. One issue per PR; reference the issue and
  the SPEC section / LAW your change implements.

## Testing

- Test command (placeholder until the package lands): `python3 -m pytest -q`
- **Property tests are required for calibration code** — new calibrators, metrics, and
  residual code land with Hypothesis-style property tests and, where applicable,
  golden-house recovery tests per `specs/SPEC.md` §6. A calibration PR without them
  does not merge.
- Plumbing changes (ingest, timestamps, resampling) need the edge-case tests named in
  SPEC §6 / RD-6: DST spring/fall, leap day, billing-period slicing, interval
  start/end stamp semantics.
- Tests must pass **without network access and without engine binaries installed**,
  except tests explicitly marked as adapter integration tests.

## Domain reminders (the easy ways to be wrong)

- Guideline 14 thresholds: monthly NMBE ±5% / CV(RMSE) ≤15%; hourly ±10% / ≤30%.
  BPI-2400 has its own acceptance set — never conflate them.
- TMY weather is never used for calibration. Actual (AMY) weather, sliced to the
  billing/data period, is. (LAW-10.)
- Net, import, and export meter series are different things. PV, battery, EV, and
  dual-fuel homes degrade calibration confidence by design (LAW-3), not by accident.
- A model is invalid after a retrofit until recalibrated or in M&V mode. (LAW-4.)
