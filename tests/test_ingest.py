"""Tests for monthly bill ingest (issue #9).

Covered:

* typed rows and CSV rows → billing-period ``UtilitySeries`` with correct
  half-open ``[start, end)`` bounds, provenance, preserved estimated flags
  and a recorded stamp-semantics conversion,
* a Green Button DMD-shaped (ESPI Atom/XML) document → the same shape of
  series (synthetic document only, LAW-11),
* the SPEC §6 / RD-6 edges that apply to monthly ingest: billing-period
  slicing boundaries, leap day, and a DST-spanning period.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from home_energy_twin.errors import InsufficientData
from home_energy_twin.ingest import (
    MonthlyBillRow,
    ingest_green_button_dmd,
    ingest_monthly_csv,
    monthly_rows_to_series,
)
from home_energy_twin.intervals import slice_series
from home_energy_twin.types import TimeWindow

UTC = timezone.utc
NY = "America/New_York"

CSV_TEXT = "\n".join(
    [
        "period_start,period_end,value,unit,estimated",
        "2024-01-01,2024-02-01,812.4,kWh,false",
        "2024-02-01,2024-03-01,795.1,kWh,false",
        "2024-03-01,2024-04-01,640.8,kWh,true",
    ]
)

DMD_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:espi="http://www.greenbuttonalliance.org/schema/1.0/">
  <entry>
    <id>urn:uuid:00000000-0000-0000-0000-000000000001</id>
    <title>Electric Usage</title>
    <content type="application/xml">
      <espi:ElectricUsage>
        <espi:Usage>
          <espi:usagePoint>
            <espi:UsagePoint>
              <espi:ConsumingMeter>
                <espi:meterNumber>0000000001</espi:meterNumber>
              </espi:ConsumingMeter>
            </espi:UsagePoint>
          </espi:usagePoint>
        </espi:Usage>
        <espi:Reading>
          <espi:interval>
            <espi:duration>P31D</espi:duration>
            <espi:startTime>2024-01-01T05:00:00Z</espi:startTime>
            <espi:value>812.4</espi:value>
          </espi:interval>
        </espi:Reading>
        <espi:Reading>
          <espi:interval>
            <espi:duration>P29D</espi:duration>
            <espi:startTime>2024-02-01T05:00:00Z</espi:startTime>
            <espi:value>795.1</espi:value>
          </espi:interval>
          <espi:Estimated>true</espi:Estimated>
        </espi:Reading>
      </espi:ElectricUsage>
    </content>
  </entry>
</feed>
"""


# --------------------------------------------------------------------------
# Typed / CSV rows
# --------------------------------------------------------------------------


