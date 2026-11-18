# Rubber-Duck Review — adversarial pass on the original plan

Date: 2026-11-18. Scope: the concept as first pitched (calibrate from utility data → 24/7
twin → retrofit payback + fault detection + smart-home integration) plus the landscape
research verdict (compose HPXML-Calibration + OCHRE + OpenStudio-HPXML + Home Assistant;
build the RC calibrator, fault engine, and cost DB new).

The first pass answered "does it exist / what do we reuse." It did **not** ask the questions
below. Each issue: **Severity** (Blocker = must resolve before the phase it gates;
High = will bite in normal operation; Medium = real but manageable), why it bites,
mitigation, and the roadmap phase / starter issue that owns it (see `docs/ISSUES.md`).

---

## RD-1 — Employer overlap / IP: the author works at NLR, which owns the reused stack
**Severity: Blocker (Phase 0 gate).**

Why it bites: OCHRE, ResStock, OpenStudio-HPXML, and OpenStudio-HPXML-Calibration are all
NLR (formerly NREL) products, and the project author is an NLR employee working in this
exact domain. Even though every reused component is permissively licensed, a personal
project that wraps an employer's own tools invites questions no license file answers:
Was any of it written on employer time or hardware? Does it use non-public NLR data,
internal knowledge, or pre-release code? Does the name or README imply NLR endorsement?
Does outside-employment / conflict-of-interest policy require disclosure or approval?
"It was all public and BSD" is a defense you assemble *before* the repo is public, not after.

Mitigation:
- **Clean-room rule, written down in Phase 0:** only public APIs, public docs, and public
  releases of NLR tools; no NLR code beyond what the public license grants any user, no
  NLR data, no NLR time, machines, or accounts; contributions from a personal account only.
