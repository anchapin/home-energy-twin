"""Tests for the Spike C Green Button DMD parser (issue #5).

Everything here is synthetic (LAW-11): the fixtures under ``tests/fixtures``
are hand-written ESPI/Atom documents, not a real utility export.  The owner's
real DMD file is owner-local and is never committed (LAW-11); see
``spikes/green_button/REPORT.md`` for the quirks that still need it.

The parser is stdlib-only and offline, so these tests run in the core suite
with no network access and no extra dependency (RD-7).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from home_energy_twin.errors import (
    InsufficientData,
    ProvenanceMissing,
    UnidentifiableConfiguration,
)
from home_energy_twin.intervals import slice_series
from home_energy_twin.types import TimeWindow, UtilitySeries
from spikes.green_button import parse_dmd_bytes, parse_dmd_file, parse_dmd_text

FIXTURES = Path(__file__).parent / "fixtures"
DMD_PATH = FIXTURES / "green_button_dmd_synthetic.xml"
OFFSET_DMD_PATH = FIXTURES / "green_button_dmd_offset_synthetic.xml"

RETRIEVED_AT = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)

ELECTRICITY_METER = "urn:uuid:aaaaaaaa-0000-4000-8000-000000000001"
GAS_METER = "urn:uuid:aaaaaaaa-0000-4000-8000-000000000002"
NET_METER = "urn:uuid:aaaaaaaa-0000-4000-8000-000000000003"


def _dmd() -> "object":
    return parse_dmd_file(
        DMD_PATH,
        retrieved_at_utc=RETRIEVED_AT,
        terms_tag="synthetic-terms",
    )


@pytest.fixture(scope="module")
def parsed():  # type: ignore[no-untyped-def]
    return _dmd()


def _series(parsed, meter_id: str, granularity: str) -> UtilitySeries:
    matches = [
        s
        for s in parsed.series
        if s.meter_id == meter_id and s.granularity == granularity
    ]
    assert len(matches) == 1, (meter_id, granularity, len(matches))
    return matches[0]


# --------------------------------------------------------------------------
# Shape of the parse
# --------------------------------------------------------------------------


def test_parse_yields_utility_series(parsed) -> None:  # type: ignore[no-untyped-def]
    assert len(parsed.series) == 4
    for series in parsed.series:
        # pydantic validation passes and the model round-trips
        assert UtilitySeries.model_validate(series.model_dump()) == series


def test_points_are_ordered_by_start(parsed) -> None:  # type: ignore[no-untyped-def]
    for series in parsed.series:
        starts = [point.start_utc for point in series.points]
        assert starts == sorted(starts)
        assert series.points, "a series must carry points"


def test_intervals_are_half_open(parsed) -> None:  # type: ignore[no-untyped-def]
    hourly = _series(parsed, ELECTRICITY_METER, "hourly")
    first = hourly.points[0]
    assert first.start_utc == datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc)
    assert first.end_utc == datetime(2026, 3, 1, 6, 0, tzinfo=timezone.utc)
    # [start, end): the end instant belongs to the next interval
    window = TimeWindow(
        start_utc=first.start_utc, end_utc=first.end_utc
    )
    assert window.contains(first.start_utc)
    assert not window.contains(first.end_utc)


def test_fuel_and_kind_mapping(parsed) -> None:  # type: ignore[no-untyped-def]
    hourly = _series(parsed, ELECTRICITY_METER, "hourly")
    assert (hourly.fuel, hourly.kind) == ("electricity", "import")

    gas = _series(parsed, GAS_METER, "daily")
    assert (gas.fuel, gas.kind) == ("natural_gas", "delivered")

    net = _series(parsed, NET_METER, "hourly")
    assert (net.fuel, net.kind) == ("electricity", "net")


def test_granularity_mapping(parsed) -> None:  # type: ignore[no-untyped-def]
    assert _series(parsed, ELECTRICITY_METER, "hourly").granularity == "hourly"
    assert _series(parsed, GAS_METER, "daily").granularity == "daily"
    assert (
        _series(parsed, ELECTRICITY_METER, "billing_period").granularity
        == "billing_period"
    )


# --------------------------------------------------------------------------
# Timestamp basis and stamp semantics
# --------------------------------------------------------------------------


def test_timestamp_basis_is_reported(parsed) -> None:  # type: ignore[no-untyped-def]
    assert parsed.timestamp_basis == "utc_z"


def test_all_timestamps_are_utc(parsed) -> None:  # type: ignore[no-untyped-def]
    for series in parsed.series:
        for point in series.points:
            assert point.start_utc.utcoffset() == timedelta(0)
            assert point.end_utc.utcoffset() == timedelta(0)


def test_stamp_semantics_are_explicit(parsed) -> None:  # type: ignore[no-untyped-def]
    for series in parsed.series:
        assert series.stamp_semantics in ("interval_start", "interval_end")
    hourly = _series(parsed, ELECTRICITY_METER, "hourly")
    assert hourly.stamp_semantics == "interval_start"


def test_offset_fixture_reports_its_basis_and_converts_to_utc() -> None:
    parsed = parse_dmd_file(
        OFFSET_DMD_PATH, retrieved_at_utc=RETRIEVED_AT, terms_tag="synthetic-terms"
    )
    assert parsed.timestamp_basis == "fixed_local_offset"
    hourly = [s for s in parsed.series if s.granularity == "hourly"][0]
    assert hourly.points[0].start_utc == datetime(
        2026, 3, 1, 5, 0, tzinfo=timezone.utc
    )
    assert hourly.points[0].end_utc == datetime(2026, 3, 1, 6, 0, tzinfo=timezone.utc)


def test_interval_end_stamps_are_honoured() -> None:
    """A source that stamps interval *ends* keeps that semantics."""
    payload = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:g="http://greenbutton.com/schema/1.0">
      <entry><content>
        <g:UsagePoint id="urn:uuid:end-stamp-meter">
          <g:MeterReading>
            <g:readingType>electricity</g:readingType>
            <g:unitOfMeasure>kWh</g:unitOfMeasure>
            <g:source>Synthetic Meter Co</g:source>
            <g:IntervalBlock>
              <g:IntervalReading endTime="2026-03-01T06:00:00Z" duration="PT1H">
                <g:value>1.000</g:value>
              </g:IntervalReading>
              <g:IntervalReading endTime="2026-03-01T07:00:00Z" duration="PT1H">
                <g:value>2.000</g:value>
              </g:IntervalReading>
            </g:IntervalBlock>
          </g:MeterReading>
        </g:UsagePoint>
      </content></entry>
    </feed>"""
    parsed = parse_dmd_text(
        payload, retrieved_at_utc=RETRIEVED_AT, terms_tag="synthetic-terms"
    )
    series = parsed.series[0]
    assert series.stamp_semantics == "interval_end"
    assert series.points[0].start_utc == datetime(
        2026, 3, 1, 5, 0, tzinfo=timezone.utc
    )
    assert series.points[0].end_utc == datetime(
        2026, 3, 1, 6, 0, tzinfo=timezone.utc
    )


