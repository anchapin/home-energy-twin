# Roadmap — home-energy-twin

Gates are **pre-registered**: criteria below are the pass bars, decided before the work
runs. A gate that fails produces a No-go / iterate decision, not a quietly lowered bar —
changing a bar requires the owner's explicit approval and a note in this file.
Status rule: **Phase 1+ issues are gated on owner approval of `specs/SPEC.md`.**
Phase 0 issues are dispatchable now (see `docs/ISSUES.md`).

---

## Phase 0 — Spec & spikes
**Goal:** approve the contract, and replace the plan's load-bearing assumptions with
measured facts before any architecture hardens.

Scope:
- Owner review/approval of `specs/SPEC.md`, `docs/PLAN.md`, this roadmap (RD-1, RD-12a).
- **NLR-overlap gate (RD-1):** clean-room rules written into repo docs; owner completes
  employer outside-activity disclosure as required; owner decides public-release +
  naming. **No public push before this gate clears.**
- Spike A: OpenStudio-HPXML-Calibration hands-on — install it, calibrate a sample HPXML
  home against sample monthly bills, record install weight, runtime, versions, and
  whether outputs carry what SPEC §2.4 needs.
- Spike B: OCHRE live-stepping — drive OCHRE step-by-step with changing weather/state
  (not one batch `simulate()`), record whether state can be carried, corrected, and
  checkpointed.
- Spike C: Green Button parser — parse at least one real DMD file (owner's own, kept
  local, never committed) end-to-end into `UtilitySeries`; catalogue its timestamp,
  stamp-semantics, and estimated-read quirks (RD-6).
- Privacy/safety posture statements drafted (RD-2, RD-3) and data-source `NOTICE`
  started (RD-11).

NOT in scope: any production code, any UI, repo publication, issue dispatch beyond Phase 0.

**Go gate (all required):**
1. SPEC approved by owner (recorded in-repo).
2. RD-1 gate cleared by owner (disclosure + release/naming decision recorded).
3. Spike A: calibration completes on the sample home; Juneau-style acceptance recorded
   (bill-regression CVRMSE ≤0.20, bias ≤5% as reported by that workflow) **or** a written
   finding of what the package actually produces and a spec amendment proposal.
4. Spike B: OCHRE stepped ≥1 simulated week at ≤15-min steps with externally supplied
   weather, with state checkpoint/restore demonstrated — or a written No-go on OCHRE
   as the live engine and a fallback proposal.
5. Spike C: real Green Button file parses with zero silent timestamp assumptions;
   quirks documented.
**No-go consequence:** fix the spec/stack choice on paper and re-run only the failed spike.

## Phase 1 — Calibration MVP (monthly)
**Goal:** a homeowner with 12 monthly bills and house facts gets a calibrated model and
an honest report. Manual path is the product (RD-12b).

Scope:
- Domain types + Protocols per SPEC; UTC/billing-period plumbing (RD-6).
- Onboarding → HPXML generator from homeowner-known facts; ResStock priors fill gaps,
  flagged as priors.
- Green Button DMD + typed/CSV monthly bill ingest (`DataSource`s).
- Monthly calibration: HPXML-Calibration adapter (out-of-process, RD-7) + change-point
  baseline calibrator (pure core) as cross-check and offline fallback.
- Calibration report: parameters with ranges, G14 metrics (monthly targets NMBE ±5%,
  CV(RMSE) ≤15% — reported pass/fail, both facts shown), degradation reasons (LAW-3).
- Synthetic golden-house harness v1 (SPEC §6) with monthly recovery tests.
- Model validity bookkeeping (`state_hash`, LAW-4) — retrofit recording exists as a
  manual event even though ranking lands in Phase 3.

NOT in scope: interval/RC calibration, live twin, fault detection, HA integration,
any aggregator (UtilityAPI/CMD) integration, cost data.

**Go gate:**
1. Golden-house monthly tests pass: NAC within ±10% on ≥90% of houses; UA range covers
   truth on ≥90% (SPEC §6).
2. Core test suite passes with **no** engine binaries installed (RD-7).
3. End-to-end on the owner's own house (data stays local): report generated, ranges and
   G14 metrics present, no point-only figures anywhere in the report (LAW-1).
4. `mypy --strict` clean; property tests for LAWs 1, 2, 4, 5 green.

## Phase 2 — Interval/RC calibrator + live twin loop
**Goal:** the twin actually twins: hourly-or-better data, RC dynamics, live stepping
against real weather, residuals computed continuously.

Scope:
- Grey-box RC calibrator (interval + thermostat runtime → UA, C, τ with ranges; LAW-3
  degradation for confounded homes, RD-5).
- Interval ingest: Green Button interval files, thermostat CSV (ecobee export), HA
  bridge read path (meter/CT/thermostat entities).
- Weather adapters: Open-Meteo (hosted API only) + NWS; AMY slicing for calibration,
  observed+forecast for the twin (LAW-10).
- Twin loop on the OCHRE adapter: step, predict, align, residual; bounded assimilation;
  checkpointing; drift check → recalibration prompt (RD-4).
