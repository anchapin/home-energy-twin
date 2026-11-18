"""Green Button DMD (ESPI XML) parser — Spike C (issue #5).

Reads a Green Button "Download My Data" (DMD) export and turns it into the
repository's own domain type :class:`home_energy_twin.types.UtilitySeries`.

Scope and rules honoured here:

* **Stdlib only** — ``xml.etree.ElementTree``; no new dependencies.
* **LAW-5** — every timestamp is converted to UTC at ingest.  The *basis* the
  export used (``Z``/UTC, a fixed offset, or mixed) is reported alongside the
  parsed series so the caller never has to guess it.
* **SPEC §2.2 / RD-6** — intervals are half-open ``[start, end)``; a billing
  period is a *point* with a long, irregular ``[start, end)``; the source's
  stamp semantics are recorded explicitly on the series.
* **SPEC §5** — failures raise the typed errors from
  ``home_energy_twin.errors``.  No bare ``ValueError`` crosses this boundary.
* **LAW-11** — the parser never fetches anything; the caller supplies the
  bytes.  No real user data is committed anywhere in this spike.

The parser is deliberately tolerant about *namespace prefixes* (real exports
use ``http://greenbutton.com/schema/1.0`` or the NAESB ESPI namespaces) and
strict about *meaning*: an interval without a resolvable end, a naive
timestamp, or an unmappable reading type is an error, not a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Optional
from xml.etree import ElementTree

from pydantic import ValidationError

from home_energy_twin.errors import (
    InsufficientData,
    ProvenanceMissing,
    UnidentifiableConfiguration,
)
from home_energy_twin.types import (
    EnergyPoint,
    Fuel,
    Granularity,
    MeterKind,
    Provenance,
    StampSemantics,
    UtilitySeries,
)

__all__ = [
    "CoverageIssue",
    "GreenButtonFile",
    "MeterReadSummary",
    "parse_dmd_bytes",
    "parse_dmd_file",
    "parse_dmd_text",
]


# --------------------------------------------------------------------------
# Small value objects
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageIssue:
    """A hole (``kind == "gap"``) or an overlap in an otherwise regular run."""

    kind: str
    meter_id: str
    after_end_utc: datetime
    before_start_utc: datetime
    duration: timedelta


@dataclass(frozen=True)
class MeterReadSummary:
    """What one ``MeterReading`` contributed, for the quirks report."""

    meter_id: str
    reading_type: str
    fuel: str
    kind: MeterKind
    unit: str
    granularity: Granularity
    stamp_semantics: StampSemantics
    point_count: int
    estimated_count: int
    total_value: float
    total_cost: Optional[float]
    currency: Optional[str]


@dataclass(frozen=True)
class GreenButtonFile:
    """The parsed result of one DMD export."""

    series: tuple[UtilitySeries, ...]
    reads: tuple[MeterReadSummary, ...]
    gaps: tuple[CoverageIssue, ...]
    overlaps: tuple[CoverageIssue, ...]
    timestamp_basis: str
    estimated_read_count: int
    point_count: int
    warnings: tuple[str, ...]
    provenance: Provenance

    def series_for(self, meter_id: str) -> tuple[UtilitySeries, ...]:
        """All series belonging to one meter (usage point)."""
        return tuple(s for s in self.series if s.meter_id == meter_id)


@dataclass(frozen=True)
class _Stamp:
    """One parsed timestamp plus the offset it arrived with."""

    utc: datetime
    offset_minutes: int
    explicit_z: bool


@dataclass(frozen=True)
class _RawRead:
    """One ``MeterReading`` reduced to points and semantics."""

    meter_id: str
    reading_type: str
    unit: str
    stamp_semantics: StampSemantics
    granularity: Granularity
    points: tuple[EnergyPoint, ...]
    estimated_count: int
    total_cost: Optional[float]
    currency: Optional[str]


# --------------------------------------------------------------------------
# Reference tables
# --------------------------------------------------------------------------

#: ESPI ``readingType`` -> (fuel, meter kind).  Anything not listed is
#: refused with :class:`UnidentifiableConfiguration` rather than guessed.
_READING_TYPES: Mapping[str, tuple[Fuel, MeterKind]] = {
    "electricity": ("electricity", "import"),
    "electricitynet": ("electricity", "net"),
    "electricityexport": ("electricity", "export"),
    "gas": ("natural_gas", "delivered"),
    "naturalgas": ("natural_gas", "delivered"),
    "heatinggas": ("natural_gas", "delivered"),
    "steam": ("district", "delivered"),
    "district": ("district", "delivered"),
    "districtsteam": ("district", "delivered"),
}

#: Fallback unit when the export omits ``unitOfMeasure``.  Never silent: the
#: caller gets a warning naming the assumption.
_DEFAULT_UNITS: Mapping[str, str] = {
    "electricity": "kWh",
    "electricitynet": "kWh",
    "electricityexport": "kWh",
    "gas": "CCF",
    "naturalgas": "CCF",
    "heatinggas": "CCF",
    "steam": "MMBtu",
    "district": "MMBtu",
    "districtsteam": "MMBtu",
}

_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)

_INTERVAL_ELEMENTS = ("IntervalBlock", "Interval")
_PERIOD_ELEMENTS = ("Summation", "Reading", "BillingPeriod")


# --------------------------------------------------------------------------
# XML helpers — namespace-agnostic, local-name based
# --------------------------------------------------------------------------


def _local(tag: str) -> str:
    """Strip the ``{namespace}`` prefix from an element tag."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _children(element: ElementTree.Element, *names: str) -> list[ElementTree.Element]:
    wanted = {name.lower() for name in names}
    return [child for child in element if _local(child.tag).lower() in wanted]


