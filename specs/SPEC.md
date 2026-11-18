# SPEC — home-energy-twin core contract

> **STATUS: GREENFIELD — AWAITING SPEC APPROVAL.**
> This spec must be approved by Alex (repo owner) before any implementation work beyond
> Phase 0 spikes begins. **Implementation workers do not edit anything under `specs/`
> without Alex's explicit approval.** If implementation reveals a spec error, file an
> issue describing it; do not silently diverge.
>
> Conventions: Python, pydantic v2 models, `mypy --strict` clean. All quantities carry
> explicit units in field names or types. All timestamps are UTC internally (LAW-5).

## 1. Purpose and scope

Define the domain types, service Protocols, invariants (LAWS), uncertainty model, and the
property-test harness for a system that (1) calibrates a residential energy model from
utility data + house information, and (2) runs the calibrated model as a 24/7 digital
twin for retrofit ranking and fault detection, integrated with Home Assistant.
v1 is read-only/advisory (LAW-7). v1 device scope: thermostat, whole-home meter,
CT monitor (RD-9).

## 2. Core domain types

All types are pydantic models in `home_energy_twin.types`. Sketches below are normative
in field semantics, not in exact Python syntax.

### 2.1 `House`
- `house_id: UUID`
- `hpxml_ref: HPXMLRef` — path/URI + SHA-256 of the canonical HPXML document describing
  the house. HPXML is the single source of truth for physical description; no parallel
  shadow schema for envelope/HVAC properties.
- `timezone: str` — IANA name (e.g. `America/New_York`); the only place local time exists.
- `location: LatLon` — used for weather lookup and recorded with its precision.
- `systems: SystemInventory` — declared heating/cooling/DHW systems per fuel, plus
  confounder flags: `has_pv`, `has_battery`, `has_ev_charging`, `dual_fuel`,
  `aux_heat`, `secondary_unmetered_fuel` (RD-5). A calibration run must refuse or
  degrade per §5 when flags imply unidentifiable configurations.
- `state_hash: str` — hash of HPXML + inventory; changes when the physical house is
  declared changed (retrofit, equipment swap). Drives model validity (LAW-4).

### 2.2 `UtilitySeries`
A time series of energy for one `(house, fuel, meter)` with explicit semantics:
- `fuel: Literal["electricity","natural_gas","oil","propane","district"]`
- `kind: Literal["import","export","net","delivered"]` — never silently mixed (RD-5).
- `granularity: Literal["billing_period","daily","hourly","subhourly"]`
- `points: list[EnergyPoint]` where `EnergyPoint = {start_utc, end_utc, value, unit,
  estimated: bool}`. Intervals are `[start, end)`; **billing periods are points with
  long, irregular `[start, end)`** — there is no separate monthly type (RD-6).
- `stamp_semantics: Literal["interval_start","interval_end"]` as reported by the source,
  normalised to `[start, end)` at ingest with the conversion recorded.
- `provenance: Provenance {source, retrieved_at_utc, terms_tag}`.

### 2.3 `WeatherSeries`
- `points: list[WeatherPoint]` — `{timestamp_utc, dry_bulb_c, humidity_ratio|rh,
  wind_mps, ghi_wm2, dni_wm2|None, dhi_wm2|None}` at a declared cadence.
- `source_kind: Literal["observed","reanalysis","forecast","tmy"]` — **TMY is forbidden
  for calibration** (LAW-10); calibration requires observed/reanalysis (AMY) sliced to
  the utility data's exact bounds.
- `station_or_cell: str`, `distance_km: float` — representativeness is recorded, not hidden.
- `provenance: Provenance` (incl. attribution string where terms require it, e.g. CC BY 4.0).

### 2.4 `CalibratedModel`
- `house_id`, `state_hash` (the house state this model describes), `engine_ref`
  (which engine + version produced/consumes it), `hpxml_ref` (calibrated HPXML).
- `parameters: dict[str, ParameterEstimate]` — every calibrated parameter is a
  `ParameterEstimate {value, low, high, unit, method}` (see §5; LAW-1).
- `metrics: CalibrationMetrics {nmbe_pct, cvrmse_pct, granularity,
  guideline14_pass: bool}` — computed and stored with **every** calibration (LAW-2).
  Reference thresholds: monthly NMBE ±5%, CV(RMSE) ≤15%; hourly ±10%, ≤30%.
  (BPI-2400 acceptance, e.g. CVRMSE ≤0.20 in the Juneau application, is recorded
  separately as `bpi2400_metrics` when that calibrator is used — the two standards are
  never conflated.)
- `calibration_window: {start_utc, end_utc}`, `data_granularity`, `calibrator_id+version`.
- `status: Literal["valid","stale","invalidated_by_retrofit","mv_in_progress"]` (LAW-4).
- `rc_parameters: RCParameters | None` — grey-box results when available:
  `{ua_w_per_k, capacitance_j_per_k, tau_hours, per-zone optional}` with ranges per §5.

