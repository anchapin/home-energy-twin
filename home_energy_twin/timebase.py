"""UTC / local-time plumbing (LAW-5, RD-6).

Rules implemented here:

* Every stored or computed timestamp is UTC.  :func:`ensure_utc` is the
  single gate; the domain models enforce it through ``UTCDateTime``.
* Local time exists only at ingest/display edges, and is derived from a
  house's stored IANA timezone via :func:`to_local` / :func:`from_local`.
* DST-transition days are valid inputs, not errors: a local time inside a
  spring-forward gap is resolved forward past the gap, and an ambiguous
  autumn time is resolved to the first (pre-transition) occurrence unless an
  explicit ``fold`` is requested.
* Leap days are ordinary days: :func:`local_day_window` returns a 24-hour
  window for February 29th, and 23/25-hour windows on DST transition days.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .types import TimeWindow

__all__ = [
    "ensure_utc",
    "from_local",
    "is_ambiguous_local",
    "is_nonexistent_local",
    "local_day_window",
    "local_midnights",
    "to_local",
]


def ensure_utc(moment: datetime) -> datetime:
    """Return ``moment`` as an aware UTC datetime; reject naive input."""
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("naive datetime is not allowed (LAW-5): timestamps are UTC")
    return moment.astimezone(timezone.utc)


def _zone(timezone_name: str) -> ZoneInfo:
    return ZoneInfo(timezone_name)


def to_local(moment: datetime, timezone_name: str) -> datetime:
    """Convert a UTC instant to house-local time (display edge)."""
    return ensure_utc(moment).astimezone(_zone(timezone_name))


def _gap_size(naive: datetime, zone: ZoneInfo) -> timedelta:
    """Size of the spring-forward gap that swallows ``naive`` (0 if not in one)."""
    before = naive.replace(tzinfo=zone, fold=0).utcoffset()
    after = naive.replace(tzinfo=zone, fold=1).utcoffset()
    if before is None or after is None:  # pragma: no cover - defensive
        return timedelta(0)
    size = after - before
    return size if size > timedelta(0) else timedelta(0)


def is_nonexistent_local(naive: datetime, timezone_name: str) -> bool:
    """True when the wall-clock time does not exist in ``timezone_name``."""
    zone = _zone(timezone_name)
    if naive.tzinfo is not None:
        raise ValueError("expected a naive local datetime")
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            return False
    return True


def is_ambiguous_local(naive: datetime, timezone_name: str) -> bool:
    """True when the wall-clock time occurs twice in ``timezone_name``."""
    zone = _zone(timezone_name)
    if naive.tzinfo is not None:
        raise ValueError("expected a naive local datetime")
    round_trips = 0
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            round_trips += 1
    return round_trips == 2


def from_local(naive: datetime, timezone_name: str, *, fold: int = 0) -> datetime:
    """Convert a house-local wall-clock time to a UTC instant.

    * Ambiguous times (autumn fall-back) resolve to ``fold=0`` (the first
      occurrence) unless ``fold=1`` is requested.
    * Nonexistent times (spring-forward gap) resolve forward past the gap to
      the first valid local instant — a DST day is data, not an error.
    """
    if naive.tzinfo is not None:
        raise ValueError("from_local expects a naive local datetime")
    zone = _zone(timezone_name)

    round_trip_folds = []
    for candidate in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=candidate)
        instant = aware.astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            round_trip_folds.append(candidate)
    if round_trip_folds:
        chosen = 1 if fold == 1 and 1 in round_trip_folds else 0
        return naive.replace(tzinfo=zone, fold=chosen).astimezone(timezone.utc)

    gap = _gap_size(naive, zone)
    shifted = naive + gap
    return shifted.replace(tzinfo=zone, fold=1).astimezone(timezone.utc)


def local_midnights(
    first_day: date,
    last_day: date,
    timezone_name: str,
) -> list[datetime]:
    """UTC instants of local midnight for each day in ``[first_day, last_day]``."""
    if last_day < first_day:
        raise ValueError("last_day must not precede first_day")
    zone = _zone(timezone_name)
    midnights: list[datetime] = []
    day = first_day
    while day <= last_day:
        midnights.append(
            datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)
        )
        day += timedelta(days=1)
    return midnights


def local_day_window(day: date, timezone_name: str) -> TimeWindow:
    """The local calendar day ``day`` as a half-open UTC window.

    The window is 23, 24 or 25 hours long depending on the DST transition.
    """
    zone = _zone(timezone_name)
    start = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)
    # The next local midnight, computed DST-aware.
    next_day = day + timedelta(days=1)
    end = datetime(
        next_day.year, next_day.month, next_day.day, tzinfo=zone
    ).astimezone(timezone.utc)
    return TimeWindow(start_utc=start, end_utc=end)


def utc_window_to_local(window: TimeWindow, timezone_name: str) -> tuple[datetime, datetime]:
    """Convenience: a window's bounds expressed in house-local time."""
    return to_local(window.start_utc, timezone_name), to_local(window.end_utc, timezone_name)


def optional_local(
    naive: Optional[datetime], timezone_name: str
) -> Optional[datetime]:  # pragma: no cover - trivial helper
    if naive is None:
        return None
    return from_local(naive, timezone_name)
