"""Font indexing and string measurement (T057).

Resolves a CSS ``font-family`` list to the face a renderer would actually land on, and
measures a string by summing advance widths.

A field whose first declared family is absent is measured against the substitute and
raises a ``FONT_SUBSTITUTED`` **notice**, never a problem (Constitution XIV.4): a host
missing a template's preferred face still renders.

Pure: no database, no Discord.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont, TTLibError

log = logging.getLogger(__name__)

#: Where installed fonts live, per platform.
_FONT_DIRS_WINDOWS = (
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
)
_FONT_DIRS_POSIX = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / ".local/share/fonts",
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
)

_FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}

#: Generic CSS families, mapped to something a host is likely to carry. A generic name
#: is never itself an installed face, so resolving one is a substitution but an expected
#: one — the template asked for "whatever you have".
_GENERIC_FAMILIES = {
    "sans-serif": ("Arial", "Helvetica", "DejaVu Sans", "Liberation Sans"),
    "serif": ("Times New Roman", "DejaVu Serif", "Liberation Serif"),
    "monospace": ("Consolas", "Courier New", "DejaVu Sans Mono"),
}

#: Last resort when nothing at all resolves. Measurement then falls back to an estimate.
_FALLBACK_ADVANCE_RATIO = 0.5


@dataclass(frozen=True)
class ResolvedFont:
    """The face a renderer would land on for a given family list."""

    family: str | None          # the installed family actually used
    path: Path | None
    requested: str              # the first family the template asked for
    substituted: bool           # True when `family` is not `requested`


@lru_cache(maxsize=1)
def font_index() -> dict[str, Path]:
    """Map lowercase family name -> font file.

    Built once and cached for the process lifetime: rebuilding it per render costs more
    than the rasterisation itself.
    """
    index: dict[str, Path] = {}
    directories = _FONT_DIRS_WINDOWS if os.name == "nt" else _FONT_DIRS_POSIX

    for directory in directories:
        if not directory or not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.suffix.lower() not in _FONT_SUFFIXES:
                continue
            for family in _families_of(path):
                index.setdefault(family.lower(), path)

    log.debug("font_index: %d families indexed", len(index))
    return index


def _families_of(path: Path) -> list[str]:
    """Read the family names a font file declares. Unreadable files are skipped."""
    try:
        font = TTFont(str(path), fontNumber=0, lazy=True)
    except (TTLibError, OSError, Exception):  # noqa: BLE001 - a bad font must not break the index
        return []

    families: list[str] = []
    try:
        name_table = font["name"]
        for record in name_table.names:
            # nameID 1 is the family; 16 is the typographic family.
            if record.nameID in (1, 16):
                try:
                    value = record.toUnicode()
                except Exception:  # noqa: BLE001
                    continue
                if value and value not in families:
                    families.append(value)
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            font.close()
        except Exception:  # noqa: BLE001
            pass

    return families


def parse_font_family(declaration: str | None) -> list[str]:
    """Split a CSS ``font-family`` list into its family names, in order."""
    if not declaration:
        return []
    families = []
    for part in declaration.split(","):
        name = part.strip().strip("'\"").strip()
        if name:
            families.append(name)
    return families


def resolve_family(declaration: str | None) -> ResolvedFont:
    """Resolve a ``font-family`` list to the face a renderer would land on."""
    families = parse_font_family(declaration)
    requested = families[0] if families else "sans-serif"
    index = font_index()

    for family in families:
        hit = index.get(family.lower())
        if hit is not None:
            return ResolvedFont(family, hit, requested, substituted=family != requested)

    # Nothing named resolved; try the generic fallbacks the last family implies.
    for family in families + ["sans-serif"]:
        for candidate in _GENERIC_FAMILIES.get(family.lower(), ()):
            hit = index.get(candidate.lower())
            if hit is not None:
                return ResolvedFont(candidate, hit, requested, substituted=True)

    return ResolvedFont(None, None, requested, substituted=True)


@lru_cache(maxsize=64)
def _metrics(path_str: str) -> tuple[dict[str, int], int, int]:
    """Return (advance widths by glyph name, units per em, fallback advance)."""
    font = TTFont(path_str, fontNumber=0, lazy=True)
    hmtx = font["hmtx"]
    upem = font["head"].unitsPerEm
    widths = {name: hmtx[name][0] for name in hmtx.metrics}
    fallback = int(upem * _FALLBACK_ADVANCE_RATIO)
    return widths, upem, fallback


@lru_cache(maxsize=64)
def _cmap(path_str: str) -> dict[int, str]:
    font = TTFont(path_str, fontNumber=0, lazy=True)
    table = font.getBestCmap()
    return dict(table) if table else {}


def measure(text: str, resolved: ResolvedFont, size: float) -> float:
    """Width of *text* set in *resolved* at *size* pixels.

    Falls back to a proportional estimate when no face resolved at all, so measurement
    never raises — a wrap that cannot be measured exactly is still better than a crash.
    """
    if not text:
        return 0.0
    if resolved.path is None:
        return len(text) * size * _FALLBACK_ADVANCE_RATIO

    try:
        widths, upem, fallback = _metrics(str(resolved.path))
        cmap = _cmap(str(resolved.path))
    except Exception as exc:  # noqa: BLE001
        log.warning("measure: unreadable metrics for %s: %s", resolved.path, exc)
        return len(text) * size * _FALLBACK_ADVANCE_RATIO

    total = 0
    for character in text:
        glyph = cmap.get(ord(character))
        total += widths.get(glyph, fallback) if glyph else fallback

    return total * size / upem


def clear_cache() -> None:
    """Drop the memoised index and metrics. Used by tests."""
    font_index.cache_clear()
    _metrics.cache_clear()
    _cmap.cache_clear()
