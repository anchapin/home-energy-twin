# Spike C — REPORT: Green Button DMD (ESPI XML) parsing quirks

Issue: <https://github.com/anchapin/home-energy-twin/issues/5> (`docs/ISSUES.md:19`)
Roadmap: `docs/ROADMAP.md` Phase 0, Spike C · RD-6 · RD-12b · SPEC §2.2, §4, §5
LAWS: LAW-5 (UTC inside), LAW-11 (synthetic fixtures only), LAW-12 (provenance everywhere)

## 0. Read this first — what was and was not validated

**The parser has never been run against a real DMD file.** The issue's real
export is owner-local, is not present in this worktree, and repo law (LAW-11)
forbids committing real user data. No network fetch was attempted or
performed.

Everything measured in this report was measured against a **synthetic**
ESPI/Atom fixture authored for this spike
(`tests/fixtures/green_button_dmd_synthetic.xml`, plus a second fixture with
non-UTC stamps). Those numbers are properties of the fixture, not of any real
utility's data. Section 7 lists the quirks that could **only** be confirmed on
the owner's real file, and the exact command that will settle them.

The deliverable is therefore the parser plus this written quirks report —
which is what `AGENTS.md` requires for a Green Button spike: *"If a task seems
to need a real file (e.g. a Green Button spike), it stays on the owner's
machine, uncommitted, and the deliverable is a written quirks report."*

## 1. What was built

`spikes/green_button/parser.py` — a stdlib-only (`xml.etree.ElementTree`)
DMD/ESPI parser with three entry points:

```python
parse_dmd_text(text: str, *, retrieved_at_utc=None, source=None, terms_tag=None) -> GreenButtonFile
parse_dmd_bytes(payload: bytes, ...) -> GreenButtonFile
parse_dmd_file(path: Path | str, ...) -> GreenButtonFile
```

It produces `home_energy_twin.types.UtilitySeries` objects — one per
`(meter, readingType, unit, granularity)` — plus a `GreenButtonFile` record
holding the timestamp basis, gap/overlap findings, estimated-read counts,
warnings and provenance.

Design decisions:

