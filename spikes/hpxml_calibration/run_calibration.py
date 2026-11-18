"""Run one calibration through the engine and write the result as JSON.

This is the executable form of the hands-on script in ``SPIKE_REPORT.md``
(§8.3).  It is spike scaffolding: it is not imported by ``home_energy_twin``
and it registers no production ``Calibrator``.

Inputs are **synthetic** (LAW-11):

* ``--hpxml`` — a synthetic HPXML document (its SHA-256 becomes ``hpxml_ref``);
* ``--bills`` — a JSON file of synthetic monthly bills;
* ``--weather`` — optional JSON file holding an observed/reanalysis
  ``WeatherSeries`` (LAW-10: TMY weather is never used for calibration; the
  caller supplies the actual-weather slice for the billing period).

The engine executable is located through ``HET_HPXML_CALIBRATION_BIN`` only.
When it is absent the command exits with the shared typed error
``home_energy_twin.errors.EngineUnavailable``.

Usage::

    python3 -m spikes.hpxml_calibration.run_calibration \\
        --hpxml spikes/hpxml_calibration/fixtures/home.xml \\
        --bills spikes/hpxml_calibration/fixtures/bills.json \\
        --output spikes/hpxml_calibration/fixtures/calibration_result.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from home_energy_twin.errors import EnergyTwinError
from home_energy_twin.types import (
    EnergyPoint,
    HPXMLRef,
    House,
    LatLon,
    Provenance,
    UtilitySeries,
    WeatherSeries,
)

from .adapter import HPXMLCalibrationAdapter

__all__ = ["build_house", "build_utility_series", "main", "run"]


def build_house(
    hpxml_path: Path,
    *,
    house_id: str,
    timezone: str,
    latitude: float,
    longitude: float,
    state_hash: str,
) -> House:
    """Build the :class:`House` for a synthetic HPXML document."""
    digest = hashlib.sha256(hpxml_path.read_bytes()).hexdigest()
    return House(
        house_id=UUID(house_id),
        hpxml_ref=HPXMLRef(uri=hpxml_path.resolve().as_uri(), sha256=digest),
        timezone=timezone,
        location=LatLon(latitude=latitude, longitude=longitude),
        state_hash=state_hash,
    )


def build_utility_series(
    bills: dict[str, Any],
    *,
    granularity: str = "billing_period",
) -> list[UtilitySeries]:
    """Build monthly synthetic bill series from the bills JSON file.

    Expected shape::

        {"bills": [{"fuel": "electricity", "start_utc": "...",
                    "end_utc": "...", "value": 1420.0, "unit": "kWh"}, ...]}
    """
    retrieved_at = datetime.now(tz=UTC)
    grouped: dict[str, list[EnergyPoint]] = {}
    for bill in bills.get("bills", []):
        fuel = str(bill["fuel"])
        grouped.setdefault(fuel, []).append(
            EnergyPoint(
                start_utc=bill["start_utc"],
                end_utc=bill["end_utc"],
                value=float(bill["value"]),
                unit=str(bill.get("unit", "kWh")),
            )
        )
    series = []
    for fuel, points in grouped.items():
        series.append(
            UtilitySeries(
                fuel=fuel,  # type: ignore[arg-type]
                kind="import",
                granularity=granularity,  # type: ignore[arg-type]
                points=sorted(points, key=lambda p: p.start_utc),
                stamp_semantics="interval_start",
                provenance=Provenance(
                    source="synthetic-spike-a-bills",
                    retrieved_at_utc=retrieved_at,
                    terms_tag="synthetic",
                ),
                meter_id=f"synthetic-{fuel}",
            )
        )
    return series


def run(
    *,
    hpxml: Path,
    bills: Path,
    output: Path,
    house_id: str,
    timezone: str,
    latitude: float,
    longitude: float,
    state_hash: str,
    weather: Optional[Path] = None,
) -> dict[str, Any]:
    """Run one calibration and write the resulting model as JSON."""
    house = build_house(
        hpxml,
        house_id=house_id,
        timezone=timezone,
        latitude=latitude,
        longitude=longitude,
        state_hash=state_hash,
    )
    utility = build_utility_series(json.loads(bills.read_text(encoding="utf-8")))
    if weather is None:
        weather_series = WeatherSeries(
            points=[],
            source_kind="observed",
            station_or_cell="synthetic",
            distance_km=0.0,
            provenance=Provenance(
                source="synthetic-spike-a-weather",
                retrieved_at_utc=datetime.now(tz=UTC),
                terms_tag="synthetic",
            ),
        )
    else:
        weather_series = WeatherSeries.model_validate(
            json.loads(weather.read_text(encoding="utf-8"))
        )
    model = HPXMLCalibrationAdapter().calibrate(house, utility, weather_series)
    payload = model.model_dump(mode="json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns 0 on success, 2 when the engine is absent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hpxml", type=Path, required=True)
    parser.add_argument("--bills", type=Path, required=True)
    parser.add_argument("--weather", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--house-id", default="11111111-2222-3333-4444-555555555555")
    parser.add_argument("--timezone", default="America/Juneau")
    parser.add_argument("--latitude", type=float, default=58.3019)
    parser.add_argument("--longitude", type=float, default=-134.4195)
    parser.add_argument("--state-hash", default="synthetic-state-hash")
    args = parser.parse_args(argv)
    try:
        payload = run(
            hpxml=args.hpxml,
            bills=args.bills,
            output=args.output,
            house_id=args.house_id,
            timezone=args.timezone,
            latitude=args.latitude,
            longitude=args.longitude,
            state_hash=args.state_hash,
            weather=args.weather,
        )
    except EnergyTwinError as exc:
        print(f"engine unavailable: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload["metrics"], indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