- **Threat model document (RD-2) — required before this phase ships**; retention/
  downsampling controls; encryption at rest.

NOT in scope: fault *alerts* (residuals are computed and stored; detectors land in
Phase 4), retrofit ranking, any control output, UtilityAPI/CMD (optional stretch only
if a Phase 0/1 finding justifies it).

**Go gate:**
1. Golden-house interval tests pass: UA within ±20% on ≥80% (hourly); τ within ±25%
   and UA within ±15% on ≥80% of favourable thermostat cases (SPEC §6).
2. Twin runs 14 consecutive days on the owner's house, unattended, with no manual
   restarts; residuals stored for ≥95% of expected intervals (gaps explained by source latency).
3. Threat model merged; retention controls demonstrably delete/downsample on command.
4. G14 hourly metrics (NMBE ±10%, CV(RMSE) ≤30%) computed and stored for the live
   calibration; pass/fail reported honestly either way.

## Phase 3 — Retrofit ranking
**Goal:** ranked measures with payback ranges a homeowner can take to a contractor.

Scope:
- Measure library v1: subset of ResStock upgrades + BCL measures as HPXML transforms
  (air sealing, attic insulation, duct sealing, HVAC upgrade, HPWH — final list set at
  phase start from SPEC-conformant transforms only).
- BEopt-style sequential search reimplemented on OpenStudio-HPXML (never BEopt itself).
- Cost DB v1: curated entries with source/as-of/region (RD-10); freshness thresholds;
  incentive entries for federal + owner's state programmes at minimum.
- Ranking UI/report: savings ranges (BPI-2400 bounded deltas), cost ranges, payback
  ranges, all inputs shown; suppression when confidence is insufficient (SPEC §2.5).
- M&V hook: recording a completed retrofit starts the post-retrofit M&V window (RD-4).

NOT in scope: contractor marketplace/lead-gen, financing, quotes, Home Energy Score
submission, any monetization (RD-1).

**Go gate:**
1. On golden houses, ranking recovers the *known* best measure (by true simulated
   payback) in the top 2 on ≥80% of test houses.
2. Every ranked output displays cost source + as-of; stale entries (>12 months or
   past a programme end date) render as stale and are excluded from headline payback.
3. Suppression path demonstrated: a deliberately under-identified house gets "cannot
   rank responsibly" with reasons, not a ranking.

## Phase 4 — Fault detection
**Goal:** sustained-residual detectors with physical explanations and dismissal learning.

Scope:
- Detectors v1: baseload step, heating-slope change, cooling-slope change, aux-heat
  overuse, short-cycling (where data granularity supports it). Duct leakage is reported
  only as a combined envelope/delivered-heat signature — never as a separated claim.
- Alert UX: explanation with measured quantities, confidence, "not a fault" dismissal
  feeding per-house threshold learning (RD-12c); declared-change windows suppress
  detectors (RD-4); `drift_suspected` routes to recalibration, not alerts.
- Detector evaluation on golden houses with injected faults.

NOT in scope: NILM/disaggregation, appliance-level claims, any safety-related detection
or wording (LAW-8), gas fault claims on daily-only gas data.

**Go gate:**
1. Injected-fault golden tests: ≥80% detection of sustained injected faults
   (baseload step ≥20%, aux-heat overuse, heating-slope change ≥20%) within 14 days
   of simulated data each.
2. False-alarm budget: ≤1 false alert per house per simulated year on unfaulted
   golden houses.
3. Every alert text passes a wording review against LAW-8 (checklist in the PR).

## Phase 5 — Home Assistant integration (advisory only)
**Goal:** the twin lives where the house's devices already are.

Scope:
- HA add-on packaging (separate container; core talks HA via API — license and process
  boundary, RD-11); twin state, forecasts, calibration status, and FaultEvents
  published as HA entities/notifications.
- DeviceBridge: read thermostat/meter/CT from HA (the only v1 classes, RD-9);
  generic annotation entities (e.g. occupancy/guests flag) allowed as inputs.
- EMHASS link: twin publishes calibrated thermal parameters/forecasts EMHASS can use;
  optimisation stays EMHASS's job.

NOT in scope: **any actuation.** Control of any device requires a separate safety spec
(fail-safe defaults, hard bounds, watchdog, override semantics — RD-3) approved by the
owner as its own phase. Matter-direct integration, cloud hosting, multi-home.

**Go gate:**
1. Add-on installs on a stock HA instance and runs 14 days alongside Phase 2's twin
   with no HA restarts attributable to it.
2. Code audit: no device-write service calls exist in the codebase (grep-able,
   CI-checked) — LAW-7 enforced mechanically.
3. Owner decision recorded on whether a control safety spec will even be commissioned;
   default is no.

---

## Cross-phase rules

- Spec changes go through the owner; bars in this file don't move silently (§ header).
- Each phase ends with the golden-house harness extended, not replaced (SPEC §6).
- A phase that misses its gate twice in a row triggers a scope review with the owner
  before a third attempt.