# --------------------------------------------------------------------------
# Estimated reads
# --------------------------------------------------------------------------


def test_estimated_read_is_flagged(parsed) -> None:  # type: ignore[no-untyped-def]
    hourly = _series(parsed, ELECTRICITY_METER, "hourly")
    estimated = [point for point in hourly.points if point.estimated]
    assert len(estimated) == 1
    assert estimated[0].value == pytest.approx(0.417)
    assert estimated[0].start_utc == datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    assert all(not point.estimated for point in hourly.points if point is not estimated[0])


def test_estimated_count_is_reported(parsed) -> None:  # type: ignore[no-untyped-def]
    assert parsed.estimated_read_count == 1
    read = [r for r in parsed.reads if r.meter_id == ELECTRICITY_METER][0]
    assert read.estimated_count == 1


def test_data_quality_actual_is_not_estimated(parsed) -> None:  # type: ignore[no-untyped-def]
    hourly = _series(parsed, ELECTRICITY_METER, "hourly")
    actual = [point for point in hourly.points if not point.estimated]
    assert len(actual) == 8


# --------------------------------------------------------------------------
# Gaps
# --------------------------------------------------------------------------


def test_gap_is_detected(parsed) -> None:  # type: ignore[no-untyped-def]
    assert len(parsed.gaps) == 1
    gap = parsed.gaps[0]
    assert gap.kind == "gap"
    assert gap.meter_id == ELECTRICITY_METER
    assert gap.after_end_utc == datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)
    assert gap.before_start_utc == datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    assert gap.duration == timedelta(hours=1)