def _first_child(
    element: ElementTree.Element, *names: str
) -> Optional[ElementTree.Element]:
    children = _children(element, *names)
    return children[0] if children else None


def _text(element: Optional[ElementTree.Element]) -> Optional[str]:
    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None


def _child_text(element: ElementTree.Element, *names: str) -> Optional[str]:
    return _text(_first_child(element, *names))


# --------------------------------------------------------------------------
# Scalar parsing
# --------------------------------------------------------------------------


def _parse_stamp(raw: str) -> _Stamp:
    """Parse an ESPI timestamp into UTC, remembering the offset it carried."""
    text = raw.strip()
    candidate = text
    explicit_z = False
    if candidate.endswith(("Z", "z")):
        explicit_z = True
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = _parse_stamp_fallback(candidate)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InsufficientData(
            f"timestamp {raw!r} carries no UTC offset; DMD exports must be "
            "offset-aware (LAW-5)",
            reason="naive_timestamp",
        )
    offset = parsed.utcoffset() or timedelta(0)
    return _Stamp(
        utc=parsed.astimezone(timezone.utc),
        offset_minutes=int(offset.total_seconds() // 60),
        explicit_z=explicit_z,
    )


def _parse_stamp_fallback(candidate: str) -> datetime:
    """Last-resort ISO-8601 parse for shapes ``fromisoformat`` rejects."""
    match = re.match(
        r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}(?::\d{2})?)"
        r"(?:\.(?P<fraction>\d+))?(?P<offset>Z|z|[+-]\d{2}:?\d{2})?$",
        candidate,
    )
    if match is None:
        raise InsufficientData(
            f"timestamp {candidate!r} is not an ISO-8601 instant",
            reason="unparseable_timestamp",
        )
    date = match.group("date")
    time = match.group("time")
    if len(time) == 5:
        time = f"{time}:00"
    fraction = match.group("fraction")
    micro = 0
    if fraction:
        micro = int((fraction + "000000")[:6])
    offset = match.group("offset")
    if offset is None:
        base = f"{date}T{time}.{micro:06d}"
    elif offset in ("Z", "z"):
        base = f"{date}T{time}.{micro:06d}+00:00"
    else:
        normalised = offset
        if ":" not in normalised:
            normalised = f"{normalised[:3]}:{normalised[3:]}"
        base = f"{date}T{time}.{micro:06d}{normalised}"
    try:
        return datetime.fromisoformat(base)
    except ValueError as exc:  # pragma: no cover - defensive
        raise InsufficientData(
            f"timestamp {candidate!r} is not an ISO-8601 instant",
            reason="unparseable_timestamp",
        ) from exc