- **No monetization** of the project (also keeps Open-Meteo's non-commercial terms usable, RD-11).
- **Disclosure check is a Phase 0 gate, not a TODO:** the owner completes whatever
  outside-activity disclosure his employer requires, and decides the **public-release and
  naming gate** himself (repo name, org, any "NLR/NREL" mention beyond factual dependency
  attribution). No public push before that gate clears.
- README/docs refer to NLR tools factually as dependencies, never as affiliations.

Owner: Phase 0 · Issue #1.

## RD-2 — Privacy: interval energy data is occupancy data
**Severity: Blocker for any cloud/multi-user design; High for local-only.**

Why it bites: 15-minute (let alone 1-minute / CT) whole-home data reveals when occupants
wake, leave, vacation, and which large appliances run. A cloud-hosted twin is therefore a
burglary-and-surveillance database with a physics front-end. The original plan said "local
or cloud" as if interchangeable. They are not.

Mitigation:
- **Local-first is the default and the reference deployment** (user's server / HA add-on).
  Cloud is an explicit opt-in deployment the user runs themselves; the project ships no
  hosted multi-tenant service.
- **No telemetry by default** — no analytics, no crash reporting phone-home, no "anonymous"
  usage stats in v1. (Also a LAW in `specs/SPEC.md`.)
- Encryption at rest for stored utility/thermostat series; credentials (UtilityAPI tokens,
  HA tokens) in OS/secret-store, never in config files committed anywhere.
- **Retention controls:** user-configurable raw-interval retention with automatic
  downsampling (e.g. raw 15-min → hourly rollups after a set window), and a one-action
  delete-all for a house.
- A short **threat model document is required before Phase 2** (live data): assets,
  attackers (network intruder, malicious add-on, curious co-user), and what the design
  does *not* protect against.

Owner: Phase 0 (posture) + Phase 2 (threat model) · Issues #2, #11.

## RD-3 — Safety: a twin that talks to HVAC is a safety surface
**Severity: Blocker for control features; High for alert wording.**

Why it bites: The pitch listed smart devices as "inputs and outputs." Outputs to a
thermostat can freeze pipes, cook a house, short-cycle a compressor, or defeat a heat
pump's aux-heat logic in January. Separately, "fault detection" drifts easily into safety
claims the system cannot support — it cannot detect CO, gas leaks, or electrical fire risk
from meter data, and a homeowner who believes otherwise is worse off.

Mitigation:
- **v1 is read-only / advisory, full stop.** The twin publishes state, forecasts, and
  alerts. It never writes to a thermostat, plug, or any actuator. This is a LAW, not a preference.
- Any future control requires a **separate safety spec** (Phase 5 gate): fail-safe defaults
  (device-local schedule always wins on twin failure), hard setpoint bounds, watchdog /
  dead-man behaviour, and manual override that the twin cannot fight.
- Alert copy is constrained: fault alerts say "pattern consistent with X, consider checking
  Y." **No safety-device claims anywhere** — no CO, gas, fire, freeze-protection, or
  "protects your home" language in UI, README, or marketing.
- If the twin process dies, nothing in the house changes. Design invariant, tested.

Owner: Phase 0 (LAW) + Phase 5 (safety spec gate) · Issues #2, #17.

## RD-4 — Calibration drift and self-invalidation: the retrofit destroys the baseline
**Severity: High.**

Why it bites: Three slow poisons. (1) Occupancy and behaviour change (new baby, WFH, EV)
drift the house away from its calibration; residuals then fire "faults" that are just life.
(2) The moment a recommended retrofit is installed, the calibrated baseline is *wrong by
design* — comparing post-retrofit meter data to the old twin produces nonsense savings or
phantom faults. (3) Equipment degrades gradually, which looks exactly like drift.
The original plan treated calibration as a one-time onboarding event.

Mitigation:
- `CalibratedModel` carries validity metadata (calibration window, data granularity,
  G14 metrics, house-state hash). **A model is marked invalid after a recorded retrofit
  until recalibrated or explicitly flagged "post-retrofit M&V mode"** (LAW).
- Post-retrofit M&V is a designed loop, not an afterthought: weather-normalised
  before/after comparison (PRISM/NAC style) for a defined window, then recalibration.
  This is also the project's feedback loop (RD-12c).
- Scheduled recalibration check (e.g. each new season / N new bills) plus drift detection
  on the residual stream itself: sustained residual shift with no fault signature →
  "house may have changed — recalibrate?" prompt, distinct from fault alerts.
- Fault detectors require *sustained* residuals and are suppressed during a declared
  change window (user marks "we installed X / had guests").

Owner: Phase 1 (validity model) + Phase 4 (drift vs fault) · Issues #7, #15.

## RD-5 — Confounding loads: PV, battery, EV, dual-fuel, aux heat
**Severity: High.**

Why it bites: Naive whole-home calibration assumes meter = envelope + HVAC. Reality:
rooftop PV makes net meter data go negative at noon; a battery time-shifts load; an EV is
a 7–11 kW "fault" every night; dual-fuel heat pumps switch fuels at a balance point —
the research found hourly PRISM *substantially underestimated* heating in dual-fuel homes;
aux-heat strips masquerade as envelope failure. Calibrate through these unmodelled and the
"calibrated" UA, capacity, and payback numbers are garbage with good G14 cosmetics.

Mitigation:
- **Fuel/system segmentation is a first-class input**, not a note: onboarding must declare
  PV, battery, EV charging, secondary fuels, wood heat, and aux heat; each gets its own
  series/sub-model or an explicit exclusion window.
- Net vs gross metering is distinguished in `UtilitySeries` (import, export, net are
  different types, never silently mixed).
- Calibration refuses (loudly, with a named reason) configurations it cannot identify —
  e.g. monthly-only data on a dual-fuel home with unmetered secondary fuel gets a
  degraded-confidence result or no result, per the uncertainty model in `specs/SPEC.md`.
- Golden synthetic houses include confounded variants (PV+EV, dual-fuel) so the harness
  measures the damage instead of discovering it on users.

Owner: Phase 1 (segmentation types) + Phase 2 (RC calibrator) · Issues #6, #10.

## RD-6 — Time and data plumbing: the unglamorous project-killer
**Severity: High.**

Why it bites: More calibration projects die here than on physics.
- **DST:** local-time interval series have 23- and 25-hour days; naive resampling invents or drops an hour twice a year.
- **Billing periods ≠ calendar months:** a "January bill" may span Dec 12–Jan 11; calibrating it against January weather is a systematic error. HPXML-Calibration pairs bills with actual weather for the *billing period* — any code we write must do the same.
- **Interval alignment:** meter timestamps may be interval-start or interval-end, in utility-local time, sometimes with estimated reads and gaps.
- **Weather representativeness:** the nearest station / grid cell is not the house's microclimate; a shaded north-wall house calibrated against an airport station 20 km away inherits that bias.
- **AMY lag:** actual-weather archives for calibration arrive with delay; "calibrate on last month" may need reanalysis data, not forecast data.

Mitigation:
- **All timestamps UTC internally; local timezone is an explicit edge conversion** with the
  house's IANA tz stored on the house record (LAW). Interval semantics (start/end stamp,
  estimated flag) are fields on `UtilitySeries`, never assumptions.
- Billing-period objects are first-class: calibration consumes `(start, end, usage)` periods
  and slices weather to exactly those bounds.
- Weather provider records its source, station/grid distance, and whether data is
  observed / reanalysis / forecast on every `WeatherSeries`.
- Property tests include DST-transition days, leap day, misaligned billing periods, and
  interval-end-stamped series (see `specs/SPEC.md` harness).

Owner: Phase 0 (Green Button spike surfaces real files) + Phase 1 · Issues #4, #6.

## RD-7 — Dependency weight: EnergyPlus/OpenStudio are not `pip install` citizens
**Severity: High.**

Why it bites: The plan leans on OpenStudio-HPXML + EnergyPlus for authoritative runs and
on HPXML-Calibration for monthly calibration. Those are heavy binary stacks with their own
release cadence, platform quirks, and install pain. If the core package imports them, the
"twin that runs on your home server" becomes a container-only science project and every CI
run pays the weight. OCHRE is pip-installable Python — the exception, not the rule.

Mitigation:
- **Adapter isolation (LAW):** engines live behind `EngineAdapter` / `Calibrator`
  Protocols. The core (types, change-point baseline, RC calibrator, residuals, storage)
  imports **no** engine binaries and its test suite runs with none installed.
- Heavy engines ship as **optional extras / separate containers** (e.g. an
  `hpxml-engine` container image) invoked out-of-process; the HA add-on composes them,
  the core never links them.
- **Version pinning with an explicit support matrix:** each adapter declares the engine
  versions it was validated against; a spike in Phase 0 records the actually-tested
  versions, and upgrades are deliberate PRs, not renovate noise.

Owner: Phase 0 (spikes measure install reality) + Phase 1 · Issues #3, #5, #8.

## RD-8 — Ground truth: there is no redistributable real-home truth set
**Severity: High (validation strategy depends on it).**

Why it bites: You cannot validate parameter recovery against real houses whose true UA,
capacitance, and duct leakage nobody knows. The tempting datasets are closed: ecobee
Donate Your Data is research-agreement only; Pecan Street is licensed; utility AMI
research sets are restricted. A project that "validates" only by refitting the bills it
calibrated on is grading its own homework (equifinality guarantees a pass).

Mitigation:
- **Primary harness: synthetic golden houses.** A generator builds houses with *known*
  UA, capacitance, infiltration, equipment capacity/efficiency, and schedules; a forward
  model (OCHRE and/or EnergyPlus) produces bills/intervals/thermostat series; calibrators
  must recover parameters within pre-registered tolerances (see `specs/SPEC.md`).
- Real data is used for *plumbing and plausibility* (does the parser survive a real Green
  Button file; do residuals look sane on the author's own house), never as published
  fixtures — **fixtures are synthetic only** (also a privacy rule, RD-2).
- Published accuracy claims are always scoped: "recovers UA within X% on synthetic houses
  with Y data granularity," never "accurate for your home."

Owner: Phase 0 (harness design in spec) + Phase 1 (harness built) · Issue #7.

## RD-9 — Scope creep: smart bulbs are noise
**Severity: Medium.**

Why it bites: "Thermostat or light bulbs, etc." as twin inputs sounds harmless and eats
the project. Lighting is a tiny, behaviour-dominated load that interval meter data cannot
attribute anyway; every device class added is an API, an entity model, and a test matrix.
The twin's physics only cares about a handful of signals.

Mitigation:
- **v1 device scope is exactly three classes: thermostat, whole-home meter, CT monitor.**
  They are the only devices that constrain calibration or residuals.
- Everything else in Home Assistant is, at most, a *generic optional annotation input*
  later (e.g. an occupancy or "guests" flag entity) behind the `DeviceBridge` Protocol —
  no per-device-class code in v1.
- New device classes require a sentence in the spec naming which calibrated parameter or
  detector they improve, or they don't land.

Owner: Phase 1 (scope line) · Issue #13.

## RD-10 — Payback advice: liability and the staleness of money numbers
**Severity: High.**

Why it bites: "This heat pump pays back in 6.2 years" is financial advice wearing a lab
coat. It rests on (a) a calibrated delta with real uncertainty, (b) an installed cost that
varies 2× by contractor and region, and (c) incentives (federal/utility) that change,
expire, and have eligibility rules. The research found **no open, maintained cost/incentive
database** — so this project must curate one, and curated money data goes stale silently.
A confident wrong payback that induces a five-figure purchase is the project's worst
reputational and ethical failure mode.

Mitigation:
- **Ranges only, with the cost and incentive inputs shown** (LAW): every ranking displays
  the assumed cost range, incentive, rate, and data-as-of date next to the payback range.
- BPI-2400 bounded-delta discipline for savings: deltas computed from the calibrated
  baseline with bounded input movement, uncertainty propagated to the ranking.
- Cost DB records carry `source` + `as_of` + region; entries past a freshness threshold
  render as "stale — verify locally" instead of silently ranking.
- Standing disclaimer in UI and docs: estimates for planning, not a quote, not financial
  advice; get contractor quotes. Rankings may be *suppressed* when calibration confidence
  is too low to rank responsibly (a designed outcome, not an error).

Owner: Phase 3 · Issue #14.

## RD-11 — Data-licensing traps beyond code licenses
**Severity: Medium.**

Why it bites: The first pass checked code licenses (permissive core; BEopt closed;
OpenHEMS GPL-3.0). It under-weighted *data and service* terms:
- **Open-Meteo:** free tier is **non-commercial** use, data CC BY 4.0 (attribution owed),
  and its *server code* is AGPLv3. A monetized or commercial deployment breaks the free
  tier; embedding the server code would copyleft the deployment.
- Aggregator data (UtilityAPI/Arcadia/Bayou) comes with per-contract terms; caching,
  retention, and redistribution of a user's data through them is contractual, not free.
- TMY/AMY and ResStock-derived data carry their own attribution terms.

Mitigation:
- Core stays non-commercial-compatible by default: Open-Meteo is used **via its hosted
  API only** (never embed AGPL server code), attribution shipped in the UI/docs, and
  NWS (US public domain) is a first-class alternative provider behind the same Protocol,
  so a commercial user can swap providers without code changes.
- **GPL/AGPL components stay out of the core:** OpenHEMS is not a dependency; EMHASS
  (MIT) is integrated as a separate HA-side service, not linked code.
- A `NOTICE` / data-sources file lists every external data source, its terms, and its
  attribution string; adding a source requires adding its entry.

Owner: Phase 0 (posture) + Phase 2 (weather adapter) · Issues #2, #11.

## RD-12 — The research's three failure modes, mapped to mitigations
**Severity: Blocker-level as a set — these are why comparable projects die.**

**(a) Identifiability overclaiming.** 12 monthly numbers cannot determine 20+ parameters;
G14-passing models can be physically wrong (monthly PRISM overestimated electric heating
by ~31% vs hourly AMI in a ~2,400-home study).
→ Mitigation: ranges-never-points LAW; uncertainty model in the spec; thermostat/interval
data as the designed upgrade path that *constrains* (dynamics → capacitance, night data →
UA, runtime → capacity); golden-house recovery tolerances pre-registered per granularity.
Owner: Phase 1 · Issues #6, #7.

**(b) Onboarding friction.** Per-utility CMD registration, per-meter aggregator fees,
~13-month histories, hours-delayed intervals, thin gas data, ecobee's closed developer-key
program (new keys stopped 2024-03-28), Nest's GCP + $5 + Pub/Sub setup. If activation
requires any of that, most users never finish.
→ Mitigation: **manual path is the product, automation is progressive enhancement.**
Day-one inputs are a Green Button file upload and typed-in monthly bills; the onboarding→
HPXML generator works from address-level facts a homeowner knows; every integration
(UtilityAPI, CMD, ecobee CSV, HA) is an optional accelerator, none is a prerequisite.
Phase 0 includes a real-file Green Button spike precisely to feel this friction early.
Owner: Phase 0 + Phase 1 · Issues #4, #9.

**(c) No ground truth / no feedback loop.** Fault alerts that are really guests or a hot
tub train users to ignore all alerts; retrofit rankings that are never checked are
unfalsifiable.
→ Mitigation: conservative sustained-residual detectors with physical explanations and
**dismissal learning** (user feedback tunes thresholds per house); the post-retrofit M&V
loop (RD-4) closes the retrofit side — every completed retrofit becomes a measured
before/after the user can see, and a calibration data point.
Owner: Phase 4 (alerts) + Phase 3 (M&V hook) · Issues #14, #15, #16.

---

## Summary table

| ID | Issue | Severity | Phase | Issue # |
|---|---|---|---|---|
| RD-1 | NLR employer overlap / IP, naming & release gate | Blocker | 0 | #1 |
| RD-2 | Privacy of interval data; local-first; threat model | Blocker/High | 0, 2 | #2, #11 |
| RD-3 | Safety: v1 read-only; no safety-device claims | Blocker (control) | 0, 5 | #2, #17 |
| RD-4 | Drift & retrofit self-invalidation; M&V loop | High | 1, 4 | #7, #15 |
| RD-5 | Confounding loads (PV/battery/EV/dual-fuel/aux) | High | 1, 2 | #6, #10 |
| RD-6 | Time plumbing: DST, billing periods, AMY lag | High | 0, 1 | #4, #6 |
| RD-7 | Heavy engine deps; adapter isolation, pinning | High | 0, 1 | #3, #5, #8 |
| RD-8 | No real-home ground truth → synthetic golden houses | High | 0, 1 | #7 |
| RD-9 | Scope creep: v1 devices = thermostat + meter + CT | Medium | 1 | #13 |
| RD-10 | Payback liability; cost/incentive staleness | High | 3 | #14 |
| RD-11 | Data licensing: Open-Meteo terms, GPL/AGPL exclusion | Medium | 0, 2 | #2, #11 |
| RD-12 | Failure modes: overclaiming, friction, no feedback | Blocker (set) | 0–4 | #4, #6, #7, #9, #14–#16 |
