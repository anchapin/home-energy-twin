#!/usr/bin/env python3
"""Synthetic stub engine that reports no version at all.

Used to prove the adapter refuses an engine whose ``--version`` banner
carries no version token (typed error, never a silent pass).
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if "--version" in argv:
        sys.stdout.write("stub engine, build unknown\n")
        return 0
    sys.stderr.write("stub engine without a version banner\n")
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