def _parse_duration(raw: str) -> timedelta:
    """Parse an ESPI duration (``PT1H``, ``P1D``, or a seconds count)."""
    text = raw.strip()
    match = _ISO_DURATION.match(text)
    if match is not None and any(
        match.group(part) for part in ("days", "hours", "minutes", "seconds")
    ):
        days = float(match.group("days") or 0.0)
        hours = float(match.group("hours") or 0.0)
        minutes = float(match.group("minutes") or 0.0)
        seconds = float(match.group("seconds") or 0.0)
        return timedelta(
            days=days, hours=hours, minutes=minutes, seconds=seconds
        )
    try:
        return timedelta(seconds=float(text))
    except ValueError as exc:
        raise InsufficientData(
            f"interval duration {raw!r} is not an ISO-8601 duration",
            reason="unparseable_duration",
        ) from exc


def _parse_float(raw: str, *, what: str) -> float:
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise InsufficientData(
            f"{what} {raw!r} is not a number", reason="unparseable_value"
        ) from exc


def _granularity_for(duration: timedelta) -> Granularity:
    """Map a nominal interval length onto the repository's granularity scale."""
    minutes = duration.total_seconds() / 60.0
    if minutes <= 15.0:
        return "subhourly"
    if minutes <= 60.0:
        return "hourly"
    if minutes <= 24.0 * 60.0:
        return "daily"
    return "billing_period"


# --------------------------------------------------------------------------
# Reading parsing
# --------------------------------------------------------------------------


