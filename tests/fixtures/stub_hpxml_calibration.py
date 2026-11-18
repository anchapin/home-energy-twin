#!/usr/bin/env python3
"""Synthetic stand-in for the OpenStudio-HPXML-Calibration engine.

This is a **test fixture**, not an engine: it speaks the same request/result
file contract as the real out-of-process tool so the adapter's hand-off can
be exercised with no heavy runtime installed (RD-7).  Everything it reports
is synthetic (LAW-11).

Usage::

    stub_hpxml_calibration.py --version
    stub_hpxml_calibration.py --request REQUEST.json --output RESULT.json

The reported version is taken from ``HET_STUB_ENGINE_VERSION`` (default
``0.1.0``) so the pinned ``0.1.x`` line can be exercised and drifted versions
can be refused.
"""

from __future__ import annotations

import json
import os
import sys

VERSION = os.environ.get("HET_STUB_ENGINE_VERSION", "0.1.0")
MINOR_LINE = ".".join(VERSION.split(".")[:2])


def _main(argv: list[str]) -> int:
    if "--version" in argv:
        sys.stdout.write(f"OpenStudio-HPXML-Calibration {VERSION}\n")
        return 0

    args = dict(zip(argv[1::2], argv[2::2]))
    request_path = args.get("--request")
    output_path = args.get("--output")
    if request_path is None or output_path is None:
        sys.stderr.write("usage: --request FILE --output FILE\n")
        return 2

    with open(request_path, encoding="utf-8") as handle:
        request = json.load(handle)

    house = request["house"]
    payload = {
        "house_id": house["house_id"],
        "state_hash": house["state_hash"],
        "engine_ref": f"openstudio-hpxml-calibration-{MINOR_LINE}",
        "hpxml_ref": house["hpxml_ref"],
        "parameters": {
            "ua": {
                "value": 0.5,
                "low": 0.4,
                "high": 0.6,
                "unit": "W/K",
                "method": "stub-engine",
            }
        },
        "metrics": {
            "nmbe_pct": 2.1,
            "cvrmse_pct": 11.0,
            "granularity": "billing_period",
            "guideline14_pass": True,
        },
        "calibration_window": {
            "start_utc": "2026-01-01T00:00:00Z",
            "end_utc": "2026-12-31T00:00:00Z",
        },
        "data_granularity": "billing_period",
        "status": "valid",
        "bpi2400_metrics": {"cvrmse": 0.18},
    }
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
