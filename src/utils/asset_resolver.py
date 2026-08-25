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

    #: True where a ``FALLBACK`` outcome came from the **packaged** tier rather than the
    #: configured directory. Diagnostics and tests only: XIV.13 requires the two to report
    #: the *same* notice, so no caller may branch on this to change what a league is told.
    from_packaged: bool = False

    @property
    def found(self) -> bool:
        return self.outcome is AssetOutcome.FOUND

    @property
    def used_fallback(self) -> bool:
        return self.outcome is AssetOutcome.FALLBACK

    @property
    def missing(self) -> bool:
        return self.outcome is AssetOutcome.MISSING

    @property
    def drew_own_file(self) -> bool:
        """Whether the file drawn is the datum's *own*, from whichever tier supplied it.

        Always true of ``FOUND``. True of ``FALLBACK`` only on a closed-set hit in the
        packaged tier, where the module drew its own correct file for the datum — nothing
        was substituted, and a caller must not describe that as a placeholder standing in
        for missing artwork.
        """
        if self.path is None or not self.slug:
            return False
        return self.path.name == f"{self.slug}{ASSET_EXTENSION}"


def resolve_asset(
    directory: Path, datum: str, *, packaged: Path | None = None, closed_set: bool = False
) -> AssetResolution:
    """Find the file for *datum* in *directory*, or a fallback, or neither.

    One computed name, one existence test — no globbing, no case-insensitive scan, no
    trying other extensions. Determinism is what lets a league reason about why an asset
    did or did not appear.

    *packaged* is the directory shipped with the module for this asset class, and is the
    **second fallback tier** (Constitution XIV.13, 047 FR-040). Four paths and no fifth:

    1. the datum's own file in *directory*                     → ``FOUND``
    2. no such file, but *directory* holds a fallback          → ``FALLBACK``
    3. neither, but *packaged* holds a fallback                → ``FALLBACK``, packaged
    4. neither tier holds a fallback                           → ``MISSING``

    The datum's own file is sought in *directory* **alone**. A file of that name sitting in
    *packaged* is never drawn: a league that supplied no image must not be handed one it
    did not choose. Only ``fallback.svg`` is read from the packaged tier.

    *closed_set* is the one exception (Constitution XIV.13, v6.1.0): where the datum is the
    module's **own vocabulary** rather than a value the league chose — the league did not
    choose it and cannot be incomplete against it — path 3 first tries the datum's own file
    in *packaged* before its ``fallback.svg``, so a customised directory missing an entry
    still draws the module's own correct file rather than a generic placeholder. This is
    still reported as ``FALLBACK`` with ``from_packaged=True``: it is still not what the
    league supplied, only more specific than the generic placeholder would have been. Such
    a hit answers True to :attr:`AssetResolution.drew_own_file`, which is how a caller tells
    it apart from a placeholder when saying what happened.

    Whether a datum qualifies is not this module's decision: it is asked of
    ``is_closed_set_datum`` by the caller, because a whole class can qualify (marker,
    weather) or an individual reserved slug can (``mystery``, ``other``).

    Omitting *packaged* gives the single-tier behaviour that stood before v6.0.0.
    """
    slug = normalise(datum)

    if slug:
        candidate = directory / f"{slug}{ASSET_EXTENSION}"
        if candidate.is_file():
            return AssetResolution(AssetOutcome.FOUND, candidate, slug)

    fallback = directory / FALLBACK_ASSET_NAME
    if fallback.is_file():
        return AssetResolution(AssetOutcome.FALLBACK, fallback, slug)

    if packaged is not None:
        if closed_set and slug:
            packaged_exact = packaged / f"{slug}{ASSET_EXTENSION}"
            if packaged_exact.is_file():
                return AssetResolution(
                    AssetOutcome.FALLBACK, packaged_exact, slug, from_packaged=True
                )

        packaged_fallback = packaged / FALLBACK_ASSET_NAME
        if packaged_fallback.is_file():
            return AssetResolution(
                AssetOutcome.FALLBACK, packaged_fallback, slug, from_packaged=True
            )

    return AssetResolution(AssetOutcome.MISSING, None, slug)


def has_fallback(directory: Path, *, packaged: Path | None = None) -> bool:
    """Whether this asset class can survive a datum it has no file for.

    Both tiers, taken as a whole (FR-043). A configured directory carrying no fallback of
    its own is still tolerant where the packaged directory carries one.
    """
    if (directory / FALLBACK_ASSET_NAME).is_file():
        return True
    return packaged is not None and (packaged / FALLBACK_ASSET_NAME).is_file()