class _Parser:
    """Internal parse state (one DMD export)."""

    def __init__(self) -> None:
        self.stamps: list[_Stamp] = []
        self.warnings: list[str] = []
        self.reads: list[_RawRead] = []

    # -- timestamps ------------------------------------------------------

    def stamp(self, raw: str) -> _Stamp:
        parsed = _parse_stamp(raw)
        self.stamps.append(parsed)
        return parsed

    # -- intervals -------------------------------------------------------

    def _interval_from(
        self,
        element: ElementTree.Element,
        *,
        start_raw: Optional[str],
        end_raw: Optional[str],
        duration_raw: Optional[str],
        unit: str,
        default_estimated: bool,
    ) -> tuple[EnergyPoint, StampSemantics, Optional[float], Optional[str]]:
        """Resolve one reading's ``[start, end)`` bounds and its value."""
        start: Optional[_Stamp] = None
        end: Optional[_Stamp] = None
        duration: Optional[timedelta] = None

        if start_raw:
            start = self.stamp(start_raw)
        if end_raw:
            end = self.stamp(end_raw)
        if duration_raw:
            duration = _parse_duration(duration_raw)

        semantics: StampSemantics
        if start is not None and end is not None:
            if end.utc <= start.utc:
                raise InsufficientData(
                    "interval end is not after its start",
                    reason="inverted_interval",
                )
            semantics = "interval_start"
            window_start, window_end = start.utc, end.utc
        elif start is not None and duration is not None:
            if duration <= timedelta(0):
                raise InsufficientData(
                    "interval duration must be positive",
                    reason="nonpositive_duration",
                )
            semantics = "interval_start"
            window_start = start.utc
            window_end = start.utc + duration
        elif end is not None and duration is not None:
            if duration <= timedelta(0):
                raise InsufficientData(
                    "interval duration must be positive",
                    reason="nonpositive_duration",
                )
            semantics = "interval_end"
            window_end = end.utc
            window_start = end.utc - duration
        else:
            raise InsufficientData(
                "interval has neither a resolvable end nor a duration; the "
                "export cannot be read without guessing (LAW-5/RD-6)",
                reason="unbounded_interval",
            )

        value_raw = _child_text(element, "value", "readingValue")
        if value_raw is None:
            raise InsufficientData(
                "interval reading has no value", reason="missing_value"
            )
        value = _parse_float(value_raw, what="interval value")

        estimated = default_estimated
        quality = _child_text(element, "dataQuality", "quality")
        if quality is None:
            quality = element.attrib.get("quality")
        if quality is not None:
            estimated = quality.strip().lower() in ("estimated", "estimate", "imputed")

        cost: Optional[float] = None
        currency: Optional[str] = None
        cost_element = _first_child(element, "cost")
        if cost_element is not None:
            cost_text = _text(cost_element)
            if cost_text is not None:
                cost = _parse_float(cost_text, what="interval cost")
            currency = cost_element.attrib.get("currency")

        try:
            point = EnergyPoint(
                start_utc=window_start,
                end_utc=window_end,
                value=value,
                unit=unit,
                estimated=estimated,
            )
        except ValidationError as exc:
            raise InsufficientData(
                f"interval reading is not a valid energy point: {exc}",
                reason="invalid_point",
            ) from exc
        return point, semantics, cost, currency

    def _parse_interval_block(
        self,
        block: ElementTree.Element,
        *,
        unit: str,
        default_estimated: bool,
    ) -> tuple[list[EnergyPoint], list[StampSemantics], Optional[float], Optional[str]]:
        points: list[EnergyPoint] = []
        semantics: list[StampSemantics] = []
        costs: list[float] = []
        currency: Optional[str] = None

        block_start = block.attrib.get("startTime")
        block_end = block.attrib.get("endTime")
        block_duration = block.attrib.get("duration")
        if block_duration is None and block_start and block_end:
            block_duration = str(
                (self.stamp(block_end).utc - self.stamp(block_start).utc).total_seconds()
            )

        readings = _children(block, "IntervalReading")
        if not readings:
            return points, semantics, None, None
        for reading in readings:
            point, sem, cost, curr = self._interval_from(
                reading,
                start_raw=reading.attrib.get("startTime") or block_start,
                end_raw=reading.attrib.get("endTime"),
                duration_raw=reading.attrib.get("duration") or block_duration,
                unit=unit,
                default_estimated=default_estimated,
            )
            points.append(point)
            semantics.append(sem)
            if cost is not None:
                costs.append(cost)
            if curr is not None:
                currency = curr
        total_cost = sum(costs) if costs else None
        return points, semantics, total_cost, currency

    def _parse_period_element(
        self,
        element: ElementTree.Element,
        *,
        unit: str,
        default_estimated: bool,
    ) -> tuple[list[EnergyPoint], list[StampSemantics], Optional[float], Optional[str]]:
        """Parse a summation/billing-shaped element with explicit bounds."""
        point, sem, cost, currency = self._interval_from(
            element,
            start_raw=element.attrib.get("startTime"),
            end_raw=element.attrib.get("endTime"),
            duration_raw=element.attrib.get("duration"),
            unit=unit,
            default_estimated=default_estimated,
        )
        total_cost = cost
        return [point], [sem], total_cost, currency

    def parse_meter_reading(
        self, reading: ElementTree.Element, meter_id: str
    ) -> list[_RawRead]:
        reading_type_raw = _child_text(reading, "readingType")
        if reading_type_raw is None:
            reading_type_raw = reading.attrib.get("readingType")
        if reading_type_raw is None:
            self.warnings.append(
                "MeterReading without readingType was skipped"
            )
            return []
        reading_type = reading_type_raw.strip().lower()

        unit = _child_text(reading, "unitOfMeasure") or _child_text(
            reading, "unit"
        )
        if unit is not None and not unit.strip():
            unit = None
        if unit is None:
            unit = _DEFAULT_UNITS.get(reading_type)
            if unit is None:
                raise InsufficientData(
                    f"readingType {reading_type!r} has no unit and no known default",
                    reason="missing_unit",
                )
            self.warnings.append(
                f"readingType {reading_type!r} had no unitOfMeasure; "
                f"assumed {unit!r}"
            )

        quality = reading.attrib.get("quality") or _child_text(reading, "quality")
        default_estimated = bool(
            quality and quality.strip().lower() in ("estimated", "estimate")
        )

        interval_points: list[EnergyPoint] = []
        interval_semantics: list[StampSemantics] = []
        interval_cost: Optional[float] = None
        interval_currency: Optional[str] = None
        period_points: list[EnergyPoint] = []
        period_semantics: list[StampSemantics] = []
        period_cost: Optional[float] = None
        period_currency: Optional[str] = None

        for child in reading:
            name = _local(child.tag).lower()
            if name in {n.lower() for n in _INTERVAL_ELEMENTS}:
                block_points, block_sem, block_cost, block_currency = (
                    self._parse_interval_block(
                        child, unit=unit, default_estimated=default_estimated
                    )
                )
                interval_points.extend(block_points)
                interval_semantics.extend(block_sem)
                if block_cost is not None:
                    interval_cost = (interval_cost or 0.0) + block_cost
                interval_currency = interval_currency or block_currency
            elif name in {n.lower() for n in _PERIOD_ELEMENTS}:
                element_points, element_sem, element_cost, element_currency = (
                    self._parse_period_element(
                        child, unit=unit, default_estimated=default_estimated
                    )
                )
                period_points.extend(element_points)
                period_semantics.extend(element_sem)
                if element_cost is not None:
                    period_cost = (period_cost or 0.0) + element_cost
                period_currency = period_currency or element_currency

        if not interval_points and not period_points:
            self.warnings.append(
                f"MeterReading {reading_type!r} carried no interval or summation data"
            )
            return []

        reads: list[_RawRead] = []
        interval_read = self._make_read(
            meter_id=meter_id,
            reading_type=reading_type,
            unit=unit,
            points=interval_points,
            semantics=interval_semantics,
            cost=interval_cost,
            currency=interval_currency,
            granularity=None,
        )
        if interval_read is not None:
            reads.append(interval_read)
        period_read = self._make_read(
            meter_id=meter_id,
            reading_type=reading_type,
            unit=unit,
            points=period_points,
            semantics=period_semantics,
            cost=period_cost,
            currency=period_currency,
            granularity="billing_period",
        )
        if period_read is not None:
            reads.append(period_read)
        return reads

    def _make_read(
        self,
        *,
        meter_id: str,
        reading_type: str,
        unit: str,
        points: list[EnergyPoint],
        semantics: list[StampSemantics],
        cost: Optional[float],
        currency: Optional[str],
        granularity: Optional[Granularity],
    ) -> Optional[_RawRead]:
        """Build one read, or ``None`` when the shape carried no points."""
        if not points:
            return None
        if len(set(semantics)) > 1:
            self.warnings.append(
                f"MeterReading {reading_type!r} mixes stamp semantics "
                f"{sorted(set(semantics))}; the first is used for the series"
            )
        resolved: Granularity = (
            granularity if granularity is not None else self._granularity_of(points)
        )
        return _RawRead(
            meter_id=meter_id,
            reading_type=reading_type,
            unit=unit,
            stamp_semantics=semantics[0],
            granularity=resolved,
            points=tuple(points),
            estimated_count=sum(1 for point in points if point.estimated),
            total_cost=cost,
            currency=currency,
        )

    def _granularity_of(self, points: list[EnergyPoint]) -> Granularity:
        """Granularity of a read: the modal interval length wins."""
        durations = [p.duration for p in points]
        longest = max(durations)
        if longest > timedelta(days=1):
            return "billing_period"
        counts: dict[timedelta, int] = {}
        for duration in durations:
            counts[duration] = counts.get(duration, 0) + 1
        modal = max(counts.items(), key=lambda item: (item[1], -item[0].total_seconds()))[0]
        return _granularity_for(modal)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def parse_dmd_text(
    text: str,
    *,
    retrieved_at_utc: Optional[datetime] = None,
    source: Optional[str] = None,
    terms_tag: Optional[str] = None,
) -> GreenButtonFile:
    """Parse a DMD export held in a string into ``UtilitySeries`` objects.

    ``source`` / ``terms_tag`` override what the export itself states; when
    omitted they are taken from the export (``MeterReading/source``) or, for
    the terms, defaulted to ``"unspecified"`` with a warning — a Green Button
    export carries no licence tag.
    """
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise InsufficientData(
            "DTD/entity declarations are not accepted in a DMD export",
            reason="dtd_not_allowed",
        )
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise InsufficientData(
            f"DMD export is not well-formed XML: {exc}", reason="malformed_xml"
        ) from exc

    parser = _Parser()

    usage_points = [el for el in root.iter() if _local(el.tag).lower() == "usagepoint"]
    reads: list[_RawRead] = []

    if usage_points:
        for usage_point in usage_points:
            meter_id = usage_point.attrib.get("id") or _child_text(
                usage_point, "id"
            ) or "unknown-meter"
            for reading in usage_point.iter():
                if _local(reading.tag).lower() != "meterreading":
                    continue
                reads.extend(parser.parse_meter_reading(reading, meter_id))
    else:
        meter_id = "unknown-meter"
        for reading in root.iter():
            if _local(reading.tag).lower() == "meterreading":
                reads.extend(parser.parse_meter_reading(reading, meter_id))

    if not reads:
        raise InsufficientData(
            "DMD export contains no MeterReading with usable data",
            reason="no_readings",
        )

    file_source = source
    if file_source is None:
        for element in root.iter():
            if _local(element.tag).lower() == "source":
                text_value = _text(element)
                if text_value:
                    file_source = f"green_button_dmd:{text_value}"
                    break
    if file_source is None:
        raise ProvenanceMissing(
            "DMD export states no source; provenance cannot be recorded (LAW-12)",
            reason="missing_source",
        )

    warnings = list(parser.warnings)
    effective_terms = terms_tag or "unspecified"
    if terms_tag is None:
        warnings.append(
            "no terms tag was supplied; a Green Button DMD export carries no "
            "licence information (LAW-12) — set the utility's terms on ingest"
        )

    retrieved = retrieved_at_utc or datetime.now(timezone.utc)
    if retrieved.tzinfo is None or retrieved.utcoffset() != timedelta(0):
        raise InsufficientData(
            "retrieved_at_utc must be timezone-aware UTC (LAW-5)",
            reason="naive_retrieval_time",
        )
    provenance = Provenance(
        source=file_source,
        retrieved_at_utc=retrieved.astimezone(timezone.utc),
        terms_tag=effective_terms,
    )

    series, gaps, overlaps = _build_series(reads, provenance)

    estimated_total = sum(read.estimated_count for read in reads)
    point_total = sum(len(read.points) for read in reads)

    summaries = tuple(
        MeterReadSummary(
            meter_id=read.meter_id,
            reading_type=read.reading_type,
            fuel=_fuel_for(read.reading_type),
            kind=_kind_for(read.reading_type),
            unit=read.unit,
            granularity=read.granularity,
            stamp_semantics=read.stamp_semantics,
            point_count=len(read.points),
            estimated_count=read.estimated_count,
            total_value=sum(p.value for p in read.points),
            total_cost=read.total_cost,
            currency=read.currency,
        )
        for read in reads
    )

    return GreenButtonFile(
        series=tuple(series),
        reads=summaries,
        gaps=tuple(gaps),
        overlaps=tuple(overlaps),
        timestamp_basis=_timestamp_basis(parser.stamps),
        estimated_read_count=estimated_total,
        point_count=point_total,
        warnings=tuple(warnings),
        provenance=provenance,
    )