### 2.5 `RetrofitScenario`
- `measure_id` (ResStock/BCL-derived identifier), `description`,
  `hpxml_transform_ref` (the modified HPXML that was simulated).
- `baseline_model_ref` — must reference a `valid` CalibratedModel (LAW-4).
- `savings: dict[fuel, ParameterEstimate]` — annual savings **as ranges** (LAW-1),
  computed as bounded delta vs the calibrated baseline (BPI-2400 discipline).
- `cost: CostEstimate {low, high, currency, source, as_of, region}` and
  `incentives: list[Incentive {program, amount_or_range, source, as_of}]`.
- `payback_years: {low, high} | None` — `None` (ranking suppressed) when calibration
  confidence or cost freshness is insufficient; suppression is a result, not an error (RD-10).

### 2.6 `FaultEvent`
- `house_id`, `detector_id+version`, `detected_at_utc`,
  `window: {start_utc, end_utc}` (the sustained-residual window that fired it).
- `signature: Literal["baseload_step","heating_slope_change","cooling_slope_change",
  "aux_heat_overuse","short_cycling","drift_suspected"]` — `drift_suspected` routes to
  recalibration, not to a fault alert (RD-4).
- `explanation: str` — physical, plain-language, names the measured quantities
  (e.g. "heating energy per degree-day up 22% over the window, driven by aux-heat runtime").
- `confidence: Literal["low","medium","high"]`, `status:
  Literal["open","acknowledged","dismissed_not_a_fault","confirmed"]` — dismissal is
  data: it feeds per-house threshold learning (RD-12c).
- FaultEvents never carry safety language (LAW-8).

## 3. Protocols

Defined in `home_energy_twin.protocols` (PEP 544 `Protocol`, runtime-checkable where
cheap). Signatures are normative in semantics.

```python
class EngineAdapter(Protocol):
    name: str; version: str
    def simulate(self, house: House, weather: WeatherSeries,
                 params: CalibratedModel | HPXMLRef,
                 window: TimeWindow) -> UtilitySeries: ...
    def step(self, state: EngineState, weather_step: WeatherPoint,
             controls: AdvisoryControls) -> EngineState: ...  # live twin; advisory only

class Calibrator(Protocol):
    id: str; version: str
    supported_granularities: set[Granularity]
    def calibrate(self, house: House, utility: list[UtilitySeries],
                  weather: WeatherSeries) -> CalibratedModel: ...

class DataSource(Protocol):
    id: str
    capabilities: DataCapabilities  # granularities, fuels, latency, history depth
    def fetch(self, house: House, window: TimeWindow) -> list[UtilitySeries]: ...

class WeatherProvider(Protocol):
    id: str
    def observed(self, location: LatLon, window: TimeWindow) -> WeatherSeries: ...
    def forecast(self, location: LatLon, horizon_hours: int) -> WeatherSeries: ...

class DeviceBridge(Protocol):
    id: str
    def read_state(self, house: House) -> DeviceState: ...   # thermostat/meter/CT only in v1
    def publish(self, house: House, twin_state: TwinState) -> None: ...  # state/alerts out; NO actuation

class CostProvider(Protocol):
    id: str
    def estimate(self, measure_id: str, house: House,
                 region: str) -> CostEstimate: ...  # always with source + as_of
```

`AdvisoryControls` may carry *suggested* setpoints for display/optimisation hand-off
(e.g. to EMHASS); no Protocol in v1 has a method that actuates a device.

## 4. Invariants — LAWS

Violating a LAW is a spec bug or a code bug; there is no third option. Property tests
(§6) exist for every LAW marked †.

- **LAW-1 † Ranges, never points.** Every user-facing calibrated parameter, savings
  figure, and payback is an interval with stated basis. A point estimate may exist
  internally only as the centre of a reported range.
- **LAW-2 † Metrics always stored.** Every `CalibratedModel` carries NMBE and CV(RMSE)
  computed against the data it was calibrated on, with granularity, whether or not it
  passes Guideline 14.
- **LAW-3 † Identifiability honesty.** A calibrator must refuse or return a
  degraded-confidence model (named reason) when the data cannot identify the requested
  parameters (e.g. hourly dynamics from monthly bills; dual-fuel split with an
  unmetered fuel). Silent best-effort output is a bug.
- **LAW-4 † Validity follows house state.** A model whose `state_hash` ≠ the house's
  current `state_hash` is not `valid`. Recording a retrofit invalidates the baseline
  until recalibration or explicit `mv_in_progress` status. Retrofit ranking and fault
  detection must not run against a non-`valid` model (M&V comparisons excepted, and
  they must say so).
