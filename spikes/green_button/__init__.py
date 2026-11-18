"""Spike C — Green Button DMD (ESPI XML) parser.

Issue #5 (``docs/ISSUES.md``) · Phase 0 Spike C · RD-6 · RD-12b · SPEC §2.2.

The package parses a Green Button "Download My Data" (DMD) export — Atom XML
carrying NAESB ESPI ``UsagePoint`` / ``MeterReading`` / ``IntervalBlock`` /
``IntervalReading`` elements — into the repository's ``UtilitySeries`` domain
type.

Nothing in this package talks to the network, and no real user data belongs
here (LAW-11): the committed fixture under ``tests/fixtures/`` is synthetic.
"""

from .parser import (
    CoverageIssue,
    GreenButtonFile,
    MeterReadSummary,
    parse_dmd_bytes,
    parse_dmd_file,
    parse_dmd_text,
)

__all__ = [
    "CoverageIssue",
    "GreenButtonFile",
    "MeterReadSummary",
    "parse_dmd_bytes",
    "parse_dmd_file",
    "parse_dmd_text",
]
