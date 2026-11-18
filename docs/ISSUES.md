# Starter issues — home-energy-twin

The list to file when the repo is created. Labels: **dispatchable-now** = Phase 0, work
may start immediately under plan-coder; **gated** = filed for planning, blocked on owner
approval of `specs/SPEC.md` (Phase 1+) or on the phase gate named. RD-n references are to
`docs/RUBBER-DUCK-REVIEW.md`. Every issue inherits the rules in `AGENTS.md` (specs not
editable, no workflows, synthetic fixtures only, no telemetry, no actuation).

## Phase 0 — Spec & spikes (dispatchable now)

1. **RD-1 gate: clean-room + disclosure + release/naming decision pack** — *dispatchable-now (owner task, not worker code)*
   Draft the clean-room rules into repo docs (public APIs/docs only, no employer code/data/time, no monetization), a dependency-attribution page, and the decision record the owner signs for employer disclosure, public release, and repo naming. Blocks public push.
2. **Privacy & safety posture statements + data-source NOTICE** — *dispatchable-now*
   Local-first / no-telemetry / retention posture (RD-2), v1 read-only + no-safety-claims statement (RD-3), and a `NOTICE` listing every external data source with terms + attribution (RD-11), per LAWs 6–8, 12.
3. **Spike A: OpenStudio-HPXML-Calibration hands-on** — *dispatchable-now*
   Install, calibrate a sample HPXML home against sample monthly bills; report install weight, runtime, pinned versions, output completeness vs SPEC §2.4, and Juneau-style acceptance numbers (RD-7).
4. **Spike B: OCHRE live-stepping harness feasibility** — *dispatchable-now*
   Step OCHRE externally at ≤15-min steps for ≥1 simulated week with supplied weather; demonstrate state carry, correction, and checkpoint/restore — or write the No-go + fallback (RD-7).
5. **Spike C: Green Button DMD parser on a real file** — *dispatchable-now*
   Parse one real DMD file (owner-local, never committed) into `UtilitySeries`; deliver a quirks report: timestamp basis, stamp semantics, estimated reads, gaps, billing-period shapes (RD-6, RD-12b).

## Phase 1 — Calibration MVP (gated: spec approval)

6. **Core types + Protocols + UTC/billing-period plumbing** — *gated*
   Implement SPEC §2–§3 types and Protocols; interval/billing-period arithmetic with DST, leap-day, and stamp-semantics property tests (RD-6; LAWs 5, 9).
7. **Synthetic golden-house harness v1** — *gated*
   Seeded generator with known UA/C/infiltration/capacity + forward-model series at monthly granularity, per SPEC §6; monthly recovery tests wired as the Phase 1 gate (RD-8, RD-12a).
8. **HPXML-Calibration engine adapter (out-of-process)** — *gated*
   Container/subprocess adapter behind `Calibrator`, version-pinned per Spike A findings; core suite passes with it absent (RD-7).
9. **Onboarding → HPXML generator + monthly bill ingest** — *gated*
   Homeowner-facts → HPXML with ResStock priors flagged as priors; Green Button DMD + typed/CSV monthly ingest; the manual path is the product (RD-12b).
10. **Change-point baseline calibrator + calibration report** — *gated*
    Pure-core PRISM/change-point calibrator as cross-check/fallback; report with parameter ranges, G14 metrics, and LAW-3 degradation reasons — no point-only figures (LAW-1, LAW-2).

## Phase 2 — Interval/RC + live twin (gated: Phase 1 gate)

11. **Weather adapters (Open-Meteo API + NWS) + AMY slicing** — *gated*
    `WeatherProvider`s with provenance/distance recorded; observed/reanalysis sliced to data bounds for calibration, observed+forecast for the twin; hosted API only, attribution shipped (RD-11; LAW-10).
12. **Grey-box RC calibrator (interval + thermostat runtime)** — *gated*
    UA/C/τ recovery with ranges; night-hours UA cross-check; LAW-3 degradation for PV/EV/dual-fuel confounded homes; golden-house tests per SPEC §6 (RD-5, RD-12a).
13. **Live twin loop on OCHRE + interval/thermostat ingest** — *gated*
    Step→predict→align→residual loop with checkpointing and bounded assimilation; ecobee CSV + interval Green Button ingest; v1 device scope only — thermostat, meter, CT (RD-9). Includes the RD-2 threat model + retention/downsampling controls as a merge requirement.

## Phase 3 — Retrofit ranking (gated: Phase 2 gate)

14. **Measure library v1 + cost/incentive DB v1 + payback ranking** — *gated*
    ResStock/BCL measures as HPXML transforms; BEopt-style sequential search reimplemented (never BEopt); curated cost DB with source/as-of/region and staleness rendering; ranges-only ranking with suppression path (RD-10). Includes the post-retrofit M&V hook (RD-4).

## Phase 4 — Fault detection (gated: Phase 2 gate)

15. **Residual fault engine: baseload-step + heating/cooling-slope detectors** — *gated*
    Sustained-residual detectors over twin residuals with physical explanations; declared-change windows and `drift_suspected` → recalibration routing (RD-4).
16. **Aux-heat overuse + short-cycling detectors; alert UX with dismissal learning** — *gated*
    Remaining v1 detectors; per-house threshold learning from dismissals; injected-fault golden tests against the Phase 4 gate bars; LAW-8 wording checklist (RD-12c).

## Phase 5 — Home Assistant integration (gated: Phase 4 gate + owner decision)

17. **HA add-on (advisory only) + EMHASS link** — *gated*
    Separate-container add-on publishing twin state/forecasts/alerts as HA entities and reading thermostat/meter/CT via `DeviceBridge`; EMHASS parameter hand-off; CI check that no device-write calls exist (LAW-7). Any control work requires a separate owner-approved safety spec (RD-3) and is **not** part of this issue.

---

**Count:** 17 issues — 5 dispatchable now (Phase 0), 12 gated.
**Dispatch rule:** plan-coder may pick up #1–#5 only, until the owner records SPEC
approval in-repo; #1 is an owner task that workers can draft but only the owner can close.
