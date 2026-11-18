"""Monthly bill ingest (SPEC §2.2, RD-6, RD-12b).

The manual path is the product (RD-12b).  Two shapes of the same thing are
supported here:

* **Typed / CSV monthly rows** — a homeowner types the bill periods in (or
  uploads a CSV of them).  Bill periods are *calendar-date* periods in the
  house's IANA timezone; they become half-open ``[start, end)`` UTC windows.
* **Green Button DMD-shaped (ESPI Atom/XML)** — a synthetic, DMD-shaped
  document is parsed for its monthly billing readings.

Both produce a :class:`~home_energy_twin.types.UtilitySeries` with

* ``granularity == "billing_period"``,
* correct half-open ``[start, end)`` :class:`~home_energy_twin.types.EnergyPoint`
  bounds,
* stamp semantics normalised per SPEC §2.2 with the conversion recorded in
  the returned :class:`StampConversionRecord`,
* estimated flags preserved, and
* provenance set (LAW-12).

Scope boundary (issue #9): only the **monthly billing** subset is handled.
Readings at interval granularity (15-minute DMD blocks, hourly reads) are
skipped with an explicit note — interval ingest is issue #13, and no
interval/subhourly series is produced here.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Sequence
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import InsufficientData
from .intervals import normalise_stamp
from .timebase import ensure_utc, from_local
from .types import (
    EnergyPoint,
    Fuel,
    MeterKind,
    Provenance,
    StampSemantics,
    UTCDateTime,
    UtilitySeries,
)

__all__ = [
    "BILLING_PERIOD_MIN_DAYS",
    "IngestResult",
    "MonthlyBillRow",
    "StampConversionRecord",
    "ingest_green_button_dmd",
    "ingest_monthly_csv",
    "monthly_rows_to_series",
]

#: A billing period is at least this long; shorter readings are interval data.
BILLING_PERIOD_MIN_DAYS = 25

#: UTC as a fixed offset (LAW-5).
UTC = timezone.utc

#: Default terms tag for homeowner-typed bills.
DEFAULT_TERMS_TAG = "user-provided"

#: Default source label for homeowner-typed bills.
DEFAULT_SOURCE = "manual-monthly-bills"

#: Source label for a Green Button DMD-shaped document.
DMD_SOURCE = "green-button-dmd"

_METER_KIND_BY_NAME: dict[str, MeterKind] = {
    "import": "import",
    "export": "export",
    "net": "net",
    "delivered": "delivered",
}

_FUEL_BY_NAME: dict[str, Fuel] = {
    "electricity": "electricity",
    "electric": "electricity",
    "gas": "natural_gas",
    "natural_gas": "natural_gas",
    "naturalgas": "natural_gas",
    "oil": "oil",
    "propane": "propane",
    "district": "district",
}


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class MonthlyBillRow(BaseModel):
    """One monthly bill period, as a homeowner would type it.

    ``period_start`` / ``period_end`` are **local calendar dates** in the
    house's timezone; ``period_end`` is exclusive (the period is
    ``[period_start, period_end)``).  When ``period_start`` is omitted the
    period starts where the previous bill ended — the source then reports an
    *interval-end* stamp and the conversion is recorded at ingest.
    """

    model_config = ConfigDict(extra="forbid")

    period_start: Optional[date] = None
    period_end: date
    value: float
    unit: str = Field(default="kWh", min_length=1)
    estimated: bool = False
    fuel: Optional[Fuel] = None
    kind: Optional[MeterKind] = None
    meter_id: Optional[str] = None

    @model_validator(mode="after")
    def _check_period(self) -> "MonthlyBillRow":
        if self.period_start is not None and self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


class StampConversionRecord(BaseModel):
    """Record of the stamp-semantics normalisation applied at ingest.

    ``from_semantics`` is what the source reported, ``to_semantics`` is what
    the stored series uses (always ``interval_start``, the half-open
    ``[start, end)`` convention of SPEC §2.2).  ``nominal_duration_s`` is
    ``None`` when the periods are irregular (billing periods are).
    """

    model_config = ConfigDict(extra="forbid")

    from_semantics: StampSemantics
    to_semantics: StampSemantics = "interval_start"
    nominal_duration_s: Optional[int] = None
    applied_at_utc: UTCDateTime
    note: str = ""


class IngestResult(BaseModel):
    """The outcome of one ingest run."""

    model_config = ConfigDict(extra="forbid")

    series: UtilitySeries
    conversion: StampConversionRecord
    source: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _local_midnight_utc(day: date, timezone_name: str) -> datetime:
    """UTC instant of local midnight on ``day`` (DST-aware)."""
    return from_local(datetime.combine(day, time.min), timezone_name)


def _default_kind(fuel: Fuel) -> MeterKind:
    """Default meter kind for a fuel (``import`` for electricity)."""
    return "import" if fuel == "electricity" else "delivered"


def _resolve_fuel(
    fuel: Optional[Fuel],
    row_fuels: Sequence[Optional[Fuel]],
) -> Fuel:
    candidates = {value for value in row_fuels if value is not None}
    if fuel is not None:
        candidates.add(fuel)
    if not candidates:
        raise InsufficientData(
            "fuel is unknown: state it on the series or on every row",
            reason="fuel_unknown",
        )
    if len(candidates) > 1:
        raise InsufficientData(
            "rows mix fuels; ingest one fuel per series",
            reason="mixed_fuel",
        )
    return candidates.pop()


def _resolve_kind(
    kind: Optional[MeterKind],
    row_kinds: Sequence[Optional[MeterKind]],
    fuel: Fuel,
) -> MeterKind:
    candidates = {value for value in row_kinds if value is not None}
    if kind is not None:
        candidates.add(kind)
    if not candidates:
        return _default_kind(fuel)
    if len(candidates) > 1:
        raise InsufficientData(
            "rows mix meter kinds; ingest one meter per series",
            reason="mixed_meter_kind",
        )
    return candidates.pop()


def _provenance(
    source: str,
    terms_tag: str,
    retrieved_at_utc: Optional[datetime],
) -> Provenance:
    moment = retrieved_at_utc or datetime.now(UTC)
    return Provenance(
        source=source,
        retrieved_at_utc=ensure_utc(moment),
        terms_tag=terms_tag,
    )


# --------------------------------------------------------------------------
# Typed / CSV monthly rows
# --------------------------------------------------------------------------


def monthly_rows_to_series(
    rows: Sequence[MonthlyBillRow],
    *,
    timezone: str,
    fuel: Optional[Fuel] = None,
    kind: Optional[MeterKind] = None,
    meter_id: Optional[str] = None,
    source: str = DEFAULT_SOURCE,
    terms_tag: str = DEFAULT_TERMS_TAG,
    retrieved_at_utc: Optional[datetime] = None,
) -> IngestResult:
    """Turn typed monthly bill rows into a billing-period ``UtilitySeries``.

    Two anchoring styles are supported, and a file may mix them:

    * a row states ``period_start`` and ``period_end`` — the source reported
      the period start, and no shift is needed;
    * a row states only ``period_end`` — the period starts where the
      previous bill ended (the first row must then state a start), and the
      source reported an *interval-end* stamp, which is shifted to
      ``[start, end)`` with the conversion recorded.
    """
    if not rows:
        raise InsufficientData("no bill rows to ingest", reason="no_rows")

    explicit = [row.period_start is not None for row in rows]
    anchored_by_end = not all(explicit)

    resolved_fuel = _resolve_fuel(fuel, [row.fuel for row in rows])
    resolved_kind = _resolve_kind(kind, [row.kind for row in rows], resolved_fuel)
    meters = {row.meter_id for row in rows if row.meter_id is not None}
    if meter_id is not None:
        meters.add(meter_id)
    if len(meters) > 1:
        raise InsufficientData(
            "bill rows mix meter ids; ingest one meter per series",
            reason="mixed_meter_id",
        )
    resolved_meter = meters.pop() if meters else None

    points: list[EnergyPoint] = []
    notes: list[str] = []
    previous_end: Optional[date] = None
    durations: list[timedelta] = []

    for row in rows:
        if row.period_start is not None:
            start_local = row.period_start
        else:
            if previous_end is None:
                raise InsufficientData(
                    "the first bill period must state its start date",
                    reason="missing_first_period_start",
                )
            start_local = previous_end
        if start_local >= row.period_end:
            raise InsufficientData(
                "bill periods must be ordered and non-empty",
                reason="bad_period_order",
            )
        start_utc = _local_midnight_utc(start_local, timezone)
        end_utc = _local_midnight_utc(row.period_end, timezone)
        if end_utc <= start_utc:
            raise InsufficientData(
                "bill period collapses to an empty window",
                reason="empty_period",
            )
        durations.append(end_utc - start_utc)
        points.append(
            EnergyPoint(
                start_utc=start_utc,
                end_utc=end_utc,
                value=row.value,
                unit=row.unit,
                estimated=row.estimated,
            )
        )
        previous_end = row.period_end

    points.sort(key=lambda point: point.start_utc)

    if not anchored_by_end:
        from_semantics: StampSemantics = "interval_start"
        nominal: Optional[int] = None
        note = (
            "source stated the period start and end explicitly; local calendar "
            "dates were converted to UTC and kept as [start, end)"
        )
    else:
        from_semantics = "interval_end"
        nominal = None
        note = (
            "source reported period-end stamps; each period without an explicit "
            "start was shifted to [start, end) using the previous bill period end"
        )
        if durations:
            lengths = {round(duration.total_seconds()) for duration in durations}
            if len(lengths) == 1:
                nominal = lengths.pop()

    estimated_count = sum(1 for point in points if point.estimated)
    if estimated_count:
        notes.append(f"{estimated_count} of {len(points)} bill periods are estimated")

    series = UtilitySeries(
        fuel=resolved_fuel,
        kind=resolved_kind,
        granularity="billing_period",
        points=points,
        stamp_semantics=from_semantics,
        provenance=_provenance(source, terms_tag, retrieved_at_utc),
        meter_id=resolved_meter,
    )
    conversion = StampConversionRecord(
        from_semantics=from_semantics,
        to_semantics="interval_start",
        nominal_duration_s=nominal,
        applied_at_utc=ensure_utc(retrieved_at_utc or datetime.now(UTC)),
        note=note,
    )
    return IngestResult(series=series, conversion=conversion, source=source, notes=notes)


def _parse_bool(value: str) -> bool:
    text = value.strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n", ""}:
        return False
    raise ValueError(f"not a boolean flag: {value!r}")


def _parse_date(value: str, column: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"column {column!r} is not an ISO date: {value!r}") from exc


def ingest_monthly_csv(
    text: str,
    *,
    timezone: str,
    fuel: Optional[Fuel] = None,
    kind: Optional[MeterKind] = None,
    meter_id: Optional[str] = None,
    source: str = DEFAULT_SOURCE,
    terms_tag: str = DEFAULT_TERMS_TAG,
    retrieved_at_utc: Optional[datetime] = None,
) -> IngestResult:
    """Ingest monthly bills from CSV text.

    Required columns: ``period_start`` (optional, see
    :func:`monthly_rows_to_series`), ``period_end``, ``value``.  Optional
    columns: ``unit``, ``estimated``, ``fuel``, ``kind``, ``meter_id``.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise InsufficientData("CSV has no header row", reason="no_header")
    names = {name.strip().lower() for name in reader.fieldnames}
    missing = {"period_end", "value"} - names
    if missing:
        raise InsufficientData(
            f"CSV is missing required column(s): {sorted(missing)}",
            reason="missing_columns",
        )

    rows: list[MonthlyBillRow] = []
    for raw in reader:
        row = {
            (key.strip().lower() if key else ""): (value or "").strip()
            for key, value in raw.items()
        }
        period_start_text = row.get("period_start", "")
        row_fuel = row.get("fuel", "")
        row_kind = row.get("kind", "")
        rows.append(
            MonthlyBillRow(
                period_start=(
                    _parse_date(period_start_text, "period_start")
                    if period_start_text
                    else None
                ),
                period_end=_parse_date(row.get("period_end", ""), "period_end"),
                value=float(row.get("value", "")),
                unit=row.get("unit", "") or "kWh",
                estimated=_parse_bool(row.get("estimated", "") or "false"),
                fuel=_FUEL_BY_NAME.get(row_fuel.lower()) if row_fuel else None,
                kind=_METER_KIND_BY_NAME.get(row_kind.lower()),
                meter_id=row.get("meter_id") or None,
            )
        )

    return monthly_rows_to_series(
        rows,
        timezone=timezone,
        fuel=fuel,
        kind=kind,
        meter_id=meter_id,
        source=source,
        terms_tag=terms_tag,
        retrieved_at_utc=retrieved_at_utc,
    )