def test_no_silent_fill_of_a_gap(parsed) -> None:  # type: ignore[no-untyped-def]
    hourly = _series(parsed, ELECTRICITY_METER, "hourly")
    assert len(hourly.points) == 9  # 10 slots minus the missing 11:00 hour
    stamps = [point.start_utc for point in hourly.points]
    assert datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc) not in stamps


def test_no_overlaps_in_the_fixture(parsed) -> None:  # type: ignore[no-untyped-def]
    assert parsed.overlaps == ()


# --------------------------------------------------------------------------
# Billing-period shapes and slicing
# --------------------------------------------------------------------------


def test_billing_period_points_have_irregular_bounds(parsed) -> None:  # type: ignore[no-untyped-def]
    billing = _series(parsed, ELECTRICITY_METER, "billing_period")
    assert len(billing.points) == 2

    february = billing.points[0]
    assert february.start_utc == datetime(2026, 2, 1, 5, 0, tzinfo=timezone.utc)
    assert february.end_utc == datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc)
    assert february.duration == timedelta(days=28)  # short month

    march = billing.points[1]
    assert march.start_utc == datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc)
    # March 2026 in America/New_York is 743 h: the DST spring-forward hour
    # makes a calendar month 23 h short in absolute time.
    assert march.end_utc == datetime(2026, 4, 1, 4, 0, tzinfo=timezone.utc)
    assert march.duration == timedelta(hours=743)
    assert march.duration != timedelta(days=31)


def test_billing_period_slices_the_interval_series(parsed) -> None:  # type: ignore[no-untyped-def]
    hourly = _series(parsed, ELECTRICITY_METER, "hourly")
    billing = _series(parsed, ELECTRICITY_METER, "billing_period")
    march_window = TimeWindow(
        start_utc=billing.points[1].start_utc, end_utc=billing.points[1].end_utc
    )
    sliced = slice_series(hourly, march_window)
    assert len(sliced.points) == 9

    february_window = TimeWindow(
        start_utc=billing.points[0].start_utc, end_utc=billing.points[0].end_utc
    )
    february_slice = slice_series(hourly, february_window)
    assert february_slice.points == []  # no rescaling into an empty window


def test_billing_series_slices_to_one_point(parsed) -> None:  # type: ignore[no-untyped-def]
    billing = _series(parsed, ELECTRICITY_METER, "billing_period")
    window = TimeWindow(
        start_utc=datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 4, 1, 4, 0, tzinfo=timezone.utc),
    )
    sliced = slice_series(billing, window)
    assert len(sliced.points) == 1
    assert sliced.points[0].value == pytest.approx(604.900)


def test_billing_period_is_a_point_not_a_monthly_type(parsed) -> None:  # type: ignore[no-untyped-def]
    """RD-6: a billing period is an EnergyPoint with a long [start, end)."""
    billing = _series(parsed, ELECTRICITY_METER, "billing_period")
    point = billing.points[0]
    assert point.end_utc > point.start_utc
    assert point.unit == "kWh"


