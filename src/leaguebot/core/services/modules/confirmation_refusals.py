"""What a module refuses a season's confirmation on, gathered through the hook it is asked by.

A season is confirmed at three moments (`Confirmation`): the placements review is read, the
approval is pressed, and the configuration is judged. Each module signs a function up to
`LeagueBot.confirmation_refused`; at each moment core makes one call, handing every subscriber the
season and which confirmation is judged, and `read_refusals` returns what they hand back: each
module's refusals (`Refusal`), in the order the modules signed up. Each subscriber checks its own
module's switch and hands nothing while its module is off, so core never asks whether a module is
on before asking whether it refuses.

What a refusal is made of depends on the moment, and the subscriber words it for that moment:
*message* is the private reply the review sends, the reply the approval refuses with, or the line
the configuration's faults list; *reason* is the line the approval's refusal is logged beneath,
where the moment has one.

**A subscriber that cannot judge is not caught here**: the failure reaches the caller's own
failure path, as any other read of the confirmation's does.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from leaguebot.core.utils.hooks import AwaitedHook


class Confirmation(Enum):
    """Which confirmation of a season is being judged."""

    REVIEW = "review"
    APPROVAL = "approval"
    CONFIGURATION = "configuration"


@dataclass(frozen=True)
class Refusal:
    """One thing a module refuses a season's confirmation on."""

    message: str
    reason: str | None = None


async def read_refusals(
    confirmation_refused: AwaitedHook, season_id: int, confirmation: Confirmation
) -> list[Refusal]:
    """Every refusal the modules hand back for *confirmation* of *season_id*, in the order they
    signed up and, within a module, the order it hands them in."""
    handed = await confirmation_refused.call(season_id, confirmation)
    return [refusal for refused in handed for refusal in refused]
