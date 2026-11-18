"""Owner-local CLI for a Green Button DMD export.

    python3 -m spikes.green_button path/to/your-export.xml

This is the step that has to happen **on the owner's machine** with the real
DMD file: the file itself is never committed (LAW-11), but the summary this
prints can be pasted into the quirks report without leaking account data
(values can be replaced by the owner before pasting).

The CLI is offline and read-only: it opens one file and prints a summary.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from .parser import GreenButtonFile, parse_dmd_file


def summarise(parsed: GreenButtonFile) -> str:
    """Render a compact, pasteable summary of a parsed export."""
    lines: list[str] = []
    lines.append("Green Button DMD summary")
    lines.append(f"  timestamp basis    : {parsed.timestamp_basis}")
    lines.append(f"  points             : {parsed.point_count}")
    lines.append(f"  series             : {len(parsed.series)}")
    lines.append(f"  estimated reads    : {parsed.estimated_read_count}")
    lines.append(f"  gaps               : {len(parsed.gaps)}")
    lines.append(f"  overlaps           : {len(parsed.overlaps)}")
    lines.append(f"  provenance source  : {parsed.provenance.source}")
    lines.append(f"  terms tag          : {parsed.provenance.terms_tag}")
    lines.append("")
    lines.append("  meter                     fuel         kind       gran            sem              points")
    for series in parsed.series:
        lines.append(
            f"  {str(series.meter_id)[-12:]:<25} {series.fuel:<12} "
            f"{series.kind:<10} {series.granularity:<14} "
            f"{series.stamp_semantics:<16} {len(series.points)}"
        )
    if parsed.gaps:
        lines.append("")
        lines.append("  gaps:")
        for gap in parsed.gaps:
            lines.append(
                f"    {gap.meter_id[-12:]} {gap.after_end_utc.isoformat()} -> "
                f"{gap.before_start_utc.isoformat()} ({gap.duration})"
            )
    if parsed.overlaps:
        lines.append("")
        lines.append("  overlaps:")
        for overlap in parsed.overlaps:
            lines.append(
                f"    {overlap.meter_id[-12:]} "
                f"{overlap.before_start_utc.isoformat()} < "
                f"{overlap.after_end_utc.isoformat()} ({overlap.duration})"
            )
    if parsed.warnings:
        lines.append("")
        lines.append("  warnings:")
        for warning in parsed.warnings:
            lines.append(f"    - {warning}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m spikes.green_button",
        description="Summarise a Green Button DMD (ESPI XML) export.",
    )
    parser.add_argument("path", help="path to the DMD export (never commit it)")
    parser.add_argument(
        "--terms-tag",
        default="unspecified",
        help="terms/provenance tag recorded with the series (LAW-12)",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2
    parsed = parse_dmd_file(path, terms_tag=args.terms_tag)
    print(summarise(parsed))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(main())