# --------------------------------------------------------------------------
# Provenance (LAW-12)
# --------------------------------------------------------------------------


def test_provenance_is_recorded(parsed) -> None:  # type: ignore[no-untyped-def]
    for series in parsed.series:
        assert series.provenance.source.startswith("green_button_dmd")
        assert series.provenance.retrieved_at_utc == RETRIEVED_AT
        assert series.provenance.terms_tag == "synthetic-terms"


def test_missing_terms_tag_is_warned_about() -> None:
    parsed = parse_dmd_file(DMD_PATH, retrieved_at_utc=RETRIEVED_AT)
    assert any("terms" in warning for warning in parsed.warnings)


# --------------------------------------------------------------------------
# Typed errors (SPEC §5)
# --------------------------------------------------------------------------


def test_naive_timestamp_is_refused() -> None:
    payload = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:g="http://greenbutton.com/schema/1.0">
      <entry><content>
        <g:UsagePoint id="urn:uuid:naive-meter">
          <g:MeterReading>
            <g:readingType>electricity</g:readingType>
            <g:unitOfMeasure>kWh</g:unitOfMeasure>
            <g:source>Synthetic Meter Co</g:source>
            <g:IntervalBlock>
              <g:IntervalReading startTime="2026-03-01T05:00:00" duration="PT1H">
                <g:value>1.000</g:value>
              </g:IntervalReading>
            </g:IntervalBlock>
          </g:MeterReading>
        </g:UsagePoint>
      </content></entry>
    </feed>"""
    with pytest.raises(InsufficientData) as excinfo:
        parse_dmd_text(payload, retrieved_at_utc=RETRIEVED_AT)
    assert excinfo.value.reason == "naive_timestamp"


def test_interval_without_bounds_is_refused() -> None:
    payload = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:g="http://greenbutton.com/schema/1.0">
      <entry><content>
        <g:UsagePoint id="urn:uuid:unbounded-meter">
          <g:MeterReading>
            <g:readingType>electricity</g:readingType>
            <g:unitOfMeasure>kWh</g:unitOfMeasure>
            <g:source>Synthetic Meter Co</g:source>
            <g:IntervalBlock>
              <g:IntervalReading startTime="2026-03-01T05:00:00Z">
                <g:value>1.000</g:value>
              </g:IntervalReading>
            </g:IntervalBlock>
          </g:MeterReading>
        </g:UsagePoint>
      </content></entry>
    </feed>"""
    with pytest.raises(InsufficientData) as excinfo:
        parse_dmd_text(payload, retrieved_at_utc=RETRIEVED_AT)
    assert excinfo.value.reason == "unbounded_interval"


def test_unknown_reading_type_is_refused() -> None:
    payload = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:g="http://greenbutton.com/schema/1.0">
      <entry><content>
        <g:UsagePoint id="urn:uuid:water-meter">
          <g:MeterReading>
            <g:readingType>water</g:readingType>
            <g:unitOfMeasure>gal</g:unitOfMeasure>
            <g:source>Synthetic Meter Co</g:source>
            <g:IntervalBlock>
              <g:IntervalReading startTime="2026-03-01T05:00:00Z" duration="PT1H">
                <g:value>1.000</g:value>
              </g:IntervalReading>
            </g:IntervalBlock>
          </g:MeterReading>
        </g:UsagePoint>
      </content></entry>
    </feed>"""
    with pytest.raises(UnidentifiableConfiguration) as excinfo:
        parse_dmd_text(payload, retrieved_at_utc=RETRIEVED_AT)
    assert "unsupported_reading_type" in (excinfo.value.reason or "")


def test_malformed_xml_is_refused() -> None:
    with pytest.raises(InsufficientData) as excinfo:
        parse_dmd_text("<feed><entry>", retrieved_at_utc=RETRIEVED_AT)
    assert excinfo.value.reason == "malformed_xml"


def test_dtd_is_refused() -> None:
    payload = (
        '<?xml version="1.0"?>'
        "<!DOCTYPE feed [<!ENTITY x \"boom\">]>"
        "<feed xmlns:g=\"http://greenbutton.com/schema/1.0\"><entry/></feed>"
    )
    with pytest.raises(InsufficientData) as excinfo:
        parse_dmd_text(payload, retrieved_at_utc=RETRIEVED_AT)
    assert excinfo.value.reason == "dtd_not_allowed"


def test_missing_source_is_refused() -> None:
    payload = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:g="http://greenbutton.com/schema/1.0">
      <entry><content>
        <g:UsagePoint id="urn:uuid:sourceless-meter">
          <g:MeterReading>
            <g:readingType>electricity</g:readingType>
            <g:unitOfMeasure>kWh</g:unitOfMeasure>
            <g:IntervalBlock>
              <g:IntervalReading startTime="2026-03-01T05:00:00Z" duration="PT1H">
                <g:value>1.000</g:value>
              </g:IntervalReading>
            </g:IntervalBlock>
          </g:MeterReading>
        </g:UsagePoint>
      </content></entry>
    </feed>"""
    with pytest.raises(ProvenanceMissing):
        parse_dmd_text(payload, retrieved_at_utc=RETRIEVED_AT)


