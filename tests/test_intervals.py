"""Property tests for interval / billing-period plumbing (RD-6, LAW-5).

Covered edges (named in SPEC §6 / RD-6):

* DST spring-forward and fall-back days (23-hour and 25-hour local days).
* Leap day (February 29th) arithmetic and window lengths.
* Interval start/end stamp semantics and the ``[start, end)`` half-open rule.
* Billing-period slicing (long, irregular intervals; no monthly type).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from home_energy_twin.intervals import (
    billing_period_windows,
    contains_point,
    normalise_points,
    normalise_stamp,
    normalise_stamps,
    points_cover_window,
    slice_points,
    slice_series,
    split_interval,
)
from home_energy_twin.timebase import (
    from_local,
    is_ambiguous_local,
    is_nonexistent_local,
    local_day_window,
    to_local,
)
from home_energy_twin.types import EnergyPoint, Provenance, UtilitySeries

UTC = timezone.utc

DST_ZONES = ["America/New_York", "Europe/Berlin", "Australia/Sydney"]

utc_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1, tzinfo=UTC),
    max_value=datetime(2035, 1, 1, tzinfo=UTC),
    timezones=st.just(UTC),
)

positive_durations = st.timedeltas(
    min_value=timedelta(minutes=1), max_value=timedelta(days=45)
)


def _point(start: datetime, duration: timedelta, value: float = 1.0) -> EnergyPoint:
    return EnergyPoint(
        start_utc=start,
        end_utc=start + duration,
        value=value,
        unit="kWh",
        estimated=False,
    )


def _provenance() -> Provenance:
    return Provenance(
        source="synthetic-fixture",
        retrieved_at_utc=datetime(2024, 1, 1, tzinfo=UTC),
        terms_tag="CC0-1.0",
    )


# --------------------------------------------------------------------------
# [start, end) semantics
# --------------------------------------------------------------------------


@given(start=utc_datetimes, duration=positive_durations)
def test_intervals_are_half_open(start: datetime, duration: timedelta) -> None:
    point = _point(start, duration)
    assert point.start_utc < point.end_utc
    assert point.duration == duration


@given(start=utc_datetimes, duration=positive_durations)
def test_window_contains_excludes_its_end(start: datetime, duration: timedelta) -> None:
    from home_energy_twin.types import TimeWindow

    window = TimeWindow(start_utc=start, end_utc=start + duration)
    assert window.contains(start)
    assert not window.contains(start + duration)
    assert not window.contains(start - timedelta(microseconds=1))


@given(start=utc_datetimes, duration=positive_durations)
def test_slicing_keeps_only_wholly_contained_points(start: datetime, duration: timedelta) -> None:
    from home_energy_twin.types import TimeWindow

    points = [_point(start, duration), _point(start + duration, duration)]
    window = TimeWindow(start_utc=start, end_utc=start + duration)
    sliced = slice_points(points, window)
    assert len(sliced) == 1
    assert contains_point(window, sliced[0])
    for point in sliced:
        assert window.start_utc <= point.start_utc
        assert point.end_utc <= window.end_utc


# --------------------------------------------------------------------------
# Stamp semantics
# --------------------------------------------------------------------------


@given(stamp=utc_datetimes, duration=positive_durations)
def test_interval_start_stamp_is_the_left_edge(stamp: datetime, duration: timedelta) -> None:
    window = normalise_stamp(stamp, "interval_start", duration)
    assert window.start_utc == stamp
    assert window.end_utc == stamp + duration
    assert window.duration == duration


@given(stamp=utc_datetimes, duration=positive_durations)
def test_interval_end_stamp_is_the_right_edge(stamp: datetime, duration: timedelta) -> None:
    window = normalise_stamp(stamp, "interval_end", duration)
    assert window.end_utc == stamp
    assert window.start_utc == stamp - duration
    assert window.duration == duration


@given(stamp=utc_datetimes, duration=positive_durations)
def test_both_stamp_conventions_describe_the_same_interval(
    stamp: datetime, duration: timedelta
) -> None:
    """The same interval reported two ways normalises to the same window."""
    from_start = normalise_stamp(stamp, "interval_start", duration)
    from_end = normalise_stamp(stamp + duration, "interval_end", duration)
    assert from_start.start_utc == from_end.start_utc
    assert from_start.end_utc == from_end.end_utc


@given(
    stamps=st.lists(utc_datetimes, min_size=1, max_size=12),
    duration=positive_durations,
)
def test_normalising_stamps_preserves_order_and_length(
    stamps: list[datetime], duration: timedelta
) -> None:
    windows = normalise_stamps(sorted(stamps), "interval_start", duration)
    assert len(windows) == len(stamps)
    for window in windows:
        assert window.duration == duration
    for previous, current in zip(windows, windows[1:]):
        assert previous.start_utc <= current.start_utc


@given(start=utc_datetimes, duration=positive_durations)
def test_normalise_points_uses_the_reported_stamp(start: datetime, duration: timedelta) -> None:
    point = _point(start, duration)
    as_start = normalise_points([point], "interval_start", duration)[0]
    assert as_start.start_utc == point.start_utc
    assert as_start.end_utc == point.start_utc + duration

    as_end = normalise_points([point], "interval_end", duration)[0]
    assert as_end.end_utc == point.end_utc
    assert as_end.start_utc == point.end_utc - duration


# --------------------------------------------------------------------------
# DST
# --------------------------------------------------------------------------


@given(
    instant=st.datetimes(
        min_value=datetime(2023, 1, 1, tzinfo=UTC),
        max_value=datetime(2027, 1, 1, tzinfo=UTC),
        timezones=st.just(UTC),
    ),
    zone=st.sampled_from(DST_ZONES),
)
@settings(max_examples=300)
def test_utc_local_round_trip_is_within_the_dst_shift(
    instant: datetime, zone: str
) -> None:
    """LAW-5: UTC in, UTC out — local conversion never loses the instant.

    For unambiguous local times the round trip is exact; during an ambiguous
    autumn hour it is at most one hour off (the repeated hour).
    """
    local = to_local(instant, zone)
    back = from_local(local.replace(tzinfo=None), zone)
    assert abs(back - instant) <= timedelta(hours=1)
    if not is_ambiguous_local(local.replace(tzinfo=None), zone):
        assert back == instant


@given(
    naive=st.datetimes(
        min_value=datetime(2023, 1, 1),
        max_value=datetime(2027, 1, 1),
    ),
    zone=st.sampled_from(DST_ZONES),
)
@settings(max_examples=300)
def test_local_times_always_resolve_to_a_real_instant(naive: datetime, zone: str) -> None:
    """A DST day is data, not an error: every local time resolves."""
    instant = from_local(naive, zone)
    assert instant.tzinfo is not None
    assert instant.utcoffset() == timedelta(0)


def test_spring_forward_local_day_is_23_hours() -> None:
    window = local_day_window(date(2024, 3, 10), "America/New_York")
    assert window.duration == timedelta(hours=23)
    assert window.start_utc == datetime(2024, 3, 10, 5, tzinfo=UTC)
    assert window.end_utc == datetime(2024, 3, 11, 4, tzinfo=UTC)


def test_fall_back_local_day_is_25_hours() -> None:
    window = local_day_window(date(2024, 11, 3), "America/New_York")
    assert window.duration == timedelta(hours=25)
    assert window.start_utc == datetime(2024, 11, 3, 4, tzinfo=UTC)
    assert window.end_utc == datetime(2024, 11, 4, 5, tzinfo=UTC)


def test_normal_local_day_is_24_hours() -> None:
    window = local_day_window(date(2024, 6, 15), "America/New_York")
    assert window.duration == timedelta(hours=24)


@given(
    day=st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)),
    zone=st.sampled_from(DST_ZONES),
)
def test_local_day_windows_are_contiguous_and_non_empty(
    day: date, zone: str
) -> None:
    window = local_day_window(day, zone)
    assert window.duration > timedelta(0)
    assert window.duration <= timedelta(hours=25)
    nxt = local_day_window(day + timedelta(days=1), zone)
    assert window.end_utc == nxt.start_utc


def test_nonexistent_local_time_is_detected_and_resolved_forward() -> None:
    # 2024-03-10 02:30 local does not exist in America/New_York.
    naive = datetime(2024, 3, 10, 2, 30)
    assert is_nonexistent_local(naive, "America/New_York")
    instant = from_local(naive, "America/New_York")
    assert instant == datetime(2024, 3, 10, 7, 30, tzinfo=UTC)
    # The resolved instant is the first valid local time after the gap.
    assert to_local(instant, "America/New_York").replace(tzinfo=None) == datetime(
        2024, 3, 10, 3, 30
    )


def test_ambiguous_local_time_is_detected_and_resolves_to_first_occurrence() -> None:
    naive = datetime(2024, 11, 3, 1, 30)
    assert is_ambiguous_local(naive, "America/New_York")
    first = from_local(naive, "America/New_York")
    second = from_local(naive, "America/New_York", fold=1)
    assert first == datetime(2024, 11, 3, 5, 30, tzinfo=UTC)
    assert second == datetime(2024, 11, 3, 6, 30, tzinfo=UTC)


# --------------------------------------------------------------------------
# Leap day
# --------------------------------------------------------------------------


@given(
    day=st.dates(min_value=date(2000, 1, 1), max_value=date(2035, 12, 31)),
)
def test_leap_day_arithmetic(day: date) -> None:
    leap = (day.year % 4 == 0 and (day.year % 100 != 0 or day.year % 400 == 0))
    feb28 = date(day.year, 2, 28)
    nxt = feb28 + timedelta(days=1)
    if leap:
        assert nxt == date(day.year, 2, 29)
        assert (nxt + timedelta(days=1)) == date(day.year, 3, 1)
    else:
        assert nxt == date(day.year, 3, 1)


def test_leap_day_window_is_24_hours() -> None:
    window = local_day_window(date(2024, 2, 29), "America/New_York")
    assert window.duration == timedelta(hours=24)
    assert window.start_utc == datetime(2024, 2, 29, 5, tzinfo=UTC)
    assert window.end_utc == datetime(2024, 3, 1, 5, tzinfo=UTC)


def test_interval_spanning_leap_day_has_the_right_duration() -> None:
    start = datetime(2024, 2, 28, 5, tzinfo=UTC)
    point = _point(start, timedelta(days=2))
    assert point.duration == timedelta(days=2)
    assert point.end_utc == datetime(2024, 3, 1, 5, tzinfo=UTC)


# --------------------------------------------------------------------------
# Billing periods
# --------------------------------------------------------------------------


@given(
    start=utc_datetimes,
    durations=st.lists(positive_durations, min_size=1, max_size=8),
)
def test_billing_periods_are_points_with_long_irregular_intervals(
    start: datetime, durations: list[timedelta]
) -> None:
    points: list[EnergyPoint] = []
    cursor = start
    for duration in durations:
        points.append(_point(cursor, duration, value=float(len(points))))
        cursor = cursor + duration
    windows = billing_period_windows(points)
    assert len(windows) == len(points)
    for window, point in zip(windows, points):
        assert window.start_utc == point.start_utc
        assert window.end_utc == point.end_utc
    # Billing periods are contiguous, irregular, and never a "month" type.
    assert len({window.duration for window in windows}) >= 1
    for previous, current in zip(windows, windows[1:]):
        assert previous.end_utc == current.start_utc


@given(
    start=utc_datetimes,
    durations=st.lists(positive_durations, min_size=2, max_size=8),
    cut_index=st.integers(min_value=0, max_value=7),
)
def test_slicing_a_billing_series_returns_a_contiguous_sub_series(
    start: datetime, durations: list[timedelta], cut_index: int
) -> None:
    assume(cut_index < len(durations))
    points: list[EnergyPoint] = []
    cursor = start
    for duration in durations:
        points.append(_point(cursor, duration))
        cursor = cursor + duration
    series = UtilitySeries(
        fuel="electricity",
        kind="import",
        granularity="billing_period",
        points=points,
        stamp_semantics="interval_start",
        provenance=_provenance(),
    )
    window = points[cut_index]
    from home_energy_twin.types import TimeWindow

    slice_window = TimeWindow(start_utc=window.start_utc, end_utc=window.end_utc)
    sliced = slice_series(series, slice_window)
    assert len(sliced.points) == 1
    assert sliced.points[0].start_utc == window.start_utc
    assert sliced.points[0].end_utc == window.end_utc
    assert points_cover_window(sliced.points, slice_window)


@given(start=utc_datetimes, duration=positive_durations)
def test_coverage_detection_is_exact(start: datetime, duration: timedelta) -> None:
    from home_energy_twin.types import TimeWindow

    points = [_point(start, duration), _point(start + duration, duration)]
    covered = TimeWindow(start_utc=start, end_utc=start + 2 * duration)
    assert points_cover_window(points, covered)
    # A window reaching past the data is not covered.
    beyond = TimeWindow(start_utc=start, end_utc=start + 3 * duration)
    assert not points_cover_window(points, beyond)
    # A hole in the middle is not covered either.
    holed = [
        _point(start, duration),
        _point(start + 2 * duration, duration),
    ]
    assert not points_cover_window(holed, covered)


@given(
    start=utc_datetimes,
    duration=positive_durations,
    offsets=st.lists(st.integers(min_value=0, max_value=1000), min_size=0, max_size=6),
)
def test_split_interval_covers_the_window_exactly(
    start: datetime, duration: timedelta, offsets: list[int]
) -> None:
    from home_energy_twin.types import TimeWindow

    window = TimeWindow(start_utc=start, end_utc=start + duration)
    boundaries = [start + timedelta(seconds=offset) for offset in offsets]
    pieces = split_interval(window, boundaries)
    assert pieces[0].start_utc == window.start_utc
    assert pieces[-1].end_utc == window.end_utc
    for previous, current in zip(pieces, pieces[1:]):
        assert previous.end_utc == current.start_utc
    total = sum((piece.duration for piece in pieces), timedelta(0))
    assert total == window.duration


@pytest.mark.parametrize("zone", DST_ZONES)
def test_stored_timestamps_stay_utc_across_a_dst_day(zone: str) -> None:
    """LAW-5: conversions happen at the edge; stored stamps stay UTC."""
    window = local_day_window(date(2024, 3, 10), zone)
    point = _point(window.start_utc, window.duration)
    assert point.start_utc.utcoffset() == timedelta(0)
    assert point.end_utc.utcoffset() == timedelta(0)
