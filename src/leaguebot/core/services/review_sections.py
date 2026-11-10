"""The modules' sections of a season review, gathered through the hook a review reads them by.

A review (`Review`) is the placements review or the configuration review. Each module signs a
function up to `LeagueBot.review_sections_read`; at the moment a review gathers its lines, core
makes one call, handing every subscriber which review is gathered, the season and the divisions
the review shows, and `read_review_sections` returns what they hand back: each module's section
(`ReviewSection`) or nothing, in the order the modules signed up. That order is the order the
review lays the sections out in, so the builder's order is the review's, and core sorts by no
module's name.

Each subscriber reads its own module's switch and decides what it hands back while its module is
off, so core never asks whether a module is on before asking for its section: most hand nothing,
and results still names its configurations in the placements review. **A subscriber that cannot read
its section is not caught here**: the failure reaches the review command's own failure path, as
any other read of the review's does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from leaguebot.core.utils.hooks import AwaitedHook


class Review(Enum):
    """Which review is gathering its lines."""

    PLACEMENTS = "placements"
    CONFIGURATION = "configuration"


@dataclass(frozen=True)
class ReviewSection:
    """One module's part of a review.

    *lines* are the section's own message, headed by the module's title; an empty list is a
    module with nothing to say of itself. *modules_note* is what the review's Modules section
    shows beside the module's on-or-off line, or None.
    """

    module: str
    lines: list[str] = field(default_factory=list)
    modules_note: str | None = None


async def read_review_sections(
    review_sections_read: AwaitedHook,
    review: Review,
    *,
    season_id: int,
    division_ids: Sequence[int],
) -> list[ReviewSection]:
    """The sections the modules hand back for *review* of *season_id*, in the order they signed
    up; a module that is off, or has no section, hands nothing and is left out."""
    handed = await review_sections_read.call(
        review=review, season_id=season_id, division_ids=list(division_ids)
    )
    return [section for section in handed if section is not None]


async def read_division_lines(
    division_lines_read: AwaitedHook,
    *,
    season_id: int,
    division_ids: Sequence[int],
) -> list[dict[int, list[str]]]:
    """The lines the modules hand back for each division's configuration message in the
    placements review, one mapping by division id for each module that answered, in the order
    they signed up (results' channels, then attendance's, then weather's): the review lays them out
    as they come.
    A module with nothing to say hands an empty mapping."""
    return list(
        await division_lines_read.call(season_id=season_id, division_ids=list(division_ids))
    )
