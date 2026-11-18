"""Interval and billing-period arithmetic (RD-6, LAW-5).

Semantics implemented here:

* An interval is ``[start, end)``: it contains its start and excludes its
  end.  A point at ``end`` belongs to the next interval.
* A billing period is *a point* (an :class:`~home_energy_twin.types.EnergyPoint`)
  carrying a long, irregular ``[start, end)`` — there is no separate monthly
  type (SPEC §2.2, RD-6).
* A source may report ``interval_start`` or ``interval_end`` stamps; both are
  normalised to ``[start, end)`` at ingest and the conversion is recorded.
* Slicing never silently rescales energy: only points wholly inside the
  requested window are returned, and partial coverage is reported.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence

from .types import EnergyPoint, StampSemantics, TimeWindow, UtilitySeries

__all__ = [
    "StampConversion",
    "billing_period_windows",
    "contains_point",
    "normalise_points",
    "normalise_series",
    "normalise_stamp",
    "normalise_stamps",
    "points_cover_window",
    "slice_points",
    "slice_series",
    "split_interval",
]


class StampConversion:
    """Record of a stamp-semantics normalisation applied at ingest."""

    __slots__ = ("from_semantics", "to_semantics", "nominal_duration", "applied_at_utc")

    def __init__(
        self,
        from_semantics: StampSemantics,
        to_semantics: StampSemantics,
        nominal_duration: timedelta,
        applied_at_utc: datetime,
    ) -> None:
        self.from_semantics = from_semantics
        self.to_semantics = to_semantics
        self.nominal_duration = nominal_duration
        self.applied_at_utc = applied_at_utc

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"StampConversion(from_semantics={self.from_semantics!r}, "
            f"to_semantics={self.to_semantics!r}, "
            f"nominal_duration={self.nominal_duration!r})"
        )


def normalise_stamp(
    stamp: datetime,
    semantics: StampSemantics,
    duration: timedelta,
) -> TimeWindow:
    """Turn a single source stamp into a half-open ``[start, end)`` window.

    ``interval_start``: the stamp is the interval start, so the end is
    ``stamp + duration``.  ``interval_end``: the stamp is the interval end, so
    the start is ``stamp - duration``.
    """
    if duration <= timedelta(0):
        raise ValueError("interval duration must be positive")
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("source stamps must be UTC before normalisation (LAW-5)")
    if semantics == "interval_start":
        return TimeWindow(start_utc=stamp, end_utc=stamp + duration)
    return TimeWindow(start_utc=stamp - duration, end_utc=stamp)


def normalise_stamps(
    stamps: Iterable[datetime],
    semantics: StampSemantics,
    duration: timedelta,
) -> list[TimeWindow]:
    """Normalise a run of source stamps into half-open windows."""
    return [normalise_stamp(stamp, semantics, duration) for stamp in stamps]


def normalise_points(
    points: Sequence[EnergyPoint],
    semantics: StampSemantics,
    duration: timedelta,
) -> list[EnergyPoint]:
    """Rewrite points so their bounds follow ``[start, end)`` for ``semantics``.

    The stamp the source reported is authoritative; the other bound is
    derived from the nominal duration.
    """
    normalised: list[EnergyPoint] = []
    for point in points:
        if semantics == "interval_start":
            start, end = point.start_utc, point.start_utc + duration
        else:
            end, start = point.end_utc, point.end_utc - duration
        normalised.append(
            EnergyPoint(
                start_utc=start,
                end_utc=end,
                value=point.value,
                unit=point.unit,
                estimated=point.estimated,
            )
        )
    return normalised


def normalise_series(
    series: UtilitySeries,
    duration: timedelta,
    applied_at_utc: datetime,
) -> tuple[UtilitySeries, StampConversion]:
    """Normalise a whole series to ``[start, end)`` and record the conversion.

    The series keeps the source's reported ``stamp_semantics`` (SPEC §2.2);
    the conversion that was applied is returned alongside it.
    """
    points = normalise_points(series.points, series.stamp_semantics, duration)
    conversion = StampConversion(
        from_semantics=series.stamp_semantics,
        to_semantics="interval_start",
        nominal_duration=duration,
        applied_at_utc=applied_at_utc,
    )
    normalised = series.model_copy(update={"points": points})
    return normalised, conversion


def contains_point(window: TimeWindow, point: EnergyPoint) -> bool:
    """True when ``point`` lies wholly inside ``window`` (``[start, end)``)."""
    return window.start_utc <= point.start_utc and point.end_utc <= window.end_utc


def slice_points(points: Sequence[EnergyPoint], window: TimeWindow) -> list[EnergyPoint]:
    """Return the points wholly inside ``window``.

    Energy is never rescaled to fit a window: a point that only partly
    overlaps is dropped, and the caller is expected to notice the gap.
    """
    return [point for point in points if contains_point(window, point)]


def slice_series(series: UtilitySeries, window: TimeWindow) -> UtilitySeries:
    """Slice a series to the points wholly inside ``window``."""
    return series.model_copy(update={"points": slice_points(series.points, window)})


def points_cover_window(points: Sequence[EnergyPoint], window: TimeWindow) -> bool:
    """True when the points cover ``window`` with no gap and no overlap."""
    cursor = window.start_utc
    for point in points:
        if point.end_utc <= cursor:
            continue
        if point.start_utc > cursor:
            return False  # a gap in the coverage
        cursor = max(cursor, point.end_utc)
        if cursor >= window.end_utc:
            return True
    return cursor >= window.end_utc


def billing_period_windows(points: Sequence[EnergyPoint]) -> list[TimeWindow]:
    """The windows of a billing-period series (long, irregular intervals)."""
    return [TimeWindow(start_utc=p.start_utc, end_utc=p.end_utc) for p in points]


def split_interval(
    window: TimeWindow,
    boundaries: Sequence[datetime],
) -> list[TimeWindow]:
    """Split ``window`` at the given UTC boundaries.

    Boundaries outside the window are ignored; the result is a cover of the
    window by contiguous half-open pieces.
    """
    inside = sorted({b for b in boundaries if window.start_utc < b < window.end_utc})
    pieces: list[TimeWindow] = []
    cursor = window.start_utc
    for boundary in inside:
        pieces.append(TimeWindow(start_utc=cursor, end_utc=boundary))
        cursor = boundary
    pieces.append(TimeWindow(start_utc=cursor, end_utc=window.end_utc))
    return pieces


def first_point_start(points: Sequence[EnergyPoint]) -> Optional[datetime]:
    """Start of the first point, if any."""
    return points[0].start_utc if points else None