def test_empty_export_is_refused() -> None:
    payload = (
        '<?xml version="1.0"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    )
    with pytest.raises(InsufficientData) as excinfo:
        parse_dmd_text(payload, retrieved_at_utc=RETRIEVED_AT)
    assert excinfo.value.reason == "no_readings"


def test_missing_unit_is_warned_about_not_guessed_silently() -> None:
    payload = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:g="http://greenbutton.com/schema/1.0">
      <entry><content>
        <g:UsagePoint id="urn:uuid:unitless-meter">
          <g:MeterReading>
            <g:readingType>electricity</g:readingType>
            <g:source>Synthetic Meter Co</g:source>
            <g:IntervalBlock>
              <g:IntervalReading startTime="2026-03-01T05:00:00Z" duration="PT1H">
                <g:value>1.000</g:value>
              </g:IntervalReading>
            </g:IntervalBlock>
          </g:MeterReading>
        </g:UsagePoint>
      </content></entry>
    </feed>"""
    parsed = parse_dmd_text(
        payload, retrieved_at_utc=RETRIEVED_AT, terms_tag="synthetic-terms"
    )
    assert parsed.series[0].points[0].unit == "kWh"
    assert any("unitOfMeasure" in warning for warning in parsed.warnings)


# --------------------------------------------------------------------------
# Parse counts quoted by the report
# --------------------------------------------------------------------------


def test_parse_counts_match_the_report(parsed) -> None:  # type: ignore[no-untyped-def]
    assert parsed.point_count == 16
    assert len(parsed.series) == 4
    assert len(parsed.reads) == 4
    assert parsed.estimated_read_count == 1
    assert len(parsed.gaps) == 1
    assert len(parsed.overlaps) == 0


def test_parse_dmd_bytes_matches_parse_dmd_file(parsed) -> None:  # type: ignore[no-untyped-def]
    from_bytes = parse_dmd_bytes(
        DMD_PATH.read_bytes(), retrieved_at_utc=RETRIEVED_AT, terms_tag="synthetic-terms"
    )
    assert from_bytes.point_count == parsed.point_count
    assert from_bytes.timestamp_basis == parsed.timestamp_basis


# --------------------------------------------------------------------------
# Owner-local CLI (the step that has to run on the owner's own file)
# --------------------------------------------------------------------------


def test_cli_summarises_the_fixture() -> None:
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "spikes.green_button", str(DMD_PATH)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "timestamp basis" in result.stdout
    assert "utc_z" in result.stdout
    assert "gaps               : 1" in result.stdout


def test_cli_reports_a_missing_file() -> None:
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "spikes.green_button", "no-such-export.xml"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "no such file" in result.stderr