| Decision | Why |
| --- | --- |
| Elements are matched on **local name**, not namespace | Real exports use `http://greenbutton.com/schema/1.0` or NAESB ESPI namespaces, and utilities differ. Namespace-agnostic matching keeps the parser usable across utilities (the real file's namespaces are still unverified — §7). |
| Every stamp is converted to **UTC at parse time** | LAW-5. The parser never keeps a local time internally. |
| The file's **timestamp basis is reported**, not assumed | RD-6 / the Phase-0 gate item "zero silent timestamp assumptions". |
| Interval blocks and billing summations become **separate series** | A meter can ship both; merging them would mix granularities (RD-6). |
| Unknown `readingType` is **refused**, not guessed | LAW-3 honesty; `UnidentifiableConfiguration` names the reason. |
| Gaps are **reported, never filled** | Interpolation would invent energy; `home_energy_twin.intervals.slice_points` likewise drops partially covered points rather than rescaling them. |
| DTD / entity declarations are **rejected** | `xml.etree` is not hardened against entity expansion; a DMD file has no business carrying a DTD. |

## 2. Evidence — parse counts (synthetic fixture)

Command:

```
$ python3 -m spikes.green_button tests/fixtures/green_button_dmd_synthetic.xml
Green Button DMD summary
  timestamp basis    : utc_z
  points             : 16
  series             : 4
  estimated reads    : 1
  gaps               : 1
  overlaps           : 0
  provenance source  : green_button_dmd:Synthetic Meter Co
  terms tag          : unspecified

  meter                     fuel         kind       gran            sem              points
  000000000001              electricity  import     billing_period interval_start   2
  000000000001              electricity  import     hourly         interval_start   9
  000000000002              natural_gas  delivered  daily          interval_start   3
  000000000003              electricity  net        hourly         interval_start   2

  gaps:
    000000000001 2026-03-01T11:00:00+00:00 -> 2026-03-01T12:00:00+00:00 (1:00:00)
```

| Measure | Value (synthetic fixture) |
| --- | --- |
| Points parsed | 16 |
| `UtilitySeries` produced | 4 |
| `MeterReadSummary` produced | 4 |
| Timestamp basis | `utc_z` (every stamp carried `Z`) |
| Estimated reads | 1 of 9 hourly electricity reads (0.417 kWh at 2026-03-01T09:00Z) |
| Gaps | 1 (exactly 1 h, between the 10:00Z and 12:00Z readings) |
| Overlaps | 0 |
| Hourly electricity total | 3.574 kWh (9 points, 1 estimated) |
| Gas total | 3.213 CCF (3 daily points) |
| Net-meter total | 0.752 kWh (2 hourly points) |
| Billing-period points | 2, on the electricity meter |

Second fixture (`green_button_dmd_offset_synthetic.xml`, stamps written as
`-05:00`): basis reported as `fixed_local_offset`, and the three hourly
readings land on **05:00Z, 06:00Z, 07:00Z** — the same instants as the UTC
fixture, converted at ingest.

## 3. Quirks

### 3.1 Timestamp basis

* ESPI carries **no in-band declaration** of its timestamp basis. The basis is
  inferable only from the literal form of the stamps themselves, so the parser
  *measures* it and reports it: `utc_z`, `utc_offset_zero`, `fixed_local_offset`
  or `mixed_offsets`.
* A stamp **without an offset** is refused (`InsufficientData`, reason
  `naive_timestamp`) rather than localised — the parser has no house timezone
  and must not borrow one (LAW-5: local time exists only at the edges).
* Mixed-offset files are reported as `mixed_offsets`; the parser still
  normalises each stamp to the instant it denotes.
* **Open question for the real file:** whether the utility stamps in UTC
  (`Z`) or in local time with a DST-observing offset. The dangerous case is a
  *fixed* offset written all year (`-05:00` in July): it would shift every
  local-time-aligned billing boundary by an hour after the DST transition
  without any signal in the file. This can only be confirmed on the owner's
  file (§7).

### 3.2 Stamp semantics

* ESPI `IntervalReading` normally states `startTime` **plus a duration**, so
  the source stamp is the interval **start**: the parser records
  `stamp_semantics = "interval_start"` on the series (SPEC §2.2 requires the
  source's semantics to be recorded, not silently normalised).
* When a reading states only `endTime` + duration, the parser records
  `"interval_end"` and derives the start by subtracting the duration. Covered
  by `test_interval_end_stamps_are_honoured` (synthetic end-stamped export).
* `IntervalBlock/@duration` applies to every reading in the block that does
  not carry its own duration; a block may also carry `startTime`/`endTime`
  instead of a duration.
* Intervals are half-open `[start, end)` throughout, matching
  `home_energy_twin.intervals.normalise_stamp` — the end instant belongs to
  the next interval (asserted by `test_intervals_are_half_open`).
* **Open question for the real file:** whether the export stamps starts or
  ends, and whether a "settlement"/"billing" reading type uses different
  semantics from the interval readings (some utilities ship a settlement
  read whose stamp is the *end* of the billing period).

### 3.3 Estimated reads

* The `dataQuality` element carries `actual` / `estimated`; the parser sets
  `EnergyPoint.estimated` from it (also honouring a `quality` attribute and a
  reading-level default).
* Estimated reads are **kept and flagged**, not dropped: the fixture's
  electricity series carries 9 points of which 1 is estimated, and the parse
  reports `estimated_read_count = 1` and `estimated_count = 1` in the read
  summary.
* Flagged, not silently equal: downstream calibration can then exclude or
  down-weight them, and a user can see that a month was not fully metered.
* **Open question for the real file:** whether estimated reads are marked
  `estimated`, or are exported with `dataQuality` absent (in which case
  estimated and actual are indistinguishable and the flag would always be
  `False` — a silent data-quality loss worth knowing about). Some utilities
  also express an estimate as a zero-value read, which is a different trap
  (§3.4).

### 3.4 Gaps

* A gap is a hole between two consecutive intervals: `next.start > prev.end`.
  The fixture contains exactly one — the 11:00Z hour is missing between the
  10:00Z and 12:00Z readings — reported as a 1-hour `CoverageIssue`, with
  `after_end_utc = 2026-03-01T11:00Z` and `before_start_utc = 2026-03-01T12:00Z`.
* Gaps are **reported, never filled**: the hourly series keeps 9 points, not
  10, and no interpolated point appears (`test_no_silent_fill_of_a_gap`).
  `home_energy_twin.intervals.slice_points` behaves the same way: a point that
  only partly overlaps a window is dropped rather than rescaled.
* Overlaps are detected too (a point starting before the previous point ends);
  the fixture has none.
* **Open question for the real file:** how absence is expressed — omitted
  intervals, an empty block, a zero-valued read, or a repeated stamp. A
  zero-valued read is the nastiest case: it *looks* like data and would
  silently pull a monthly total down, so the real file needs checking for
  `value == 0` rows before the parser is trusted with it.

### 3.5 Billing-period shapes

* A billing period is a **point with a long, irregular `[start, end)`** — not
  a separate monthly type (SPEC §2.2, RD-6). The parser keeps them in their
  own `billing_period`-granularity series.
* The fixture's two billing points show why "a month" is not a duration:

| Billing point | Bounds (UTC) | Length |
| --- | --- | --- |
| February 2026 | 2026-02-01T05:00Z → 2026-03-01T05:00Z | 672 h (28 days) |
| March 2026 | 2026-03-01T05:00Z → 2026-04-01T04:00Z | **743 h** (calendar March in `America/New_York`, 23 h short of 31 days because of the DST spring-forward) |

  A calendar month in a DST-observing zone is 720, 744 or 743 h depending on
  the zone and the month. Code that assumes 30 × 24 h is wrong on both ends
  of a DST boundary, and wrong by up to an hour *inside* it.
* Slicing behaves per RD-6: slicing the hourly series to the March billing
  window keeps all 9 hourly points; slicing it to the February window returns
  **0** points — no rescaling into a window the data does not cover. Slicing
  the billing series to March returns the single March point (604.900 kWh).
* One meter yields **two** series here (`hourly` and `billing_period`), so a
  downstream consumer must not assume "one series per meter".
* **Open question for the real file:** whether billing boundaries are
  calendar months, meter-read dates (which drift a day each cycle), or
  something the utility states explicitly — and whether the export carries
  them as `Summation` elements at all (some utilities ship only intervals and
  leave billing to a separate bill document). Also whether the billing bounds
  are stated in UTC or in local time; if local, the DST hour inside a period
  is invisible in the file and only the *length* betrays it.

### 3.6 Other quirks seen in the fixture

* `readingType` → `(fuel, kind)` mapping is explicit:
  `electricity` → import, `electricityNet` → net, `gas` → natural gas /
  delivered. An unmapped type (e.g. `water`) raises
  `UnidentifiableConfiguration` with reason `unsupported_reading_type:…`
  rather than being coerced into a fuel.
* A missing `unitOfMeasure` is *not* silent: the parser falls back to the
  reading type's usual unit and records a warning naming the assumption.
* A `PT24H` "daily" interval is a **fixed 24-hour window in UTC**, not a
  local calendar day (fixture: gas reads at 05:00Z each day). Aggregating
  those into "days" gives UTC days unless they are re-cut to the house's
  IANA timezone at the display/ingest edge (LAW-5).
* Cost is present per reading in the fixture (with a `currency` attribute) and
  is kept in the read summary — `UtilitySeries` has no cost field, so cost
  has nowhere to live in the domain type yet (a gap for Phase 1, not for this
  spike).
* A DMD export carries **no licence or terms information**, so
  `Provenance.terms_tag` defaults to `"unspecified"` and the parse emits a
  warning (LAW-12): the utility's terms must be attached by the caller.
* `MeterReading/source` is used as the provenance source
  (`green_button_dmd:<source>`); a file with no source at all is refused with
  `ProvenanceMissing` rather than being attributed to nothing.

## 4. Error mapping (SPEC §5)

| Condition | Typed error | `reason` |
| --- | --- | --- |
| Not well-formed XML | `InsufficientData` | `malformed_xml` |
| DTD / entity declarations present | `InsufficientData` | `dtd_not_allowed` |
| Stamp without a UTC offset | `InsufficientData` | `naive_timestamp` |
| Interval with neither a resolvable end nor a duration | `InsufficientData` | `unbounded_interval` |
| Non-numeric value | `InsufficientData` | `unparseable_value` |
| Export with no usable `MeterReading` | `InsufficientData` | `no_readings` |
| `readingType` with no fuel/kind mapping | `UnidentifiableConfiguration` | `unsupported_reading_type:<type>` |
| Export states no source | `ProvenanceMissing` | `missing_source` |

No bare `ValueError` leaves the parser.

## 5. Verification

Run from the worktree root on branch `worker/5-green-button-spike`.

```
$ python3 -m pytest tests/ -q
239 passed, 1 skipped in 5.46s

$ python3 -m pytest tests/test_green_button_spike.py -q
33 passed in 1.04s

$ mypy --strict spikes/green_button home_energy_twin
Success: no issues found in 18 source files
```

`mypy` is configured in `pyproject.toml` with `strict = true` and
`files = ["home_energy_twin"]`; the command above extends the file list to
the new package (the repo's own invocation, with the spike package added).

`ruff` is **not installed** in this environment (`ruff: not found`), so no
ruff output is quoted; the code follows the style of the existing packages
and passes `mypy --strict`.

## 6. Hard stops honoured

* No telemetry, no network calls, no device actuation (LAW-6, LAW-7).
* No real user data in the repo, fixtures, tests, logs or this report
  (LAW-11). Every identifier and value in the fixtures is synthetic.
* No new dependency: stdlib `xml.etree` only, so no license question
  (LAW-10's dependency rule / AGENTS.md closed-license rule).
* No calibrated figure is produced anywhere in this spike, so LAW-1 (ranges,
  not points) has nothing to bite on: the only numbers here are measured
  sums of a synthetic file. If a future change turns any of these into a
  user-facing calibrated number, it must become a range.

## 7. PENDING — validation against the owner's real DMD file (owner-local step)

**Status: pending. Not done. The parser has not seen a real export.**

The owner runs this **on their own machine**; the export file itself is never
committed (LAW-11), and only the summary output is pasted back (with values
edited if desired):

```
$ python3 -m spikes.green_button ~/Downloads/<your-export>.xml
$ python3 -m spikes.green_button ~/Downloads/<your-export>.xml --terms-tag "<utility>-dmd"
```

Quirks that **only the real file can settle**, in the order they matter:

| # | Question to settle on the real file | Why it matters | How to see it |
| --- | --- | --- | --- |
| 1 | Timestamp basis: `Z`, offset-aware local, or naive? | A naive stamp is currently refused; a *fixed* local offset shifts billing boundaries across DST | Check the summary's `timestamp basis` line, and open the XML in a text editor to look at a few `startTime` literals |
| 2 | Do the stamps observe DST (offset changes across the year)? | A fixed offset is a silent one-hour error after the transition | Compare the offset of the first and last interval in the file |
| 3 | Stamp semantics: start or end? | Decides whether every interval is off by one slot | Check `sem` in the summary; compare a block's `startTime` with the neighbouring block |
| 4 | Estimated reads: `dataQuality=estimated`, absent, or zero-valued? | Decides whether `EnergyPoint.estimated` is trustworthy at all | Count `dataQuality` values in the XML; look for `value` of `0` |
| 5 | Gaps: omitted intervals, empty blocks, or zero reads? | Zero reads look like data and would understate a monthly total | Compare the summary's `gaps` count with the number of `IntervalReading` elements; check for `value` of `0` |
| 6 | Billing-period shapes: are billing periods present at all, and are their bounds UTC or local? | RD-6 requires billing periods as points with irregular bounds | Look for `Summation` elements; check whether their bounds line up with local meter-read dates |
| 7 | `readingType` values and units actually used | An unmapped type is refused today (`UnidentifiableConfiguration`) | Grep the `readingType` and `unitOfMeasure` values in the file |
| 8 | Namespaces and element names actually used | The parser matches local names; an unusual vocabulary may need a mapping | Look at the root element's namespaces and the element names |
| 9 | Whether one meter ships several `MeterReading`s of different types (e.g. demand, settlement) | Each `(meter, readingType, unit, granularity)` becomes its own series | Count `MeterReading` elements per `UsagePoint` |
| 10 | The utility's terms/licence for the export | `Provenance.terms_tag` defaults to `unspecified` (LAW-12) | Read the utility's terms; pass `--terms-tag` |

If the real file trips one of the refusals (`naive_timestamp`,
`unbounded_interval`, `unsupported_reading_type`, `missing_source`), that is
the spike doing its job: the fix belongs in the parser, keyed on what the
real file actually contains — and the fix must stay inside this spike until
the owner has confirmed it.

## 8. Suggested follow-ups (not started)

1. Run §7 on the owner's file and fold the answers into this report.
2. Decide where cost belongs: `UtilitySeries` has no cost field today, and
   `CostEstimate` in SPEC §2.5 is a retrofit-cost type, not a utility-bill
   type. A DMD ingest may need its own small type (or a `DataSource`
   implementation under `home_energy_twin/`), which is Phase-1 work
   (`docs/ISSUES.md:31`), not spike work.
3. If the real export turns out to stamp intervals in local time, the ingest
   path will need the house's IANA timezone at the edge (LAW-5) rather than
   the current "refuse naive stamps" behaviour — decide that with the owner,
   not unilaterally.
4. A `DataSource` implementation (`home_energy_twin/protocols.py`) wrapping
   this parser is the natural Phase-1 deliverable once the real-file quirks
   are known.