def test_csv_rows_ingest_to_a_billing_period_series() -> None:
    result = ingest_monthly_csv(
        CSV_TEXT,
        timezone=NY,
        fuel="electricity",
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    series = result.series

    assert series.fuel == "electricity"
    assert series.kind == "import"
    assert series.granularity == "billing_period"
    assert len(series.points) == 3
    assert series.stamp_semantics == "interval_start"
    assert series.provenance.source == "manual-monthly-bills"
    assert series.provenance.retrieved_at_utc == datetime(2024, 5, 1, tzinfo=UTC)
    assert series.provenance.terms_tag

    first = series.points[0]
    assert first.start_utc == datetime(2024, 1, 1, 5, 0, tzinfo=UTC)
    assert first.end_utc == datetime(2024, 2, 1, 5, 0, tzinfo=UTC)
    assert first.value == 812.4
    assert first.estimated is False

    last = series.points[-1]
    assert last.start_utc == datetime(2024, 3, 1, 5, 0, tzinfo=UTC)
    assert last.end_utc == datetime(2024, 4, 1, 4, 0, tzinfo=UTC)
    assert last.estimated is True

    # The conversion applied at ingest is recorded.
    assert result.conversion.from_semantics == "interval_start"
    assert result.conversion.to_semantics == "interval_start"
    assert result.conversion.note


def test_typed_rows_ingest_to_a_billing_period_series() -> None:
    rows = [
        MonthlyBillRow(
            period_start=datetime(2024, 1, 1).date(),
            period_end=datetime(2024, 2, 1).date(),
            value=100.0,
        ),
        MonthlyBillRow(
            period_start=datetime(2024, 2, 1).date(),
            period_end=datetime(2024, 3, 1).date(),
            value=110.0,
            fuel="electricity",
            estimated=True,
        ),
    ]
    result = monthly_rows_to_series(
        rows,
        timezone=NY,
        fuel="electricity",
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    assert len(result.series.points) == 2
    assert result.series.points[1].estimated is True
    assert result.series.points[1].unit == "kWh"


def test_period_end_anchored_rows_record_the_stamp_conversion() -> None:
    rows = [
        MonthlyBillRow(
            period_start=datetime(2024, 1, 1).date(),
            period_end=datetime(2024, 2, 1).date(),
            value=812.4,
        ),
        MonthlyBillRow(period_end=datetime(2024, 3, 1).date(), value=795.1),
    ]
    result = monthly_rows_to_series(
        rows,
        timezone=NY,
        fuel="electricity",
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    series = result.series
    # A row that states only its period end starts where the previous bill ended.
    assert series.stamp_semantics == "interval_end"
    assert result.conversion.from_semantics == "interval_end"
    assert result.conversion.to_semantics == "interval_start"
    assert series.points[0].start_utc == datetime(2024, 1, 1, 5, 0, tzinfo=UTC)
    assert series.points[0].end_utc == datetime(2024, 2, 1, 5, 0, tzinfo=UTC)
    assert series.points[1].start_utc == datetime(2024, 2, 1, 5, 0, tzinfo=UTC)
    assert series.points[1].end_utc == datetime(2024, 3, 1, 5, 0, tzinfo=UTC)


def test_first_row_without_a_start_is_refused() -> None:
    rows = [MonthlyBillRow(period_end=datetime(2024, 2, 1).date(), value=812.4)]
    with pytest.raises(InsufficientData):
        monthly_rows_to_series(rows, timezone=NY, fuel="electricity")


def test_mixed_anchoring_uses_the_previous_period_end() -> None:
    rows = [
        MonthlyBillRow(
            period_start=datetime(2024, 1, 1).date(),
            period_end=datetime(2024, 2, 1).date(),
            value=812.4,
        ),
        MonthlyBillRow(period_end=datetime(2024, 3, 1).date(), value=795.1),
        MonthlyBillRow(period_end=datetime(2024, 4, 1).date(), value=640.8),
    ]
    result = monthly_rows_to_series(
        rows,
        timezone=NY,
        fuel="electricity",
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    points = result.series.points
    assert len(points) == 3
    assert points[2].start_utc == datetime(2024, 3, 1, 5, 0, tzinfo=UTC)
    assert points[2].end_utc == datetime(2024, 4, 1, 4, 0, tzinfo=UTC)


def test_unknown_fuel_is_refused() -> None:
    rows = [
        MonthlyBillRow(
            period_start=datetime(2024, 1, 1).date(),
            period_end=datetime(2024, 2, 1).date(),
            value=812.4,
        )
    ]
    with pytest.raises(InsufficientData):
        monthly_rows_to_series(rows, timezone=NY)


# --------------------------------------------------------------------------
# RD-6 edges: slicing boundaries, leap day, DST
# --------------------------------------------------------------------------


def test_billing_period_slicing_boundaries_are_half_open() -> None:
    result = ingest_monthly_csv(
        CSV_TEXT,
        timezone=NY,
        fuel="electricity",
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    series = result.series

    window = TimeWindow(
        start_utc=datetime(2024, 2, 1, 5, 0, tzinfo=UTC),
        end_utc=datetime(2024, 3, 1, 5, 0, tzinfo=UTC),
    )
    sliced = slice_series(series, window)
    assert len(sliced.points) == 1
    assert sliced.points[0].start_utc == window.start_utc
    assert sliced.points[0].end_utc == window.end_utc

    # A window that ends exactly on a period start excludes that period.
    exact = TimeWindow(
        start_utc=datetime(2024, 1, 1, 5, 0, tzinfo=UTC),
        end_utc=datetime(2024, 2, 1, 5, 0, tzinfo=UTC),
    )
    assert len(slice_series(series, exact).points) == 1


def test_leap_day_billing_period() -> None:
    rows = [
        MonthlyBillRow(
            period_start=datetime(2024, 2, 1).date(),
            period_end=datetime(2024, 3, 1).date(),
            value=795.1,
        )
    ]
    result = monthly_rows_to_series(
        rows,
        timezone=NY,
        fuel="electricity",
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    point = result.series.points[0]
    assert point.start_utc == datetime(2024, 2, 1, 5, 0, tzinfo=UTC)
    assert point.end_utc == datetime(2024, 3, 1, 5, 0, tzinfo=UTC)
    assert point.duration == timedelta(days=29)


def test_dst_spanning_billing_period() -> None:
    rows = [
        MonthlyBillRow(
            period_start=datetime(2024, 3, 1).date(),
            period_end=datetime(2024, 4, 1).date(),
            value=640.8,
        )
    ]
    result = monthly_rows_to_series(
        rows,
        timezone=NY,
        fuel="electricity",
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    point = result.series.points[0]
    assert point.start_utc == datetime(2024, 3, 1, 5, 0, tzinfo=UTC)
    assert point.end_utc == datetime(2024, 4, 1, 4, 0, tzinfo=UTC)
    # The spring-forward day makes the local month one hour shorter.
    assert point.duration == timedelta(days=31) - timedelta(hours=1)


# --------------------------------------------------------------------------
# Green Button DMD-shaped input
# --------------------------------------------------------------------------


def test_dmd_document_ingests_to_a_billing_period_series() -> None:
    result = ingest_green_button_dmd(
        DMD_XML,
        timezone=NY,
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    series = result.series

    assert series.fuel == "electricity"
    assert series.granularity == "billing_period"
    assert len(series.points) == 2
    assert series.meter_id == "0000000001"
    assert series.provenance.source == "green-button-dmd"

    first = series.points[0]
    assert first.start_utc == datetime(2024, 1, 1, 5, 0, tzinfo=UTC)
    assert first.end_utc == datetime(2024, 2, 1, 5, 0, tzinfo=UTC)
    assert first.value == 812.4
    assert first.estimated is False

    second = series.points[1]
    assert second.start_utc == datetime(2024, 2, 1, 5, 0, tzinfo=UTC)
    assert second.end_utc == datetime(2024, 3, 1, 5, 0, tzinfo=UTC)
    assert second.estimated is True

    assert result.conversion.from_semantics == "interval_start"
    assert result.conversion.to_semantics == "interval_start"


def test_dmd_interval_end_stamps_are_normalised_and_recorded() -> None:
    xml = DMD_XML.replace(
        "<espi:startTime>2024-01-01T05:00:00Z</espi:startTime>",
        "<espi:endTime>2024-02-01T05:00:00Z</espi:endTime>",
    ).replace(
        "<espi:startTime>2024-02-01T05:00:00Z</espi:startTime>",
        "<espi:endTime>2024-03-01T05:00:00Z</espi:endTime>",
    )
    result = ingest_green_button_dmd(
        xml,
        timezone=NY,
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    series = result.series
    assert series.stamp_semantics == "interval_end"
    assert result.conversion.from_semantics == "interval_end"
    assert result.conversion.to_semantics == "interval_start"
    assert series.points[0].start_utc == datetime(2024, 1, 1, 5, 0, tzinfo=UTC)
    assert series.points[0].end_utc == datetime(2024, 2, 1, 5, 0, tzinfo=UTC)


def test_dmd_interval_readings_are_skipped_and_reported() -> None:
    xml = DMD_XML.replace(
        "<espi:duration>P29D</espi:duration>",
        "<espi:duration>PT900S</espi:duration>",
    )
    result = ingest_green_button_dmd(
        xml,
        timezone=NY,
        retrieved_at_utc=datetime(2024, 5, 1, tzinfo=UTC),
    )
    assert len(result.series.points) == 1
    assert any("interval" in note for note in result.notes)


def test_dmd_document_without_billing_readings_is_refused() -> None:
    xml = DMD_XML.replace("<espi:duration>P29D</espi:duration>", "<espi:duration>PT900S</espi:duration>")
    xml = xml.replace("<espi:duration>P31D</espi:duration>", "<espi:duration>PT1800S</espi:duration>")
    with pytest.raises(InsufficientData):
        ingest_green_button_dmd(xml, timezone=NY)


def test_malformed_dmd_document_is_refused() -> None:
    with pytest.raises(InsufficientData):
        ingest_green_button_dmd("<feed><entry>", timezone=NY)
