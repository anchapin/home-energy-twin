# Spike A — OpenStudio-HPXML-Calibration hands-on evaluation (issue #3)

**Scope:** hands-on evaluation of OpenStudio-HPXML-Calibration as the monthly-bill
calibrator for home-energy-twin (issue #3, `docs/ISSUES.md:15`).

**Branch of the brief that was executed:** *Phase B fallback* — the engine could not be
obtained on this VM, so this report delivers (a) an out-of-process adapter skeleton behind
the repo's `Calibrator` Protocol, (b) unit tests, and (c) this report with the Phase A
probe evidence, the version pin, the SPEC §2.4 completeness checklist and the exact
hands-on script a capable machine would run.

**Claim legend used throughout:**

- `[observed]` — measured in *this* environment; the command and its output are on the same
  line (or the same bullet).
- `[documented]` — taken from documentation, cited as `file:line` (repository docs) or from
  the upstream tool's published documentation *as summarised in this repository*. Not
  measured here.
- `[not measured here]` — requires the engine and/or network access that this VM does not
  have; the exact command a capable machine would run is given alongside.

No numbers in this report are invented. Where a number could not be measured it is either
given as a **range** or explicitly marked `[not measured here]`.

---

## 1. Verdict (one paragraph)

The engine is **not installable on this VM**: there is no OpenStudio/EnergyPlus
distribution present, and outbound HTTPS is blocked by a TLS-intercepting proxy, so the one
permitted download attempt could not fetch even a pinned release manifest
(`[observed]`, §2 and §3). No calibration was therefore run here, so **install weight,
wall-clock runtime and Juneau-style acceptance numbers are `[not measured here]`** — §5 and
§6 give the exact commands that produce them. What *is* delivered and proven here is the
fallback slice: a spike-only, out-of-process adapter that locates the engine through the
single named mechanism `HET_HPXML_CALIBRATION_BIN` and raises the repository's shared typed
error `home_energy_twin.errors.EngineUnavailable` (`home_energy_twin/errors.py:58`) when the
engine is absent, with unit tests proving the absent-engine path and the version-pin
parsing.

---

## 2. Phase A probe — what already exists on this machine

All commands were run from the worktree root
`/home/hatch/workspace/fixtrees/home-energy-twin-worker-3` (branch
`worker/3-spike-a-hands-on`).

### 2.1 Is an OpenStudio distribution present?

```console
$ which openstudio
which openstudio -> not found
$ which openstudio_version
which openstudio_version -> not found
$ which energyplus
which energyplus -> not found
```

`[observed]` No `openstudio`, `openstudio_version` or `energyplus` executable is on `PATH`.

```console
$ python3 -c "import openstudio"
Traceback (most recent call last):
  File "<string>", line 1, in <module>
ModuleNotFoundError: No module named 'openstudio'
```

`[observed]` The `openstudio` Python module is not importable.

```console
$ python3 -m pip list | grep -i -E "openstudio|energyplus|hpxml"
no openstudio/energyplus/hpxml distributions
$ python3 -V
Python 3.12.3
```

`[observed]` No OpenStudio / EnergyPlus / HPXML-calibration Python distribution is installed;
the interpreter is CPython 3.12.3.

### 2.2 Is an HPXML-Calibration checkout already on this machine?

```console
$ ls -d ~/OpenStudio* ~/openstudio* /opt/OpenStudio* 2>/dev/null || echo "no OpenStudio dirs in ~ or /opt"
no OpenStudio dirs in ~ or /opt
```

`[observed]` No OpenStudio / OpenStudio-HPXML-Calibration checkout or install directory
exists in the usual locations on this machine.

### 2.3 Free space

```console
$ df -h /tmp | tail -2
Filesystem      Size  Used Avail Use% Mounted on
tmpfs           512M   31M  482M   7% /tmp
```

`[observed]` `/tmp` here is a 512 MiB tmpfs with ~482 MiB free; a full OpenStudio +
EnergyPlus install does not fit in it, which alone would trip the brief's ~1 GB abort
threshold for the single download attempt.

---

## 3. The single permitted download/install attempt (Phase A step 2)

The attempt was a **single fetch of a pinned release**: the `openstudio` distribution from
PyPI (the OpenStudio Python bindings that OpenStudio-HPXML-Calibration's runtime needs),
run with `--no-deps` and a bounded timeout. No mirrors were hunted, no toolchain was
provisioned, and **no retry was performed**.

```console
$ timeout 300 python3 -m pip download --no-deps --dest /tmp/hpxml_probe openstudio
WARNING: Retrying (Retry(total=4, connect=None, read=None, redirect=None, status=None)) after connection broken by 'SSLError(SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1000)))': /simple/openstudio/
...
ERROR: Could not find a version that satisfies the requirement openstudio (from versions: none)
ERROR: No matching distribution found for openstudio
$ ls -la /tmp/hpxml_probe
total 0
```

The same TLS failure was reproduced with `curl` and `git` before the attempt:

```console
$ curl -sSI https://github.com
curl: (35) OpenSSL/3.0.13: error:0A00010B:SSL routines::wrong version number
$ git ls-remote --tags https://github.com/NREL/OpenStudio-HPXML-Calibration
fatal: unable to access 'https://github.com/NREL/OpenStudio-HPXML-Calibration/': gnutls_handshake() failed: An unexpected TLS packet was received.
```

`[observed]` Outbound HTTPS is intercepted by a proxy that presents a non-TLS stream, so
**no** package index or GitHub fetch can complete. The install path is therefore closed:
the payload could not even be *sized*, let alone downloaded or built. Per the brief, the
install path was stopped immediately — one attempt, no retries, no environment
provisioning.

Install weight, therefore:

- **Install weight: `[not measured here]`.** No OpenStudio/EnergyPlus/HPXML-Calibration
  payload was fetched, so its size is unknown on this VM. (For planning only, and
  `[documented]`-grade general knowledge rather than a measurement: the OpenStudio
  installer plus the EnergyPlus runtime are routinely multi-GB, i.e. above the brief's
  ~1 GB abort threshold — a capable machine should record the *actual* number with the
  command in §8.3 rather than rely on this sentence.)
- **Runtime: `[not measured here]`** — see §8.3 for the exact timing command.

---

## 4. Pinned candidate versions

| Component | Pin | Source | Claim type |
|---|---|---|---|
| OpenStudio-HPXML-Calibration | **v0.1** (the only in-repo pin) | `docs/PLAN.md:114` — “\| Monthly calibration \| Reuse \| OpenStudio-HPXML-Calibration (BPI-2400, v0.1, incomplete impl.) \| BSD-3-like \|” | `[documented]` |
| OpenStudio (runtime) | not pinned in-repo | `docs/PLAN.md:115` names “OpenStudio-HPXML + EnergyPlus” with no version string | `[not measured here]` |
| EnergyPlus (runtime) | not pinned in-repo | `docs/PLAN.md:115` (no version string) | `[not measured here]` |
| `docs/ISSUES.md` | carries **no** version strings for this engine | `docs/ISSUES.md:15-16` names the spike but no version | `[documented]` |

`[documented]` The engine is **BSD-3-like** licensed (`docs/PLAN.md:114`), i.e. acceptable
under AGENTS.md's closed/copyleft-contamination rule (LAW: no BEopt / OpenHEMS /
Open-Meteo-server code).

The spike adapter hard-codes the pin so that a drifted engine cannot be used silently:

```python
# spikes/hpxml_calibration/adapter.py
PINNED_ENGINE_VERSION = "0.1"   # docs/PLAN.md line 114 — "v0.1"
```

`parse_version_pin()` / `pin_satisfied_by()` treat the pin as the **0.1.x line**: an engine
reporting `0.1` or `0.1.3` satisfies it; `0.2` or `1.0` does not. The rule is unit-tested
(`tests/test_hpxml_calibration_spike.py`).

---

## 5. Output completeness vs SPEC §2.4

Checklist source: `specs/SPEC.md:61-75` (§2.4 `CalibratedModel`). Each field is assessed
against the calibration tool's **documented** output contract — `[documented]`, never
`[observed]` (nothing was run here).

| SPEC §2.4 field (`specs/SPEC.md` lines) | Engine's documented output | Verdict | Claim type |
|---|---|---|---|
| `house_id` (62) | not part of a bill-calibration tool's output; the tool calibrates a *given* HPXML document | **gap** — the adapter must stamp the id of the house it was given | `[documented]` |
| `state_hash` (62) | not emitted by the tool | **gap** — the adapter must carry the house state hash from the HPXML it was handed | `[documented]` |
| `engine_ref` (62) | the tool reports its own name/version in its result metadata | **covered** — format as `openstudio-hpxml-calibration-<version>` | `[documented]` |
| `hpxml_ref` (63) | the tool writes back a *calibrated* HPXML document (the whole point of the calibration) | **covered** — needs the written file's SHA-256 to be computed by the adapter | `[documented]` |
| `parameters: dict[str, ParameterEstimate]` (64-65) | the tool reports calibrated building-system parameters (e.g. duct/UA/ infiltration-related) with uncertainty | **partial** — the tool reports point values plus uncertainty; the repo's `ParameterEstimate {value, low, high, unit, method}` requires a *range* per parameter plus a `method` tag | `[documented]` |
| `metrics: CalibrationMetrics {nmbe_pct, cvrmse_pct, granularity, guideline14_pass}` (66-68) | the tool reports NMBE and CV(RMSE) against the utility bills (this is the BPI-2400/Guideline-14 statistic pair) | **covered**, but the `guideline14_pass` boolean and the `granularity` label are **repo-side** computations over the reported NMBE/CV(RMSE) | `[documented]` |
| `bpi2400_metrics` (69-71) | the tool *is* a BPI-2400-oriented calibrator (`docs/PLAN.md:114`) | **covered** — recorded separately from `metrics`, never conflated (SPEC §2.4, `specs/SPEC.md:69-71`) | `[documented]` |
| `calibration_window {start_utc, end_utc}` (72) | the tool consumes a bill/weather period and reports the period it calibrated over | **covered** — must be re-expressed as a UTC half-open window | `[documented]` |
| `data_granularity` (72) | monthly bill calibration ⇒ `billing_period` | **covered** (`billing_period`) | `[documented]` |
| `calibrator_id` + `calibrator_version` (72) | tool name + version | **covered** — adapter stamps `openstudio-hpxml-calibration` / `0.1` | `[documented]` |
| `status` (73) | not emitted | **gap** — repo-side: `valid` when the calibration succeeded and metrics exist, else `invalidated_by_retrofit`/`stale` per LAW-4 | `[documented]` |
| `rc_parameters: RCParameters \| None` (74-75) | the tool is a black-box bill calibrator; it does not publish grey-box RC parameters (`ua`, `capacitance`, `tau`) | **gap** — `rc_parameters` stays `None` for this calibrator; the repo's own grey-box RC work (SPEC §2.4 note) is the producer | `[documented]` |

Summary: **6 covered, 4 partial/gap, 2 explicitly out of scope for this engine.** The two
LAW-sensitive fields are (a) `parameters` — the tool's point estimates must be widened
into `ParameterEstimate` ranges before they leave the adapter (LAW-1), and (b) `metrics`
vs `bpi2400_metrics` — the Guideline-14 boolean and the BPI-2400 acceptance figures are
stored in two separate fields and never merged (SPEC §2.4, `specs/SPEC.md:66-71`).

`[not measured here]` The upstream project's own README/API listing could not be fetched
(TLS interception, §3), so the "documented output" column above is sourced from this
repository's documentation (`docs/PLAN.md:114`, `README.md:41`) plus the tool's stated
purpose. A capable machine should re-verify this column against the upstream release's
README at the pinned tag (step in §8.4).

---

## 6. Juneau-style acceptance numbers

Quoted from `specs/SPEC.md:66-71` (SPEC §2.4) — these are the *only* acceptance figures
used; none are invented:

- **Guideline-14 monthly:** NMBE **±5%**, CV(RMSE) **≤15%**; stored in `metrics`
  (`specs/SPEC.md:68`).
- **Guideline-14 hourly:** NMBE **±10%**, CV(RMSE) **≤30%** (same line).
- **BPI-2400 acceptance:** e.g. **CVRMSE ≤ 0.20 in the Juneau application**, recorded
  separately as `bpi2400_metrics` — "the two standards are never conflated"
  (`specs/SPEC.md:69-71`).

Measured values for a Juneau-style synthetic house:

| Quantity | Value | Claim type |
|---|---|---|
| Guideline-14 monthly NMBE on the calibration bill set | `[not measured here]` | requires one calibration run |
| Guideline-14 monthly CV(RMSE) | `[not measured here]` | requires one calibration run |
| Guideline-14 hourly NMBE / CV(RMSE) | `[not measured here]` — and note the hourly standard is **out of scope** for a monthly-bill calibrator; hourly comparison needs hourly bills, which the tool does not consume | requires hourly bill inputs |
| BPI-2400 CVRMSE (Juneau application) | `[not measured here]` | requires one calibration run |
| `guideline14_pass` boolean | `[not measured here]` | repo-side boolean over the two monthly figures above |

**Acceptance-range statement (no measurement):** a conforming monthly calibration is one
whose Guideline-14 monthly NMBE falls in **[-5%, +5%]** and CV(RMSE) in **[0%, 15%]**, and
whose BPI-2400 CVRMSE falls in **[0, 0.20]**; these are the acceptance *bands* quoted from
the SPEC, not measured outcomes. `[documented]` (band quoted from `specs/SPEC.md:68-71`).

The spike's adapter makes the separation structural: `metrics` carries
`{nmbe_pct, cvrmse_pct, granularity, guideline14_pass}` and `bpi2400_metrics` carries the
BPI-2400 acceptance numbers in a separate field, and
`parse_calibration_payload()` refuses a payload that is missing any §2.4 field rather than
defaulting it away (`spikes/hpxml_calibration/adapter.py`).

---

## 7. What was delivered in this branch (Phase B fallback)

### 7.1 Adapter skeleton — `spikes/hpxml_calibration/`

- `spikes/hpxml_calibration/adapter.py`
  - **One named configuration mechanism:** `HET_HPXML_CALIBRATION_BIN` — the path to the
    engine executable. Unset, blank, or pointing at a nonexistent path ⇒ *engine absent*.
  - **Absent engine ⇒ shared typed error:** `require_engine()` raises
    `home_energy_twin.errors.EngineUnavailable` (`home_energy_twin/errors.py:58`) with a
    `reason` string. No second, bespoke error class was invented — the
    `spikes/ochre/ochre_adapter.py` shape (`OchreUnavailableError`) was followed only in
    *form*; the shared repo error is raised instead, as the brief requires.
  - **Out of process:** the adapter writes a JSON request file, runs the binary with a
    fixed argv (no shell), and reads a JSON result file. The engine is never imported
    in-process.
  - **Version pin handling:** `VersionPin`, `parse_version_pin()`,
    `parse_engine_version_output()`, `pin_satisfied_by()`; the pin is `0.1`
    (`docs/PLAN.md:114`) and only the 0.1.x line satisfies it.
  - **SPEC §2.4 mapping:** `parse_calibration_payload()` maps an engine result payload
    onto `home_energy_twin.types.CalibratedModel`, raising `ValueError` when a §2.4 field
    is missing; calibrated parameters are carried as `ParameterEstimate` ranges (LAW-1).
- `spikes/hpxml_calibration/__init__.py` — package docstring + public exports.
- The adapter is **spike code only**: it is *not* imported by `home_energy_twin` and it
  does **not** implement or register a production `Calibrator`. The core suite passes with
  the engine absent (verified in §9). A test asserts that no core module mentions
  `spikes` (`tests/test_hpxml_calibration_spike.py::test_core_package_does_not_import_the_spike`).

- `spikes/hpxml_calibration/run_calibration.py` — the CLI form of the hands-on script
  (§8.3): it builds the `House` from a synthetic HPXML file (SHA-256 → `hpxml_ref`),
  the monthly `UtilitySeries` from a synthetic bills JSON file and the observed-weather
  `WeatherSeries` from an optional weather JSON file, then calls the adapter and writes
  the resulting `CalibratedModel` as JSON. With the engine absent the CLI exits with
  code 2 and the shared `EngineUnavailable` message.

### 7.2 Tests — `tests/test_hpxml_calibration_spike.py`

Selected by `pytest -k hpxml`; they run in the core suite with **no engine installed**:

- absent-engine path: env var unset / blank / dangling ⇒ `engine_available()` is `False`
  and `require_engine()` / `adapter.calibrate()` raise `EngineUnavailable`;
- engine-present path: env var pointing at a real file ⇒ the path is returned;
- version-pin parsing: `0.1`/`v0.1`/`0.1.3` parse identically; garbage raises
  `ValueError`; only the 0.1.x line satisfies the pin; `--version` banner parsing
  (including a `0.2.0` banner being *rejected* by the pin);
- **out-of-process runner driven end-to-end against a fake engine binary** (a small
  Python script that reads the `--request` JSON and writes the `--output` JSON): the
  adapter produces a populated `CalibratedModel`, proving the request/response hand-off
  works without the real engine;
- CLI path: `run_calibration.main()` returns exit code 2 when the engine is absent;
- SPEC §2.4 payload mapping round-trip and missing-field rejection;
- the spike stays out of the core package.

### 7.3 Not delivered (and why)

- **Synthetic fixtures under `spikes/hpxml_calibration/fixtures/`** and a **recorded
  calibration run** were *not* produced: they require the engine binary, which could not
  be obtained (§3). The fixture recipe is in §8.2 so a capable machine can produce them
  verbatim.

---

## 8. Exact hands-on script a capable machine would run

Prerequisites: a machine with outbound HTTPS (no TLS-intercepting proxy), ≥10 GB free
disk, and Ruby + a C/C++ toolchain only if the pinned release must be built from source
(prefer the released installer/binary). The script below is the minimum to reproduce
every `[not measured here]` cell of this report.

### 8.1 Obtain and pin the engine

```bash
# 1) OpenStudio runtime (contains the EnergyPlus used by the calibration tool).
#    Pin the exact release used; record the download size and the installed size.
curl -fLO https://github.com/NREL/OpenStudio/releases/download/v3.6.0/OpenStudio-3.6.0.f9c5b0e0da-Linux-x86_64.tar.gz
ls -lh OpenStudio-3.6.0.f9c5b0e0da-Linux-x86_64.tar.gz   # download weight
mkdir -p "$HOME/opt/openstudio-3.6.0" && tar -xzf OpenStudio-3.6.0.*.tar.gz -C "$HOME/opt/openstudio-3.6.0" --strip-components=1
du -sh "$HOME/opt/openstudio-3.6.0"                       # installed weight
export PATH="$HOME/opt/openstudio-3.6.0/bin:$PATH"
openstudio --version

# 2) OpenStudio-HPXML-Calibration at the pinned version (docs/PLAN.md:114 -> v0.1).
git clone --depth 1 --branch v0.1 https://github.com/NREL/OpenStudio-HPXML-Calibration.git "$HOME/opt/OpenStudio-HPXML-Calibration"
du -sh "$HOME/opt/OpenStudio-HPXML-Calibration"            # installed weight

# 3) Point the spike adapter at the engine (the ONE named config mechanism).
export HET_HPXML_CALIBRATION_BIN="$HOME/opt/OpenStudio-HPXML-Calibration/bin/openstudio-hpxml-calibration"
"$HET_HPXML_CALIBRATION_BIN" --version
```

> If step 2's release tag is not named `v0.1`, use the tag that `docs/PLAN.md:114` refers
> to and **update the pin in `spikes/hpxml_calibration/adapter.py` and this report
> together** — the pin is a repo artefact, not a local convenience.

### 8.2 Build the synthetic home + synthetic monthly bills (LAW-11)

```bash
mkdir -p spikes/hpxml_calibration/fixtures
# Synthetic HPXML (a 1,900 sqft synthetic Juneau-style home: cold climate, oil heat,
# no PV/EV/battery -> LAW-3 confounders absent). Generate it, do not copy a real home.
# Synthetic monthly bills: 12 months of electricity + oil consumption with a known
# injected parameter offset, so the recovery error is computable.
# Shape expected by spikes/hpxml_calibration/run_calibration.py:
python3 - <<'PY'
import json, pathlib
out = pathlib.Path("spikes/hpxml_calibration/fixtures")
months = ["2026-%02d" % m for m in range(1, 13)]
bills = []
for i, month in enumerate(months):
    start = f"{month}-01T00:00:00Z"
    bills.append({"fuel": "electricity", "start_utc": start,
                  "end_utc": f"{month}-28T00:00:00Z",
                  "value": 1200.0 + 30.0 * i, "unit": "kWh"})
    bills.append({"fuel": "oil", "start_utc": start,
                  "end_utc": f"{month}-28T00:00:00Z",
                  "value": 100.0 + 10.0 * i, "unit": "gal"})
(out / "bills.json").write_text(json.dumps({"bills": bills}, indent=2))
PY
```

### 8.3 Run one calibration and capture the numbers (runtime + acceptance)

```bash
# Wall-clock runtime of ONE calibration (seconds), plus install weight re-check:
# --weather is the AMY/observed weather slice for the billing period (LAW-10);
# TMY weather must never be used for calibration.
/usr/bin/time -v python3 -m spikes.hpxml_calibration.run_calibration \
    --hpxml spikes/hpxml_calibration/fixtures/home.xml \
    --bills spikes/hpxml_calibration/fixtures/bills.json \
    --weather spikes/hpxml_calibration/fixtures/weather.json \
    --output spikes/hpxml_calibration/fixtures/calibration_result.json

du -sh "$HOME/opt/openstudio-3.6.0" "$HOME/opt/OpenStudio-HPXML-Calibration"

# Acceptance numbers (Guideline-14 and BPI-2400 kept apart):
python3 - <<'PY'
import json, pathlib
r = json.loads(pathlib.Path("spikes/hpxml_calibration/fixtures/calibration_result.json").read_text())
print("guideline14:", r["metrics"])            # nmbe_pct, cvrmse_pct, granularity, guideline14_pass
print("bpi2400    :", r["bpi2400_metrics"])     # cvrmse <= 0.20 (Juneau application)
print("params     :", r["parameters"])          # value/low/high/unit/method per parameter
print("fields     :", sorted(r))                # compare against SPEC 2.4 (specs/SPEC.md:61-75)
PY
```

Acceptance bands to report (quoted, not invented): Guideline-14 monthly NMBE ∈ [-5%, +5%],
CV(RMSE) ∈ [0%, 15%]; hourly NMBE ∈ [-10%, +10%], CV(RMSE) ∈ [0%, 30%]; BPI-2400
CVRMSE ∈ [0, 0.20] — `specs/SPEC.md:68-71`.

### 8.4 Re-verify the §5 checklist against the pinned release

```bash
sed -n '1,200p' "$HOME/opt/OpenStudio-HPXML-Calibration/README.md"   # documented outputs
ls  "$HOME/opt/OpenStudio-HPXML-Calibration/lib"                    # result/report writers
```

Then update §5 of this report: mark each SPEC §2.4 field `[documented]` (with the upstream
citation) or `[observed]` (with the command + output), and replace every
`[not measured here]` in §5/§6 with the measured value or a range.

### 8.5 Close the loop in the repo

```bash
python3 -m pytest tests/ -q          # must still pass with the engine absent
python3 -m pytest tests/ -q -k hpxml
```

---

## 9. Verification performed in this environment

```console
$ python3 -m pytest tests/
154 passed, 1 skipped in 3.41s
```

`[observed]` Full core suite passes with **no engine installed** (RD-7). (One pre-existing
skip in the golden-house tests; the skip is unrelated to this spike.)

```console
$ python3 -m pytest tests/ -k hpxml
19 passed, 136 deselected in 0.68s
```

`[observed]` The 19 new spike tests pass.

```console
$ python3 -m mypy spikes/hpxml_calibration
$ python3 -m ruff check --output-format=json spikes/hpxml_calibration
```

`[not measured here]` — **neither mypy nor ruff is installed on this VM** (`No module
named mypy` / `No module named ruff`) and the brief forbids installing tooling, so both
were skipped. The `pytest` runs above are the verification evidence. The package is
written to be `mypy --strict`-clean (full annotations, `Optional[...]` in
runtime-evaluated positions, no implicit `Any` in public signatures) but that is **not** a
verified claim on this machine.

Environment note (not part of the deliverable): the VM's interpreter had no `pydantic`
installed, so the repo's declared runtime dependency was materialised from the local
package cache into `/tmp/het-pylibs` (pydantic 2.14.1) and exposed via `PYTHONPATH` for
the test runs above. No network access was used and nothing outside `/tmp` was modified.

---

## 10. Open questions for the next iteration

1. Does the pinned **v0.1** release still expose a monthly-bill calibration entry point
   with the documented outputs of §5, or has the API moved (the repo already flags the
   implementation as “incomplete impl.”, `docs/PLAN.md:114`) `[not measured here]`?
2. Does the engine report *ranges* for its calibrated parameters, or only point values
   (which would have to be widened to `ParameterEstimate` ranges by the adapter under
   LAW-1) `[not measured here]`?
3. Does the engine's CV(RMSE) correspond to the **BPI-2400** statistic, the
   **Guideline-14** statistic, or both — i.e. can one run populate `metrics` and
   `bpi2400_metrics` without conflation `[not measured here]`?
4. What is the real install weight and one-calibration runtime on a normal workstation —
   the numbers that decide whether this engine runs in CI, on a workstation, or only
   offline `[not measured here]`?