- **LAW-5 † UTC inside, local at edges.** All stored/computed timestamps are UTC.
  Local-time conversion happens only at ingest/display, using the house's stored IANA
  timezone. DST-transition days are valid inputs, not errors.
- **LAW-6 No telemetry by default.** The system makes no network call for analytics,
  crash reporting, or usage statistics. External calls happen only to data/weather/
  device endpoints the user configured, and are enumerable in config.
- **LAW-7 Read-only v1.** No code path actuates HVAC or any device. `DeviceBridge.publish`
  writes twin *state* (sensors/alerts in HA), never device commands. Control requires a
  separate, owner-approved safety spec.
- **LAW-8 No safety claims.** No output (UI, report, alert, doc shipped in-product)
  claims detection of CO, gas leaks, fire, freeze protection, or life-safety conditions.
- **LAW-9 † Engine swappability.** Calibration outputs conform to §2 schemas regardless
  of engine. Swapping `EngineAdapter` implementations changes numbers within stated
  uncertainty, never the schema, field semantics, or LAW compliance.
- **LAW-10 † Actual weather for calibration.** Calibration and M&V use observed/
  reanalysis weather sliced to the data's exact period. TMY/forecast weather is for
  forward simulation and retrofit comparison only, and outputs say which was used.
- **LAW-11 Synthetic fixtures only.** Committed test data is synthetic. Real user data
  never enters the repo, fixtures, logs, or issue reports.
- **LAW-12 Provenance everywhere.** Every series and cost figure carries source +
  retrieved/as-of metadata. A number without provenance does not reach a report.

## 5. Error and uncertainty model

- **Parameter uncertainty** comes from the calibrator (posterior/bootstrap/profile
  intervals — method recorded per parameter). Minimum viable: residual-bootstrap ranges
  for the change-point baseline; profile-likelihood or Bayesian intervals for RC.
- **Propagation:** retrofit savings ranges combine calibration ranges with engine delta
  under BPI-2400 bounded movement (inputs may move only within their calibrated ranges);
  payback ranges additionally span the cost range. Ranges widen, never narrow, as they
  propagate.
- **Degradation ladder** (explicit, user-visible): thermostat+interval → interval-only →
  daily → monthly. Each step down widens ranges and may disable features (fault
  detection requires ≥ hourly residuals; RC dynamics require thermostat or subhourly data).
- **Errors are typed:** `UnidentifiableConfiguration`, `InsufficientData`,
  `ProvenanceMissing`, `StaleCostData`, `EngineUnavailable` (heavy engine absent → core
  features keep working, RD-7). No bare `ValueError` crosses a service boundary.

## 6. Property-test harness — synthetic golden houses

Design (built in Phase 1, Issue #7; extended every phase):

- **Generator:** samples house archetypes across climate zones, vintages, sizes, and
  systems, with **known ground truth**: UA (W/K), capacitance (J/K), infiltration,
  equipment capacity + efficiency curves, duct loss factor, schedules. Variants include
  confounders: PV, battery, EV, dual-fuel + aux heat (RD-5).
- **Forward models:** OCHRE adapter (fast, primary) and OpenStudio-HPXML adapter
  (cross-check on a subset) turn truth + weather into billing-period, daily, hourly,
  and thermostat-runtime series, with realistic noise, estimated reads, gaps, and
  DST/billing-period misalignment injected (RD-6).
- **Recovery tests (pre-registered tolerances, per granularity):**
  - Monthly-only: weather-normalised annual consumption (NAC) within **±10%** of truth
    on ≥90% of generated houses; UA recovered within a **factor of 1.5** *or* reported
    range covering truth on ≥90% (range honesty is the monthly bar, not point accuracy).
  - Hourly/15-min: UA point estimate within **±20%** on ≥80% of houses; G14 hourly
    metrics computed and stored (pass or fail — the harness records both).
  - Thermostat 5-min runtime: RC time constant τ within **±25%** and UA within **±15%**
    on ≥80% of favourable houses (single-stage, no confounders); confounded variants
    must trigger LAW-3 degradation or still meet range-coverage ≥90%.
  - These numbers are Phase 1 gate inputs (see ROADMAP); changing them requires owner
    approval because they are the project's public accuracy claims in embryo.
- **LAW tests:** every † LAW has at least one property test (e.g. fuzz timestamps across
  DST for LAW-5; swap engines on identical inputs for LAW-9; assert no point-only field
  exists in report schemas for LAW-1).
- **Determinism:** generator is seeded; every failure reproduces from `(seed, config)`.

## 7. Out of scope for this spec

Control/actuation (needs its own safety spec), NILM/disaggregation, multi-home/district
scale, commercial buildings, gas-interval fault claims where gas data is daily-only,
any hosted multi-tenant service.