def parse_dmd_bytes(
    payload: bytes,
    **kwargs: object,
) -> GreenButtonFile:
    """Parse a DMD export held in bytes."""
    return parse_dmd_text(payload.decode("utf-8-sig"), **kwargs)  # type: ignore[arg-type]


def parse_dmd_file(
    path: Path | str,
    **kwargs: object,
) -> GreenButtonFile:
    """Parse a DMD export held on disk (the owner's file stays local)."""
    return parse_dmd_bytes(Path(path).read_bytes(), **kwargs)


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _fuel_for(reading_type: str) -> Fuel:
    mapping = _READING_TYPES.get(reading_type.lower())
    if mapping is None:
        raise UnidentifiableConfiguration(
            f"readingType {reading_type!r} has no fuel/meter mapping",
            reason=f"unsupported_reading_type:{reading_type}",
        )
    return mapping[0]


def _kind_for(reading_type: str) -> MeterKind:
    mapping = _READING_TYPES.get(reading_type.lower())
    if mapping is None:
        raise UnidentifiableConfiguration(
            f"readingType {reading_type!r} has no fuel/meter mapping",
            reason=f"unsupported_reading_type:{reading_type}",
        )
    return mapping[1]


def _timestamp_basis(stamps: list[_Stamp]) -> str:
    """Name the timestamp basis the export used."""
    if not stamps:
        return "none"
    offsets = {stamp.offset_minutes for stamp in stamps}
    if offsets == {0}:
        return "utc_z" if all(s.explicit_z for s in stamps) else "utc_offset_zero"
    if len(offsets) == 1:
        return "fixed_local_offset"
    return "mixed_offsets"


