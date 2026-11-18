# Spike C — Green Button DMD (ESPI XML) parser

Issue: <https://github.com/anchapin/home-energy-twin/issues/5> (`docs/ISSUES.md:19`)
Roadmap gate: `docs/ROADMAP.md` Phase 0, Spike C · RD-6 · RD-12b · SPEC §2.2, §5 · LAW-5, LAW-11, LAW-12.

## Question

Can a Green Button **DMD** export (Atom XML carrying NAESB ESPI
`UsagePoint` / `MeterReading` / `IntervalBlock` / `IntervalReading` elements) be
parsed into the repository's own `UtilitySeries` domain type **without any
silent timestamp assumption**, and what quirks does the format actually carry
(timestamp basis, stamp semantics, estimated reads, gaps, billing-period
shapes)?

## Scope and hard stops

* **Synthetic fixtures only** (LAW-11). The committed fixture
  `tests/fixtures/green_button_dmd_synthetic.xml` is hand-written. The owner's
  real DMD file is owner-local, is never committed, and no network fetch is
  attempted. See `REPORT.md` §7 for the pending owner-local validation step.
* **Stdlib only** — `xml.etree.ElementTree`. No new dependency, so no license
  question arises.
* **No telemetry** (LAW-6), **no actuation** (LAW-7): the parser reads bytes
  the caller hands it and prints a summary; it makes no network call and
  writes nothing.
* **Ranges, not points** (LAW-1): this spike produces *no* calibrated figure.
  The only numbers it reports are measured sums of the file it was given.
* **Typed errors** (SPEC §5): the parser raises `InsufficientData`,
  `UnidentifiableConfiguration` or `ProvenanceMissing` — never a bare
  `ValueError`.

## Plan (as executed)

1. Read `AGENTS.md`, `specs/SPEC.md` (§2.2, §4 LAWS, §5), `docs/ROADMAP.md`,
   `docs/ISSUES.md`, `home_energy_twin/types.py`, `home_energy_twin/errors.py`,
   `home_energy_twin/intervals.py`, `spikes/ochre/`.
2. Write the synthetic ESPI/Atom fixture(s) that exercise interval blocks,
   an estimated read, a gap, a DST-shortened billing period, a gas meter, a
   net meter and a non-UTC (offset) timestamp basis.
3. Write the parser: namespace-agnostic element lookup by local name,
   ISO-8601 duration parsing, UTC normalisation at ingest with the file's
   timestamp basis reported, explicit stamp semantics, per-meter series
   grouping, gap/overlap detection.
4. Write the tests: timestamp basis, stamp semantics (start **and** end),
   estimated-read flagging, gap detection, billing-period slicing, provenance,
   and one test per typed error.
5. Write `REPORT.md`: the quirks, the numbers, and the explicit
   **pending-real-file** section.

## What counts as done for this spike

* The parser turns a DMD/ESPI export into valid `UtilitySeries` objects
  (pydantic validation passes), points ordered by `start_utc`, stamp semantics
  set explicitly, gaps reported rather than filled.
* Tests cover timestamp basis, interval start/end stamp semantics,
  estimated-read flagging, gap detection and billing-period slicing.
* `REPORT.md` exists, with a section stating that validation against the
  owner's real DMD file is still pending and which quirks could only be
  confirmed there.
* `mypy --strict` is clean on the new package and the full test suite passes.

## Layout

| Path | Purpose |
| --- | --- |
| `spikes/green_button/__init__.py` | package exports |
| `spikes/green_button/parser.py` | the DMD/ESPI parser (`parse_dmd_text` / `parse_dmd_bytes` / `parse_dmd_file`) |
| `spikes/green_button/__main__.py` | owner-local CLI: `python3 -m spikes.green_button <export.xml>` |
| `spikes/green_button/README.md` | this plan |
| `spikes/green_button/REPORT.md` | the quirks report + the pending-real-file section |
| `tests/fixtures/green_button_dmd_synthetic.xml` | synthetic ESPI/Atom fixture (UTC basis) |
| `tests/fixtures/green_button_dmd_offset_synthetic.xml` | synthetic ESPI/Atom fixture (offset basis) |
| `tests/test_green_button_spike.py` | the tests |