# --------------------------------------------------------------------------
# Green Button DMD-shaped (ESPI Atom/XML) input
# --------------------------------------------------------------------------

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def _parse_duration(text: str) -> timedelta:
    """Parse an ISO-8601 duration such as ``P30D`` or ``PT744H``."""
    match = _DURATION_RE.match(text.strip())
    if match is None or not any(match.groupdict().values()):
        raise ValueError(f"not an ISO-8601 duration: {text!r}")
    parts = {key: float(value) for key, value in match.groupdict().items() if value}
    return timedelta(
        days=parts.get("days", 0.0),
        hours=parts.get("hours", 0.0),
        minutes=parts.get("minutes", 0.0),
        seconds=parts.get("seconds", 0.0),
    )


def _local_name(tag: str) -> str:
    """The local name of an XML tag (namespace-agnostic)."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _find_local(parent: ET.Element, name: str) -> Optional[ET.Element]:
    """Find the first descendant with the given local tag name (any namespace).

    The comparison is case-insensitive: ESPI documents in the wild spell the
    same element as ``startTime`` and ``StartTime``.
    """
    wanted = name.lower()
    for child in parent.iter():
        if _local_name(child.tag).lower() == wanted:
            return child
    return None


def _text_of(parent: ET.Element, name: str) -> Optional[str]:
    element = _find_local(parent, name)
    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None


def _parse_stamp(text: str) -> datetime:
    """Parse an ESPI timestamp (``Z`` or offset) into a UTC instant."""
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        raise ValueError(f"timestamp is not timezone-aware: {text!r}")
    return ensure_utc(moment)


def _entry_fuel(entry: ET.Element) -> Optional[Fuel]:
    for element in entry.iter():
        name = _local_name(element.tag).lower()
        if name == "electricusage":
            return "electricity"
        if name in {"gasusage", "naturalgasusage"}:
            return "natural_gas"
        if name == "oilusage":
            return "oil"
        if name == "propaneusage":
            return "propane"
    title = _text_of(entry, "title")
    if title is not None:
        lowered = title.lower()
        if "electric" in lowered:
            return "electricity"
        if "gas" in lowered:
            return "natural_gas"
        if "propane" in lowered:
            return "propane"
        if "oil" in lowered:
            return "oil"
    return None


def _entry_meter(entry: ET.Element) -> Optional[str]:
    for name in ("meterNumber", "meterID", "meterId"):
        value = _text_of(entry, name)
        if value:
            return value
    identifier = _text_of(entry, "id")
    return identifier


def _reading_estimated(reading: ET.Element) -> bool:
    for name in ("estimated", "isEstimated"):
        value = _text_of(reading, name)
        if value is not None:
            return _parse_bool(value)
    reading_type = _text_of(reading, "ReadingType") or _text_of(reading, "readingType")
    if reading_type is not None and "estimat" in reading_type.lower():
        return True
    return False


def _readings(entry: ET.Element) -> list[tuple[StampSemantics, datetime, timedelta, float, bool]]:
    """Extract ``(semantics, stamp, duration, value, estimated)`` tuples."""
    results: list[tuple[StampSemantics, datetime, timedelta, float, bool]] = []
    for reading in entry.iter():
        if _local_name(reading.tag) != "Reading":
            continue
        interval = _find_local(reading, "interval")
        holder = interval if interval is not None else reading
        duration_text = _text_of(holder, "duration")
        value_text = _text_of(holder, "value")
        if duration_text is None or value_text is None:
            continue
        duration = _parse_duration(duration_text)
        start_text = _text_of(holder, "startTime")
        end_text = _text_of(holder, "endTime")
        semantics: StampSemantics
        if start_text is not None:
            semantics = "interval_start"
            stamp = _parse_stamp(start_text)
        elif end_text is not None:
            semantics = "interval_end"
            stamp = _parse_stamp(end_text)
        else:
            continue
        results.append(
            (semantics, stamp, duration, float(value_text), _reading_estimated(reading))
        )
    return results


def ingest_green_button_dmd(
    xml_text: str,
    *,
    timezone: str,
    fuel: Optional[Fuel] = None,
    kind: Optional[MeterKind] = None,
    meter_id: Optional[str] = None,
    source: str = DMD_SOURCE,
    terms_tag: str = DEFAULT_TERMS_TAG,
    retrieved_at_utc: Optional[datetime] = None,
) -> IngestResult:
    """Ingest a Green Button DMD-shaped (ESPI Atom/XML) document.

    Only the **monthly billing** subset is handled: readings whose interval
    block is at least 25 days long become billing-period points.  Interval
    readings (15-minute DMD blocks) are skipped and reported in the notes —
    interval ingest is issue #13, not this one.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise InsufficientData(
            f"DMD document is not well-formed XML: {exc}", reason="malformed_xml"
        ) from exc

    entries = [element for element in root.iter() if _local_name(element.tag) == "entry"]
    if not entries:
        raise InsufficientData(
            "DMD document has no Atom entries", reason="no_entries"
        )

    points: list[EnergyPoint] = []
    notes: list[str] = []
    skipped_intervals = 0
    semantics_seen: set[str] = set()
    fuels: list[Fuel] = []
    meters: list[str] = []
    durations: list[timedelta] = []

    for entry in entries:
        entry_fuel = _entry_fuel(entry)
        if entry_fuel is not None:
            fuels.append(entry_fuel)
        entry_meter = _entry_meter(entry)
        if entry_meter is not None:
            meters.append(entry_meter)
        for semantics, stamp, duration, value, estimated in _readings(entry):
            if duration < timedelta(days=BILLING_PERIOD_MIN_DAYS):
                skipped_intervals += 1
                continue
            semantics_seen.add(semantics)
            window = normalise_stamp(stamp, semantics, duration)
            durations.append(duration)
            points.append(
                EnergyPoint(
                    start_utc=window.start_utc,
                    end_utc=window.end_utc,
                    value=value,
                    unit="kWh",
                    estimated=estimated,
                )
            )

    if skipped_intervals:
        notes.append(
            f"{skipped_intervals} interval-granularity reading(s) ignored "
            "(interval ingest is issue #13)"
        )
    if not points:
        raise InsufficientData(
            "DMD document has no monthly billing readings",
            reason="no_billing_readings",
        )
    if len(semantics_seen) > 1:
        raise InsufficientData(
            "DMD document mixes interval-start and interval-end stamps",
            reason="mixed_stamp_semantics",
        )

    resolved_fuel = _resolve_fuel(fuel, fuels)
    resolved_kind = _resolve_kind(kind, [], resolved_fuel)
    resolved_meter = meter_id
    if resolved_meter is None and meters:
        unique_meters = sorted(set(meters))
        if len(unique_meters) == 1:
            resolved_meter = unique_meters[0]
        else:
            notes.append(
                "multiple meter ids in document; series meter_id left unset: "
                + ", ".join(unique_meters)
            )

    from_semantics: StampSemantics = "interval_end" if "interval_end" in semantics_seen else "interval_start"
    points.sort(key=lambda point: point.start_utc)

    nominal: Optional[int] = None
    lengths = {round(duration.total_seconds()) for duration in durations}
    if len(lengths) == 1:
        nominal = lengths.pop()

    if from_semantics == "interval_end":
        note = (
            "source reported interval-end stamps; each reading was shifted to "
            "[start, end) by subtracting the interval duration"
        )
    else:
        note = (
            "source reported interval-start stamps with an explicit duration; "
            "windows were kept as [start, end)"
        )

    estimated_count = sum(1 for point in points if point.estimated)
    if estimated_count:
        notes.append(f"{estimated_count} of {len(points)} billing periods are estimated")

    for point in points:
        local_start = point.start_utc.astimezone(ZoneInfo(timezone))
        if local_start.time() != time.min:
            notes.append(
                "billing period boundaries are not aligned to local midnight in "
                f"{timezone}; the house may be billed on a cycle"
            )
            break

    series = UtilitySeries(
        fuel=resolved_fuel,
        kind=resolved_kind,
        granularity="billing_period",
        points=points,
        stamp_semantics=from_semantics,
        provenance=_provenance(source, terms_tag, retrieved_at_utc),
        meter_id=resolved_meter,
    )
    conversion = StampConversionRecord(
        from_semantics=from_semantics,
        to_semantics="interval_start",
        nominal_duration_s=nominal,
        applied_at_utc=ensure_utc(retrieved_at_utc or datetime.now(UTC)),
        note=note,
    )
    return IngestResult(series=series, conversion=conversion, source=source, notes=notes)
