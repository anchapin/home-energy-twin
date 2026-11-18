"""Optional OCHRE adapter — import-guarded, never a core dependency (RD-7).

This module is the *only* place in the repository that may reference OCHRE.
It deliberately does **not** invent an OCHRE API: OCHRE could not be
installed in the Spike B environment (see ``spikes/ochre/REPORT.md``), so the
mapping from OCHRE's simulation objects to the ``EngineAdapter`` shape is
still unknown and must not be guessed.

What this module does provide is the honest probe the harness needs:

* :func:`ochre_available` — is an OCHRE distribution importable right now?
* :func:`ochre_version` — the pinned version string, if installed;
* :func:`require_ochre` — import it or raise :class:`OchreUnavailableError`
  with the exact reason (not installed / not importable / broken).

The core package never imports this module, and ``home_energy_twin`` keeps
working with no engine installed (RD-7).
"""

from __future__ import annotations

from types import ModuleType
from typing import cast

__all__ = ["OchreUnavailableError", "ochre_available", "ochre_version", "require_ochre"]

_DISTRIBUTION = "ochre"


class OchreUnavailableError(RuntimeError):
    """Raised when the OCHRE engine is required but not usable."""


def ochre_available() -> bool:
    """True when an OCHRE distribution is importable in this interpreter."""
    try:
        require_ochre()
    except OchreUnavailableError:
        return False
    return True


def ochre_version() -> str | None:
    """The installed OCHRE version string, or ``None`` when not installed."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(_DISTRIBUTION)
    except PackageNotFoundError:
        return None


def require_ochre() -> ModuleType:
    """Import OCHRE or raise :class:`OchreUnavailableError` with the reason."""
    try:
        import ochre  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on environment
        found = ochre_version()
        detail = f"version {found}" if found else "no distribution metadata found"
        raise OchreUnavailableError(
            "OCHRE is not usable in this interpreter "
            f"(import failed: {exc}; {detail}). "
            "Install it into a scratch virtualenv and re-run the spike, or use "
            "the fallback RC engine (spikes/ochre/rc_engine.py)."
        ) from exc
    return cast(ModuleType, ochre)
