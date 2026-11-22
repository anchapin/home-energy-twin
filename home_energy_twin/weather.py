"""Weather adapters for home-energy-twin (issue #11).

Two :class:`home_energy_twin.protocols.WeatherProvider` implementations live
here:

* :class:`OpenMeteoWeatherProvider` — the **hosted** Open-Meteo API only
  (``api.open-meteo.com`` for forecasts, ``archive-api.open-meteo.com`` for
  the ERA5 reanalysis archive).  The AGPLv3 Open-Meteo *server code* is never
  vendored, imported or executed (AGENTS.md "No closed/copyleft
  contamination", RD-11).
* :class:`NWSWeatherProvider` — the US National Weather Service
  (``api.weather.gov``), the public-domain first-class alternative that lets a
  commercial user swap providers without code changes (RD-11).

Rules honoured here:

* **LAW-10** — calibration weather is *actual* weather (observed/reanalysis)
  sliced to the utility data's exact bounds.  :func:`amy_for_calibration`
  refuses a forecast/TMY series and slices the returned series to the
  requested window.  Forward simulation weather (forecast) is a separate
  call path and says which kind it is.
* **LAW-12 / RD-11** — every returned series carries
  :class:`~home_energy_twin.types.Provenance` (source, retrieval time, terms /
  attribution string) and the representativeness of the data
  (``station_or_cell`` and ``distance_km``).
* **LAW-5** — every timestamp is UTC; the local timezone is never used here.
* **LAW-11** — no real user data ships with this module; the adapters only
  ever emit data fetched for the caller's own location.
* **LAW-6** — the only network calls are to the weather endpoints the caller
  configured (the provider is instantiated by configuration, never imported
  by core code).

The HTTP layer is injected (:class:`HttpGetter`), so the tests exercise the
adapters against recorded *synthetic* API responses under ``tests/fixtures/``
and never touch the network (AGENTS.md "Tests must pass without network
access").
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Optional, Protocol

from .errors import EnergyTwinError
from .protocols import WeatherProvider
from .types import (
    LatLon,
    Provenance,
    TimeWindow,
    WeatherPoint,
    WeatherSeries,
)

__all__ = [
    "ATTRIBUTION",
    "HttpGetter",
    "NWSWeatherProvider",
    "OPEN_METEO_TERMS",
    "OpenMeteoWeatherProvider",
    "TwinWeather",
    "WeatherHttpResponse",
    "WeatherResponseError",
    "WeatherUnavailable",
    "amy_for_calibration",
    "haversine_km",
    "humidity_ratio_from_rh",
    "merge_observed_and_forecast",
    "slice_to_window",
    "urllib_get",
]

UTC: Final = timezone.utc

#: Attribution strings that must travel with every output (RD-11).
ATTRIBUTION: Final[dict[str, str]] = {
    "open-meteo": (
        "Weather data by Open-Meteo.com (CC BY 4.0); "
        "https://open-meteo.com/ , https://open-meteo.com/en/licence"
    ),
    "nws": (
        "Weather data from the U.S. National Weather Service "
        "(public domain); https://www.weather.gov/ , https://www.nws.noaa.gov/disclaimer"
    ),
}

#: Terms / licence tags recorded in :class:`~home_energy_twin.types.Provenance`.
OPEN_METEO_TERMS: Final[str] = "CC BY 4.0 (Open-Meteo hosted API, non-commercial free tier)"
NWS_TERMS: Final[str] = "US public domain (NOAA/NWS)"

_USER_AGENT: Final[str] = "home-energy-twin/0.1 (weather adapter; +https://github.com/anchapin/home-energy-twin)"
_EARTH_RADIUS_KM: Final[float] = 6371.0088
_KMH_TO_MPS: Final[float] = 1.0 / 3.6


class WeatherUnavailable(EnergyTwinError):
    """The weather endpoint could not be reached (network/host problem)."""


class WeatherResponseError(EnergyTwinError):
    """The weather endpoint answered with an unusable body."""


@dataclass(frozen=True)
class WeatherHttpResponse:
    """A minimal HTTP response, so the HTTP layer can be swapped in tests."""

    status: int
    body: str
    url: str


class HttpGetter(Protocol):
    """Callable that performs a GET and returns a :class:`WeatherHttpResponse`."""

    def __call__(self, url: str, params: Mapping[str, str]) -> WeatherHttpResponse:
        ...


def urllib_get(url: str, params: Mapping[str, str]) -> WeatherHttpResponse:
    """Default HTTP getter built on the standard library only.

    No third-party HTTP dependency is added to the core package; the adapters
    are swappable and the tests inject their own getter.
    """
    query = urllib.parse.urlencode(dict(params))
    full_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(
        full_url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            status = int(response.status)
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        body = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
    except urllib.error.URLError as exc:  # pragma: no cover - network path
        raise WeatherUnavailable(
            f"weather endpoint unreachable: {full_url}", reason=str(exc.reason)
        ) from exc
    return WeatherHttpResponse(status=status, body=body, url=full_url)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def haversine_km(
    lat_a: float, lon_a: float, lat_b: float, lon_b: float
) -> float:
    """Great-circle distance between two WGS84 points, in kilometres."""
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def humidity_ratio_from_rh(rh_pct: float, temp_c: float) -> float:
    """Humidity ratio (kg water / kg dry air) from relative humidity.

    Uses the Magnus (ASHRAE-style) saturation pressure relation at sea level,
    which is the same approximation the change-point baseline's degree-day
    features rely on.
    """
    saturation_pa = 610.78 * math.exp(17.2694 * temp_c / (temp_c + 238.3))
    rh = min(100.0, max(0.0, rh_pct)) / 100.0
    vapour_pa = rh * saturation_pa
    pressure_pa = 101325.0
    return 0.622 * vapour_pa / max(1e-6, pressure_pa - 0.378 * vapour_pa)


# ---------------------------------------------------------------------------
# Slicing helpers (LAW-10)
# ---------------------------------------------------------------------------


def slice_to_window(series: WeatherSeries, window: TimeWindow) -> WeatherSeries:
    """Slice ``series`` exactly to ``[window.start_utc, window.end_utc)``.

    Calibration weather must cover the utility data's exact bounds (LAW-10),
    so this is the single place where the "slice to data bounds" rule is
    implemented.  The provenance and representativeness fields are carried
    over unchanged.
    """
    points = [p for p in series.points if window.contains(p.timestamp_utc)]
    return series.model_copy(update={"points": points})


def amy_for_calibration(
    provider: WeatherProvider,
    location: LatLon,
    window: TimeWindow,
) -> WeatherSeries:
    """Fetch actual (observed/reanalysis) weather sliced to ``window``.

    ``provider`` is any object satisfying
    :class:`home_energy_twin.protocols.WeatherProvider`.  The result is
    refused with a typed error when the provider hands back forecast or TMY
    weather, because calibration may only use actual weather (LAW-10).
    """
    series = provider.observed(location, window)
    if series.source_kind not in ("observed", "reanalysis"):
        raise WeatherResponseError(
            "calibration requires observed/reanalysis weather (LAW-10)",
            reason=f"provider returned source_kind={series.source_kind!r}",
        )
    return slice_to_window(series, window)


def merge_observed_and_forecast(
    observed: WeatherSeries, forecast: WeatherSeries
) -> tuple[WeatherSeries, WeatherSeries]:
    """Return the observed and forecast series as an ordered twin pair.

    The twin steps the engine with *observed* weather up to now and
    *forecast* weather after it.  The two kinds are never merged into a
    single series (the kinds differ), so this helper only checks the
    ordering invariant and returns the pair.
    """
    if observed.points and forecast.points:
        if forecast.points[0].timestamp_utc < observed.points[-1].timestamp_utc:
            raise WeatherResponseError(
                "forecast series overlaps the observed series",
                reason="forecast must start at or after the last observation",
            )
    return observed, forecast


@dataclass(frozen=True)
class TwinWeather:
    """The observed + forecast pair the live twin steps through."""

    observed: WeatherSeries
    forecast: WeatherSeries

    def stepping_points(self) -> list[WeatherPoint]:
        """All points in time order: observed first, then forecast."""
        points = list(self.observed.points) + list(self.forecast.points)
        return sorted(points, key=lambda point: point.timestamp_utc)


# ---------------------------------------------------------------------------
# Open-Meteo (hosted API only)
# ---------------------------------------------------------------------------

_OPEN_METEO_ARCHIVE_URL: Final[str] = "https://archive-api.open-meteo.com/v1/archive"
_OPEN_METEO_FORECAST_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"

_OPEN_METEO_HOURLY: Final[str] = (
    "temperature_2m,relative_humidity_2m,wind_speed_10m,"
    "shortwave_radiation,direct_radiation,diffuse_radiation"
)


def _parse_utc(text: str) -> datetime:
    """Parse an ISO-8601 timestamp and return it as UTC."""
    cleaned = text.replace("Z", "+00:00")
    moment = datetime.fromisoformat(cleaned)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _as_float(value: Any) -> Optional[float]:
    """Return ``value`` as a float, or ``None`` when it is missing."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