def _build_series(
    reads: list[_RawRead], provenance: Provenance
) -> tuple[list[UtilitySeries], list[CoverageIssue], list[CoverageIssue]]:
    """Group reads into ``UtilitySeries`` and find gaps/overlaps."""
    groups: dict[tuple[str, str, str, Granularity], list[_RawRead]] = {}
    for read in reads:
        key = (read.meter_id, read.reading_type, read.unit, read.granularity)
        groups.setdefault(key, []).append(read)

    series: list[UtilitySeries] = []
    gaps: list[CoverageIssue] = []
    overlaps: list[CoverageIssue] = []

    for key in sorted(groups):
        group = groups[key]
        meter_id, reading_type, unit, granularity = key
        points: list[EnergyPoint] = []
        semantics: StampSemantics = group[0].stamp_semantics
        for read in group:
            points.extend(read.points)
        points.sort(key=lambda point: point.start_utc)

        series.append(
            UtilitySeries(
                fuel=_fuel_for(reading_type),
                kind=_kind_for(reading_type),
                granularity=granularity,
                points=points,
                stamp_semantics=semantics,
                provenance=provenance,
                meter_id=meter_id,
            )
        )

        for previous, current in zip(points, points[1:]):
            if current.start_utc > previous.end_utc:
                gaps.append(
                    CoverageIssue(
                        kind="gap",
                        meter_id=meter_id,
                        after_end_utc=previous.end_utc,
                        before_start_utc=current.start_utc,
                        duration=current.start_utc - previous.end_utc,
                    )
                )
            elif current.start_utc < previous.end_utc:
                overlaps.append(
                    CoverageIssue(
                        kind="overlap",
                        meter_id=meter_id,
                        after_end_utc=previous.end_utc,
                        before_start_utc=current.start_utc,
                        duration=previous.end_utc - current.start_utc,
                    )
                )

    return series, gaps, overlaps
