# Privacy & Safety Posture — home-energy-twin

Status: Phase 0 posture statement (issue #2). Date: 2026-11-18.

This document states the project's privacy and safety posture in plain language. It is
derived only from repository docs:

| Authority | What it governs |
|---|---|
| `docs/RUBBER-DUCK-REVIEW.md` **RD-2** | Privacy: interval energy data is occupancy data (posture + mitigation list) |
| `docs/RUBBER-DUCK-REVIEW.md` **RD-3** | Safety: a twin that talks to HVAC is a safety surface |
| `docs/RUBBER-DUCK-REVIEW.md` **RD-11** | Data-licensing traps; the `NOTICE` registry |
| `docs/PLAN.md` **§6** | Privacy & security posture summary (RD-2/RD-3 are normative) |
| `specs/SPEC.md` **LAW-6** | No telemetry by default |
| `specs/SPEC.md` **LAW-7** | Read-only v1 |
| `specs/SPEC.md` **LAW-8** | No safety claims |
| `specs/SPEC.md` **LAW-12** | Provenance everywhere |

It is a **posture statement**, not an implementation and not a completed risk analysis. The
full threat model (assets, attackers, and what the design does *not* protect against) is a
**Phase 2 gate** and is explicitly **out of scope for this issue** — see RD-2, which assigns
"Phase 0 (posture) + Phase 2 (threat model)" and lists issues #2 and #11.

---

## 1. Privacy posture (RD-2, LAW-6)

### 1.1 Why this exists

RD-2: 15-minute (let alone 1-minute / CT) whole-home data reveals when occupants wake,
leave, vacation, and which large appliances run. A cloud-hosted twin is therefore a
burglary-and-surveillance database with a physics front-end. The original plan treated
"local or cloud" as interchangeable; they are not.

### 1.2 Posture commitments

These are the commitments this project makes, stated as posture. Each traces to RD-2 and
`docs/PLAN.md` §6.

1. **Local-first is the default and the reference deployment.** The user's own server or
   Home Assistant add-on. *No project-operated cloud, no hosted multi-tenant service.*
   (RD-2; `docs/PLAN.md` §6 "Local-first reference deployment; no project-operated cloud".)
2. **Cloud is an explicit opt-in deployment that the user runs themselves.** It is not a
   service this project provides. (RD-2.)
3. **No telemetry by default.** No analytics, no crash reporting phone-home, and no
   "anonymous" usage stats in v1. LAW-6 states the same normatively: the system makes no
   network call for analytics, crash reporting, or usage statistics; external calls happen
   only to data/weather/device endpoints the user configured, and are enumerable in config.
   (RD-2; LAW-6; `docs/PLAN.md` §6.)
4. **Encryption at rest** for stored utility/thermostat series. (RD-2; `docs/PLAN.md` §6
   "Encryption at rest for stored series".)
5. **Credentials live in the OS/secret store**, never in config files committed anywhere —
   UtilityAPI tokens and Home Assistant tokens are the named examples. (RD-2; `docs/PLAN.md`
   §6 "secrets in OS secret store".)
6. **Retention controls.** User-configurable raw-interval retention with automatic
   downsampling (e.g. raw 15-minute → hourly rollups after a set window). (RD-2; `docs/PLAN.md`
   §6 "retention + downsampling controls".)
7. **One-action delete-all for a house.** (RD-2; `docs/PLAN.md` §6 "one-action house
   deletion".)
8. **A threat model document is required before Phase 2 (live data).** It must cover
   assets, attackers (network intruder, malicious add-on, curious co-user), and what the
   design does *not* protect against. **This issue delivers only the posture statements
   above; the threat model itself is the Phase 2 gate and is not part of this issue.**
   (RD-2; `docs/PLAN.md` §6 "Threat model document required before the live twin (Phase 2
   gate)".)

### 1.3 What this posture does not yet claim

The threat model is not written, so this document makes no claim about attack surface,
authentication strength, key management, multi-user isolation, or the behaviour of a
malicious add-on. Those questions are open until the Phase 2 threat model lands. (RD-2.)

---

## 2. Safety posture (RD-3, LAW-7, LAW-8)

### 2.1 Why this exists

RD-3: outputs to a thermostat can freeze pipes, cook a house, short-cycle a compressor, or
defeat a heat pump's aux-heat logic in January. Separately, "fault detection" drifts easily
into safety claims the system cannot support — it cannot detect CO, gas leaks, or
electrical fire risk from meter data, and a homeowner who believes otherwise is worse off.

### 2.2 Posture commitments

1. **v1 is read-only and advisory, full stop.** The twin publishes state, forecasts and
   alerts. It never writes to a thermostat, plug, or any other actuator — this is a LAW, not
   a preference. LAW-7: no code path actuates HVAC or any device; `DeviceBridge.publish`
   writes twin *state* (sensors/alerts in Home Assistant), never device commands.
   `docs/PLAN.md` §6 is explicit that in v1 "no actuation path exists in the code, not
   merely a disabled one". (RD-3; LAW-7; `docs/PLAN.md` §6.)
2. **If the twin process dies, nothing in the house changes.** This is a design invariant
   and is tested. (RD-3.)
3. **Any future control requires a separate, owner-approved safety spec** (a Phase 5 gate):
   fail-safe defaults (device-local schedule always wins on twin failure), hard setpoint
   bounds, watchdog / dead-man behaviour, and a manual override the twin cannot fight.
   No control feature is in scope for v1 or for this issue. (RD-3; LAW-7; `docs/ISSUES.md`
   #17 "Any control work requires a separate owner-approved safety spec (RD-3) and is not
   part of this issue".)
4. **Alert copy is constrained.** Fault alerts say "pattern consistent with X, consider
   checking Y." (RD-3.)
5. **No safety claims anywhere.** LAW-8: no output (UI, report, alert, or doc shipped
   in-product) claims detection of CO, gas leaks, fire, freeze protection, or life-safety
   conditions. RD-3 restates it as: no CO, gas, fire, freeze-protection, or "protects your
   home" language in UI, README, or marketing. **This document therefore makes no safety
   claim of its own** — it states the rule, and does not assert that the product detects,
   prevents, or protects against anything. (RD-3; LAW-8; `specs/SPEC.md` §2 "FaultEvents
   never carry safety language".)

### 2.3 What this posture does not yet claim

Because v1 never actuates, no control-safety analysis exists yet; that analysis is the
separate safety spec's job at the Phase 5 gate. This document does not claim that the
system is safe to control devices, and no part of it should be read as such. (RD-3.)

---

## 3. Data-source terms and attribution (RD-11, LAW-12)

* The registry of every external data source, with its terms and attribution string, is the
  root-level **`NOTICE`** file. Adding a source requires adding its entry. (RD-11.)
* Every adapter that touches external data stamps provenance — source, retrieved-at,
  terms/attribution string — onto the series it returns. (`docs/PLAN.md` §3.)
* LAW-12: every series and cost figure carries source + retrieved/as-of metadata; a number
  without provenance does not reach a report.
* Where the repo docs do not state a source's terms or attribution string, `NOTICE` records
  "terms not stated in repo docs — verify before use" rather than inventing them.

---

## 4. Scope note

This issue (issue #2, Phase 0) delivers: this posture statement and the `NOTICE` registry.
It does **not** deliver: the Phase 2 threat model, the Phase 5 control safety spec, any
code, any telemetry, any actuation path, or any behaviour change. The posture statements
above are commitments to be implemented and enforced by later issues, each of which is
listed in `docs/ISSUES.md` (see #11 for the threat model + retention/downsampling controls
as a merge requirement, and #17 for the advisory-only add-on and EMHASS link).

Per `specs/SPEC.md` LAW-11, any test data for this work is synthetic; no real user data
enters the repo, fixtures, logs, or issue reports.