class OpenMeteoWeatherProvider:
    """``WeatherProvider`` backed by the hosted Open-Meteo API.

    * ``observed`` returns ERA5 reanalysis (archive API) for historical
      windows and the model's own past days (forecast API) for recent
      windows; both are actual weather, never TMY.
    * ``forecast`` returns forward-looking model weather only.
    """

    id: str = "open-meteo"

    def __init__(
        self,
        http_get: Optional[HttpGetter] = None,
        *,
        archive_url: str = _OPEN_METEO_ARCHIVE_URL,
        forecast_url: str = _OPEN_METEO_FORECAST_URL,
        recent_window_days: int = 5,
        attribution: Optional[str] = None,
    ) -> None:
        self._http_get: HttpGetter = http_get or urllib_get
        self._archive_url = archive_url
        self._forecast_url = forecast_url
        self._recent_window_days = recent_window_days
        self._attribution = attribution or ATTRIBUTION["open-meteo"]

    # -- public protocol ----------------------------------------------------

    def observed(self, location: LatLon, window: TimeWindow) -> WeatherSeries:
        """Observed/reanalysis weather for ``window`` (AMY, LAW-10)."""
        now = datetime.now(tz=UTC)
        if window.end_utc <= now - timedelta(days=self._recent_window_days):
            return self._archive(location, window)
        return self._recent_observations(location, window)

    def forecast(self, location: LatLon, horizon_hours: int) -> WeatherSeries:
        """Forecast weather for forward simulation only."""
        if horizon_hours <= 0:
            raise WeatherResponseError(
                "forecast horizon must be positive", reason=f"got {horizon_hours}"
            )
        params: dict[str, str] = {
            "latitude": f"{location.latitude:.4f}",
            "longitude": f"{location.longitude:.4f}",
            "hourly": _OPEN_METEO_HOURLY,
            "forecast_hours": str(horizon_hours),
            "timezone": "UTC",
        }
        payload = self._fetch_json(self._forecast_url, params)
        points, cell_lat, cell_lon = self._parse_hourly(payload)
        if not points:
            raise WeatherResponseError(
                "open-meteo returned no forecast points",
                reason="empty hourly block",
            )
        start = points[0].timestamp_utc
        end = start + timedelta(hours=horizon_hours)
        kept = [p for p in points if start <= p.timestamp_utc < end]
        return WeatherSeries(
            points=kept,
            source_kind="forecast",
            station_or_cell=self._cell_name(cell_lat, cell_lon),
            distance_km=haversine_km(
                location.latitude, location.longitude, cell_lat, cell_lon
            ),
            provenance=self._provenance("open-meteo:forecast-api"),
        )

    # -- internals ----------------------------------------------------------

    def _archive(self, location: LatLon, window: TimeWindow) -> WeatherSeries:
        params: dict[str, str] = {
            "latitude": f"{location.latitude:.4f}",
            "longitude": f"{location.longitude:.4f}",
            "start_date": window.start_utc.date().isoformat(),
            "end_date": (window.end_utc - timedelta(seconds=1)).date().isoformat(),
            "hourly": _OPEN_METEO_HOURLY,
            "timezone": "UTC",
        }
        payload = self._fetch_json(self._archive_url, params)
        points, cell_lat, cell_lon = self._parse_hourly(payload)
        series = WeatherSeries(
            points=points,
            source_kind="reanalysis",
            station_or_cell=self._cell_name(cell_lat, cell_lon),
            distance_km=haversine_km(
                location.latitude, location.longitude, cell_lat, cell_lon
            ),
            provenance=self._provenance("open-meteo:archive-api (ERA5)"),
        )
        return slice_to_window(series, window)

    def _recent_observations(self, location: LatLon, window: TimeWindow) -> WeatherSeries:
        """Past days from the forecast endpoint (model analysis, not TMY)."""
        now = datetime.now(tz=UTC)
        past_days = max(
            1, (now.date() - window.start_utc.date()).days + 1
        )
        params: dict[str, str] = {
            "latitude": f"{location.latitude:.4f}",
            "longitude": f"{location.longitude:.4f}",
            "hourly": _OPEN_METEO_HOURLY,
            "past_days": str(past_days),
            "forecast_days": "1",
            "timezone": "UTC",
        }
        payload = self._fetch_json(self._forecast_url, params)
        points, cell_lat, cell_lon = self._parse_hourly(payload)
        series = WeatherSeries(
            points=points,
            source_kind="observed",
            station_or_cell=self._cell_name(cell_lat, cell_lon),
            distance_km=haversine_km(
                location.latitude, location.longitude, cell_lat, cell_lon
            ),
            provenance=self._provenance("open-meteo:forecast-api (past days)"),
        )
        return slice_to_window(series, window)

    def _cell_name(self, cell_lat: float, cell_lon: float) -> str:
        return f"open-meteo-grid:{cell_lat:.4f},{cell_lon:.4f}"

    def _provenance(self, source: str) -> Provenance:
        return Provenance(
            source=source,
            retrieved_at_utc=datetime.now(tz=UTC),
            terms_tag=f"{self._attribution} | terms: {OPEN_METEO_TERMS}",
        )

    def _fetch_json(self, url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        response = self._http_get(url, params)
        if response.status != 200:
            raise WeatherResponseError(
                f"open-meteo returned HTTP {response.status}",
                reason=response.url,
            )
        try:
            payload = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise WeatherResponseError(
                "open-meteo returned a non-JSON body", reason=response.url
            ) from exc
        if not isinstance(payload, dict):
            raise WeatherResponseError(
                "open-meteo returned a non-object body", reason=response.url
            )
        return payload

    def _parse_hourly(
        self, payload: Mapping[str, Any]
    ) -> tuple[list[WeatherPoint], float, float]:
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            raise WeatherResponseError(
                "open-meteo payload has no hourly block", reason="missing 'hourly'"
            )
        times = hourly.get("time")
        if not isinstance(times, list):
            raise WeatherResponseError(
                "open-meteo payload has no hourly time list", reason="missing 'time'"
            )
        cell_lat = _as_float(payload.get("latitude")) or 0.0
        cell_lon = _as_float(payload.get("longitude")) or 0.0

        def column(name: str) -> list[Any]:
            raw = hourly.get(name)
            if isinstance(raw, list):
                return raw
            return []

        temps = column("temperature_2m")
        humidity = column("relative_humidity_2m")
        wind = column("wind_speed_10m")
        ghi = column("shortwave_radiation")
        dni = column("direct_radiation")
        dhi = column("diffuse_radiation")

        points: list[WeatherPoint] = []
        for index, stamp in enumerate(times):
            if not isinstance(stamp, str):
                continue
            temperature = _as_float(temps[index]) if index < len(temps) else None
            if temperature is None:
                continue
            rh = _as_float(humidity[index]) if index < len(humidity) else None
            wind_kmh = _as_float(wind[index]) if index < len(wind) else None
            points.append(
                WeatherPoint(
                    timestamp_utc=_parse_utc(stamp),
                    dry_bulb_c=temperature,
                    rh=rh,
                    humidity_ratio=(
                        humidity_ratio_from_rh(rh, temperature) if rh is not None else None
                    ),
                    wind_mps=(wind_kmh * _KMH_TO_MPS) if wind_kmh is not None else 0.0,
                    ghi_wm2=(_as_float(ghi[index]) or 0.0) if index < len(ghi) else 0.0,
                    dni_wm2=(_as_float(dni[index]) if index < len(dni) else None),
                    dhi_wm2=(_as_float(dhi[index]) if index < len(dhi) else None),
                )
            )
        return points, cell_lat, cell_lon


# ---------------------------------------------------------------------------
# NWS (api.weather.gov)
# ---------------------------------------------------------------------------

_NWS_BASE_URL: Final[str] = "https://api.weather.gov"
_NWS_USER_AGENT: Final[str] = _USER_AGENT


class NWSWeatherProvider:
    """``WeatherProvider`` backed by the US National Weather Service API.

    NWS is US-public-domain data, the first-class alternative provider that
    lets a commercial deployment swap away from the Open-Meteo free tier
    (RD-11).  Observations come from the nearest observation station; the
    distance from the house to that station is recorded, never hidden.
    """

    id: str = "nws"

    def __init__(
        self,
        http_get: Optional[HttpGetter] = None,
        *,
        base_url: str = _NWS_BASE_URL,
        user_agent: str = _NWS_USER_AGENT,
        max_stations: int = 3,
        attribution: Optional[str] = None,
    ) -> None:
        self._http_get: HttpGetter = http_get or urllib_get
        self._base_url = base_url
        self._user_agent = user_agent
        self._max_stations = max_stations
        self._attribution = attribution or ATTRIBUTION["nws"]

    # -- public protocol ----------------------------------------------------

    def observed(self, location: LatLon, window: TimeWindow) -> WeatherSeries:
        """Observed weather for ``window`` from the nearest NWS station."""
        points_meta = self._fetch_points(location)
        stations = self._station_ids(points_meta)
        if not stations:
            raise WeatherResponseError(
                "NWS listed no observation stations for this location",
                reason="points.properties.observationStations empty",
            )
        best: Optional[tuple[float, str]] = None
        for station_id in stations[: self._max_stations]:
            metadata = self._fetch_json(
                f"{self._base_url}/stations/{station_id}"
            )
            lat, lon = self._station_coordinates(metadata)
            distance = haversine_km(location.latitude, location.longitude, lat, lon)
            if best is None or distance < best[0]:
                best = (distance, station_id)
        if best is None:  # pragma: no cover - guarded above
            raise WeatherResponseError(
                "no NWS station could be located", reason="no candidate stations"
            )
        distance_km, station_id = best
        observations = self._fetch_json(
            f"{self._base_url}/stations/{station_id}/observations",
            {
                "start": window.start_utc.isoformat().replace("+00:00", "Z"),
                "end": window.end_utc.isoformat().replace("+00:00", "Z"),
            },
        )
        points = self._parse_observations(observations)
        series = WeatherSeries(
            points=points,
            source_kind="observed",
            station_or_cell=f"nws-station:{station_id}",
            distance_km=distance_km,
            provenance=self._provenance(f"nws:observations:{station_id}"),
        )
        return slice_to_window(series, window)

    def forecast(self, location: LatLon, horizon_hours: int) -> WeatherSeries:
        """Forecast weather for forward simulation only."""
        if horizon_hours <= 0:
            raise WeatherResponseError(
                "forecast horizon must be positive", reason=f"got {horizon_hours}"
            )
        points_meta = self._fetch_points(location)
        forecast_url = self._forecast_url(points_meta)
        payload = self._fetch_json(forecast_url)
        periods = self._parse_forecast(payload)
        grid_lat, grid_lon = self._grid_coordinates(points_meta)
        series = WeatherSeries(
            points=periods,
            source_kind="forecast",
            station_or_cell=f"nws-grid:{grid_lat:.4f},{grid_lon:.4f}",
            distance_km=haversine_km(
                location.latitude, location.longitude, grid_lat, grid_lon
            ),
            provenance=self._provenance("nws:forecast"),
        )
        if not periods:
            return series
        start = periods[0].timestamp_utc
        end = start + timedelta(hours=horizon_hours)
        kept = [p for p in periods if start <= p.timestamp_utc < end]
        return series.model_copy(update={"points": kept})

    # -- internals ----------------------------------------------------------

    def _fetch_points(self, location: LatLon) -> Mapping[str, Any]:
        return self._fetch_json(
            f"{self._base_url}/points/{location.latitude:.4f},{location.longitude:.4f}"
        )

    def _fetch_json(
        self, url: str, params: Optional[Mapping[str, str]] = None
    ) -> Mapping[str, Any]:
        response = self._http_get(url, params or {})
        if response.status != 200:
            raise WeatherResponseError(
                f"NWS returned HTTP {response.status}", reason=response.url
            )
        try:
            payload = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise WeatherResponseError(
                "NWS returned a non-JSON body", reason=response.url
            ) from exc
        if not isinstance(payload, dict):
            raise WeatherResponseError(
                "NWS returned a non-object body", reason=response.url
            )
        return payload

    def _provenance(self, source: str) -> Provenance:
        return Provenance(
            source=source,
            retrieved_at_utc=datetime.now(tz=UTC),
            terms_tag=f"{self._attribution} | terms: {NWS_TERMS}",
        )

    @staticmethod
    def _properties(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        properties = payload.get("properties")
        if isinstance(properties, dict):
            return properties
        return {}

    def _station_ids(self, points_meta: Mapping[str, Any]) -> list[str]:
        raw = self._properties(points_meta).get("observationStations")
        stations: list[str] = []
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str):
                    stations.append(entry.rstrip("/").rsplit("/", 1)[-1])
        return stations

    def _station_coordinates(self, metadata: Mapping[str, Any]) -> tuple[float, float]:
        geometry = metadata.get("geometry")
        if isinstance(geometry, dict):
            coordinates = geometry.get("coordinates")
            if isinstance(coordinates, list) and len(coordinates) >= 2:
                lon = _as_float(coordinates[0])
                lat = _as_float(coordinates[1])
                if lat is not None and lon is not None:
                    return lat, lon
        raise WeatherResponseError(
            "NWS station metadata has no coordinates", reason="geometry.coordinates"
        )

    def _grid_coordinates(self, points_meta: Mapping[str, Any]) -> tuple[float, float]:
        geometry = points_meta.get("geometry")
        if isinstance(geometry, dict):
            coordinates = geometry.get("coordinates")
            if isinstance(coordinates, list) and len(coordinates) >= 2:
                lon = _as_float(coordinates[0])
                lat = _as_float(coordinates[1])
                if lat is not None and lon is not None:
                    return lat, lon
        properties = self._properties(points_meta)
        relative = properties.get("relativeLocation")
        geometry = relative.get("geometry") if isinstance(relative, dict) else None
        if isinstance(geometry, dict):
            coordinates = geometry.get("coordinates")
            if isinstance(coordinates, list) and len(coordinates) >= 2:
                lon = _as_float(coordinates[0])
                lat = _as_float(coordinates[1])
                if lat is not None and lon is not None:
                    return lat, lon
        raise WeatherResponseError(
            "NWS points metadata has no coordinates", reason="geometry.coordinates"
        )

    def _forecast_url(self, points_meta: Mapping[str, Any]) -> str:
        url = self._properties(points_meta).get("forecast")
        if not isinstance(url, str) or not url:
            raise WeatherResponseError(
                "NWS points metadata has no forecast URL",
                reason="properties.forecast missing",
            )
        return url

    def _parse_observations(self, payload: Mapping[str, Any]) -> list[WeatherPoint]:
        features = payload.get("features")
        if not isinstance(features, list):
            raise WeatherResponseError(
                "NWS observation payload has no features", reason="missing 'features'"
            )
        points: list[WeatherPoint] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            properties = self._properties(feature)
            stamp = properties.get("timestamp")
            if not isinstance(stamp, str):
                continue
            temperature = self._quantity(properties.get("temperature"))
            if temperature is None:
                continue
            rh = self._quantity(properties.get("relativeHumidity"))
            wind_kmh = self._quantity(properties.get("windSpeed"))
            points.append(
                WeatherPoint(
                    timestamp_utc=_parse_utc(stamp),
                    dry_bulb_c=temperature,
                    rh=rh,
                    humidity_ratio=(
                        humidity_ratio_from_rh(rh, temperature) if rh is not None else None
                    ),
                    wind_mps=(wind_kmh * _KMH_TO_MPS) if wind_kmh is not None else 0.0,
                    ghi_wm2=0.0,
                    dni_wm2=None,
                    dhi_wm2=None,
                )
            )
        points.sort(key=lambda point: point.timestamp_utc)
        return points

    def _parse_forecast(self, payload: Mapping[str, Any]) -> list[WeatherPoint]:
        properties = self._properties(payload)
        periods = properties.get("periods")
        if not isinstance(periods, list):
            raise WeatherResponseError(
                "NWS forecast payload has no periods", reason="missing 'periods'"
            )
        points: list[WeatherPoint] = []
        for period in periods:
            if not isinstance(period, dict):
                continue
            start = period.get("startTime")
            temperature = period.get("temperature")
            if not isinstance(start, str) or not isinstance(temperature, dict):
                continue
            value = _as_float(temperature.get("value"))
            if value is None:
                continue
            rh = self._quantity(period.get("relativeHumidity"))
            wind_kmh = self._quantity(period.get("windSpeed"))
            points.append(
                WeatherPoint(
                    timestamp_utc=_parse_utc(start),
                    dry_bulb_c=value,
                    rh=rh,
                    humidity_ratio=(
                        humidity_ratio_from_rh(rh, value) if rh is not None else None
                    ),
                    wind_mps=(wind_kmh * _KMH_TO_MPS) if wind_kmh is not None else 0.0,
                    ghi_wm2=0.0,
                    dni_wm2=None,
                    dhi_wm2=None,
                )
            )
        points.sort(key=lambda point: point.timestamp_utc)
        return points

    @staticmethod
    def _quantity(raw: Any) -> Optional[float]:
        """Read a NWS ``{value, unitCode}`` quantity."""
        if isinstance(raw, dict):
            return _as_float(raw.get("value"))
        return _as_float(raw)