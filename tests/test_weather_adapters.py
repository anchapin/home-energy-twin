"""Tests for the weather adapters (issue #11).

The adapters are exercised against **recorded synthetic API responses** under
``tests/fixtures/`` with the HTTP layer injected, so the suite never touches
the network (AGENTS.md: "Tests must pass without network access").

What is covered:

* the Open-Meteo adapter uses the **hosted** API only and records provenance
  with the CC BY 4.0 attribution string (RD-11);
* the NWS adapter picks the *nearest* observation station and records the
  distance to it (representativeness is recorded, not hidden);
* AMY slicing: calibration weather is sliced to the utility data's exact
  bounds, and forecast/TMY weather is refused for calibration (LAW-10);
* the twin path combines observed + forecast weather, keeping the kinds apart.

All fixtures are synthetic (LAW-11).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from home_energy_twin.protocols import WeatherProvider
from home_energy_twin.types import LatLon, TimeWindow, WeatherSeries
from home_energy_twin.weather import (
    ATTRIBUTION,
    OPEN_METEO_TERMS,
    NWSWeatherProvider,
    OpenMeteoWeatherProvider,
    TwinWeather,
    WeatherHttpResponse,
    WeatherResponseError,
    amy_for_calibration,
    haversine_km,
    humidity_ratio_from_rh,
    merge_observed_and_forecast,
    slice_to_window,
)

UTC = timezone.utc
FIXTURES = Path(__file__).resolve().parent / "fixtures"

BOSTON = LatLon(latitude=42.3600, longitude=-71.0600)
PORTLAND = LatLon(latitude=43.0000, longitude=-70.0000)


class RecordingHttp:
    """A fake HTTP layer that serves recorded fixtures and records calls."""

    def __init__(self, routes: Mapping[str, str]) -> None:
        self._routes = dict(routes)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, params: Mapping[str, str]) -> WeatherHttpResponse:
        self.calls.append((url, dict(params)))
        for needle, fixture in self._routes.items():
            if needle in url:
                body = (FIXTURES / fixture).read_text(encoding="utf-8")
                return WeatherHttpResponse(status=200, body=body, url=url)
        return WeatherHttpResponse(status=404, body="{}", url=url)

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


def _open_meteo_http() -> RecordingHttp:
    return RecordingHttp(
        {
            "archive-api.open-meteo.com": "openmeteo_archive_response.json",
            "api.open-meteo.com": "openmeteo_forecast_response.json",
        }
    )


def _nws_http() -> RecordingHttp:
    return RecordingHttp(
        {
            "/stations/KBOS/observations": "nws_observations_response.json",
            "/stations/KBDR/observations": "nws_observations_response.json",
            "/stations/KBOS": "nws_station_kbos_response.json",
            "/stations/KBDR": "nws_station_kbdr_response.json",
            "/gridpoints/BOX/51,62/forecast": "nws_forecast_response.json",
            "/points/": "nws_points_response.json",
        }
    )


def _window(start: str, end: str) -> TimeWindow:
    return TimeWindow(
        start_utc=datetime.fromisoformat(start).replace(tzinfo=UTC),
        end_utc=datetime.fromisoformat(end).replace(tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------


def test_open_meteo_observed_returns_reanalysis_with_provenance_and_distance() -> None:
    http = _open_meteo_http()
    provider = OpenMeteoWeatherProvider(http)
    window = _window("2024-01-01T00:00", "2024-01-02T00:00")

    series = provider.observed(PORTLAND, window)

    assert series.source_kind == "reanalysis"
    assert series.station_or_cell == "open-meteo-grid:43.1100,-70.2100"
    expected_distance = haversine_km(43.0, -70.0, 43.11, -70.21)
    assert series.distance_km == pytest.approx(expected_distance)
    assert series.distance_km > 0.0
    assert series.provenance.source == "open-meteo:archive-api (ERA5)"
    assert "CC BY 4.0" in series.provenance.terms_tag
    assert "Open-Meteo" in series.provenance.terms_tag
    assert OPEN_METEO_TERMS in series.provenance.terms_tag
    assert len(series.points) == 24
    assert all(window.contains(p.timestamp_utc) for p in series.points)


def test_open_meteo_observed_requests_the_window_bounds() -> None:
    http = _open_meteo_http()
    provider = OpenMeteoWeatherProvider(http)
    window = _window("2024-01-01T00:00", "2024-01-02T00:00")

    provider.observed(PORTLAND, window)

    url, params = http.calls[0]
    assert "archive-api.open-meteo.com" in url
    assert params["start_date"] == "2024-01-01"
    assert params["end_date"] == "2024-01-01"
    assert params["timezone"] == "UTC"


def test_open_meteo_forecast_is_forecast_kind_and_bounded_by_horizon() -> None:
    http = _open_meteo_http()
    provider = OpenMeteoWeatherProvider(http)

    series = provider.forecast(PORTLAND, 24)

    assert series.source_kind == "forecast"
    assert len(series.points) == 24
    _, params = http.calls[0]
    assert params["forecast_hours"] == "24"
    assert ATTRIBUTION["open-meteo"] in series.provenance.terms_tag


def test_open_meteo_values_are_converted_to_si_units() -> None:
    http = _open_meteo_http()
    provider = OpenMeteoWeatherProvider(http)

    series = provider.observed(PORTLAND, _window("2024-01-01T00:00", "2024-01-01T06:00"))
    first = series.points[0]

    assert first.wind_mps == pytest.approx(8.0 / 3.6)
    assert first.rh is not None
    assert first.humidity_ratio is not None
    assert 0.0 < first.humidity_ratio < 0.02
    assert first.timestamp_utc.tzinfo is not None
    assert first.timestamp_utc.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# NWS
# ---------------------------------------------------------------------------


def test_nws_observed_uses_nearest_station_and_records_distance() -> None:
    http = _nws_http()
    provider = NWSWeatherProvider(http)
    window = _window("2024-01-01T00:00", "2024-01-01T08:00")

    series = provider.observed(BOSTON, window)

    assert series.source_kind == "observed"
    assert series.station_or_cell == "nws-station:KBOS"
    expected = haversine_km(42.36, -71.06, 42.3606, -71.0106)
    assert series.distance_km == pytest.approx(expected)
    assert 0.0 < series.distance_km < 10.0
    assert "National Weather Service" in series.provenance.terms_tag
    assert "public domain" in series.provenance.terms_tag
    # 8 hourly observations in the window, one of which has no temperature.
    assert len(series.points) == 7
    assert all(window.contains(p.timestamp_utc) for p in series.points)
    assert series.points[0].timestamp_utc == datetime(2024, 1, 1, tzinfo=UTC)


def test_nws_observed_never_uses_the_farther_station() -> None:
    http = _nws_http()
    provider = NWSWeatherProvider(http)

    provider.observed(BOSTON, _window("2024-01-01T00:00", "2024-01-01T08:00"))

    assert not any("/stations/KBDR/observations" in url for url in http.urls())


def test_nws_forecast_is_forecast_kind_and_bounded_by_horizon() -> None:
    http = _nws_http()
    provider = NWSWeatherProvider(http)

    series = provider.forecast(BOSTON, 30)

    assert series.source_kind == "forecast"
    assert len(series.points) == 3
    assert series.station_or_cell == "nws-grid:42.3612,-71.0623"
    assert series.distance_km == pytest.approx(
        haversine_km(42.36, -71.06, 42.3612, -71.0623)
    )
    assert "National Weather Service" in series.provenance.terms_tag


# ---------------------------------------------------------------------------
# AMY slicing (LAW-10) and the twin path
# ---------------------------------------------------------------------------


def test_amy_for_calibration_slices_to_the_exact_data_bounds() -> None:
    http = _open_meteo_http()
    provider = OpenMeteoWeatherProvider(http)
    window = _window("2024-01-01T06:00", "2024-01-01T12:00")

    series = amy_for_calibration(provider, PORTLAND, window)

    assert len(series.points) == 6
    assert series.points[0].timestamp_utc == datetime(2024, 1, 1, 6, tzinfo=UTC)
    assert series.points[-1].timestamp_utc == datetime(2024, 1, 1, 11, tzinfo=UTC)
    assert all(window.contains(p.timestamp_utc) for p in series.points)


def test_amy_for_calibration_refuses_forecast_weather() -> None:
    class ForecastOnlyProvider:
        id = "forecast-only"

        def observed(self, location: LatLon, window: TimeWindow) -> WeatherSeries:
            return _series(source_kind="forecast")

        def forecast(self, location: LatLon, horizon_hours: int) -> WeatherSeries:
            return _series(source_kind="forecast")

    with pytest.raises(WeatherResponseError) as excinfo:
        amy_for_calibration(
            ForecastOnlyProvider(),
            BOSTON,
            _window("2024-01-01T00:00", "2024-01-02T00:00"),
        )
    assert "LAW-10" in str(excinfo.value)


def test_amy_for_calibration_refuses_tmy_weather() -> None:
    class TmyProvider:
        id = "tmy"

        def observed(self, location: LatLon, window: TimeWindow) -> WeatherSeries:
            return _series(source_kind="tmy")

        def forecast(self, location: LatLon, horizon_hours: int) -> WeatherSeries:
            return _series(source_kind="tmy")

    with pytest.raises(WeatherResponseError):
        amy_for_calibration(
            TmyProvider(), BOSTON, _window("2024-01-01T00:00", "2024-01-02T00:00")
        )


def test_slice_to_window_is_half_open() -> None:
    series = _series(source_kind="observed")
    window = _window("2024-01-01T01:00", "2024-01-01T03:00")

    sliced = slice_to_window(series, window)

    assert [p.timestamp_utc.hour for p in sliced.points] == [1, 2]


def test_observed_and_forecast_combine_for_the_twin() -> None:
    http = _open_meteo_http()
    provider = OpenMeteoWeatherProvider(http)
    observed = provider.observed(PORTLAND, _window("2024-01-01T00:00", "2024-01-01T06:00"))
    forecast = provider.forecast(PORTLAND, 6)

    pair = merge_observed_and_forecast(observed, forecast)
    twin = TwinWeather(observed=pair[0], forecast=pair[1])

    assert twin.observed.source_kind == "reanalysis"
    assert twin.forecast.source_kind == "forecast"
    stamps = [p.timestamp_utc for p in twin.stepping_points()]
    assert stamps == sorted(stamps)
    assert len(stamps) == len(observed.points) + len(forecast.points)


def test_merge_refuses_a_forecast_that_overlaps_the_observations() -> None:
    observed = _series(source_kind="observed")
    forecast = _series(source_kind="forecast")

    with pytest.raises(WeatherResponseError):
        merge_observed_and_forecast(observed, forecast)


# ---------------------------------------------------------------------------
# Shared behaviour
# ---------------------------------------------------------------------------


def test_both_adapters_satisfy_the_weather_provider_protocol() -> None:
    assert isinstance(OpenMeteoWeatherProvider(_open_meteo_http()), WeatherProvider)
    assert isinstance(NWSWeatherProvider(_nws_http()), WeatherProvider)


def test_adapters_only_call_the_endpoints_they_were_given() -> None:
    om_http = _open_meteo_http()
    OpenMeteoWeatherProvider(om_http).observed(
        PORTLAND, _window("2024-01-01T00:00", "2024-01-02T00:00")
    )
    assert all("open-meteo.com" in url for url in om_http.urls())

    nws_http = _nws_http()
    NWSWeatherProvider(nws_http).observed(
        BOSTON, _window("2024-01-01T00:00", "2024-01-01T08:00")
    )
    assert all("weather.gov" in url for url in nws_http.urls())


def test_attribution_strings_are_shipped_for_both_sources() -> None:
    assert "Open-Meteo" in ATTRIBUTION["open-meteo"]
    assert "CC BY 4.0" in ATTRIBUTION["open-meteo"]
    assert "National Weather Service" in ATTRIBUTION["nws"]


def test_http_failure_is_typed() -> None:
    provider = OpenMeteoWeatherProvider(
        RecordingHttp({})  # every request 404s
    )
    with pytest.raises(WeatherResponseError):
        provider.observed(PORTLAND, _window("2024-01-01T00:00", "2024-01-02T00:00"))


def test_non_json_body_is_typed() -> None:
    class BadHttp:
        def __call__(self, url: str, params: Mapping[str, str]) -> WeatherHttpResponse:
            return WeatherHttpResponse(status=200, body="<html>nope</html>", url=url)

    with pytest.raises(WeatherResponseError):
        OpenMeteoWeatherProvider(BadHttp()).observed(
            PORTLAND, _window("2024-01-01T00:00", "2024-01-02T00:00")
        )


def test_haversine_and_humidity_helpers_are_sane() -> None:
    assert haversine_km(42.0, -71.0, 42.0, -71.0) == pytest.approx(0.0)
    assert haversine_km(42.0, -71.0, 43.0, -71.0) == pytest.approx(111.19, abs=0.1)
    ratio = humidity_ratio_from_rh(50.0, 20.0)
    assert 0.0 < ratio < 0.02
    assert humidity_ratio_from_rh(0.0, 20.0) == pytest.approx(0.0)


def test_fixtures_are_synthetic_json() -> None:
    for name in (
        "openmeteo_archive_response.json",
        "openmeteo_forecast_response.json",
        "nws_points_response.json",
        "nws_station_kbos_response.json",
        "nws_station_kbdr_response.json",
        "nws_observations_response.json",
        "nws_forecast_response.json",
    ):
        payload: Any = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        assert isinstance(payload, dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _series(source_kind: str) -> WeatherSeries:
    from home_energy_twin.types import Provenance, WeatherPoint

    points = [
        WeatherPoint(
            timestamp_utc=datetime(2024, 1, 1, hour, tzinfo=UTC),
            dry_bulb_c=float(hour),
            rh=60.0,
            wind_mps=3.0,
            ghi_wm2=0.0,
        )
        for hour in range(4)
    ]
    return WeatherSeries(
        points=points,
        source_kind=source_kind,  # type: ignore[arg-type]
        station_or_cell="synthetic-station",
        distance_km=1.0,
        provenance=Provenance(
            source="synthetic-fixture",
            retrieved_at_utc=datetime(2024, 1, 1, tzinfo=UTC),
            terms_tag="CC0-1.0 (synthetic)",
        ),
    )