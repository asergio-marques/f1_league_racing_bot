"""Resolving a datum to an asset file (Constitution XIV.13).

Pure: no database, no Discord, no configuration lookup. Given a directory and a datum, it
answers with a path, a fallback, or nothing.

The normalisation is the project's own, and it is **underscores**. It is the rule the
proof of concept already implements — ``normalize()`` in ``resources/poc/build_poc.py``,
whose docstring calls it "the spec's normalization" — and the rule every asset already
shipped under ``resources/`` is named by. Constitution v2.13.0 briefly stated a hyphen;
that was an invention and was withdrawn in v3.0.0.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from models.image_constants import FALLBACK_ASSET_NAME

#: Every asset is SVG. There is no fallback to another extension: resolution is
#: deterministic, and guessing at `.png` would make a missing file look like a present one.
ASSET_EXTENSION = ".svg"


def normalise(text: str) -> str:
    """The slug a datum resolves to, without its extension.

    Trim, lowercase, decompose and drop combining marks, replace each run of characters
    that is neither a letter nor a digit with a single underscore, strip leading and
    trailing underscores.

        ``Red Bull Racing`` → ``red_bull_racing``
        ``São Paulo``       → ``sao_paulo``
        ``Emilia-Romagna``  → ``emilia_romagna``

    Total and pure: any string in, a string out. A datum of nothing but punctuation
    normalises to the empty string, which resolves to no file — handled by the caller as
    an unresolved asset rather than as an error here.
    """
    decomposed = unicodedata.normalize("NFKD", (text or "").strip().lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))

    out: list[str] = []
    previous_underscore = False
    for ch in stripped:
        if ch.isalnum():
            out.append(ch)
            previous_underscore = False
        elif not previous_underscore:
            out.append("_")
            previous_underscore = True

    return "".join(out).strip("_")


def filename_for(datum: str) -> str:
    """The filename a datum resolves to — ``red_bull_racing.svg``."""
    return f"{normalise(datum)}{ASSET_EXTENSION}"


class AssetOutcome(Enum):
    """How an asset lookup ended. The caller classifies severity, not this module."""

    #: The datum's own file was there.
    FOUND = "FOUND"

    #: No file for the datum, but the directory carries a fallback. A notice.
    FALLBACK = "FALLBACK"

    #: No file and no fallback. Fatal or not, depending on the field's classification.
    MISSING = "MISSING"


@dataclass(frozen=True)
class AssetResolution:
    """The outcome of one lookup, with the path when there is one."""

    outcome: AssetOutcome
    path: Path | None = None
    #: The slug that was looked for, so a notice can name what had no file of its own.
    slug: str = ""

    @property
    def found(self) -> bool:
        return self.outcome is AssetOutcome.FOUND

    @property
    def used_fallback(self) -> bool:
        return self.outcome is AssetOutcome.FALLBACK

    @property
    def missing(self) -> bool:
        return self.outcome is AssetOutcome.MISSING


def resolve_asset(directory: Path, datum: str) -> AssetResolution:
    """Find the file for *datum* inside *directory*, or its fallback, or neither.

    One computed name, one existence test — no globbing, no case-insensitive scan, no
    trying other extensions. Determinism is what lets a league reason about why an asset
    did or did not appear.
    """
    slug = normalise(datum)

    if slug:
        candidate = directory / f"{slug}{ASSET_EXTENSION}"
        if candidate.is_file():
            return AssetResolution(AssetOutcome.FOUND, candidate, slug)

    fallback = directory / FALLBACK_ASSET_NAME
    if fallback.is_file():
        return AssetResolution(AssetOutcome.FALLBACK, fallback, slug)

    return AssetResolution(AssetOutcome.MISSING, None, slug)


def has_fallback(directory: Path) -> bool:
    """Whether this asset class can survive a datum it has no file for."""
    return (directory / FALLBACK_ASSET_NAME).is_file()
