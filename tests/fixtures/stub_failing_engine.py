#!/usr/bin/env python3
"""Synthetic stub engine that reports the pinned version but then fails.

Used to prove that a failing engine run surfaces as a typed error rather than
a crash or a fabricated model.
"""

from __future__ import annotations

import os
import sys

VERSION = os.environ.get("HET_STUB_ENGINE_VERSION", "0.1.0")


def main(argv: list[str]) -> int:
    if "--version" in argv:
        sys.stdout.write(f"OpenStudio-HPXML-Calibration {VERSION}\n")
        return 0
    sys.stderr.write("synthetic calibration failure\n")
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
