"""What stops a mid-season points amendment being published.

A round amendment is a change on the queue (`round_amend_change`), not a method here. The
amendment's own tables are results': its readers are in `amendment_mode_service`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from leaguebot.framework.services.switches.module_service import ModuleService, SwitchNotSignedUp
from leaguebot.core.services.seasons.season_service import season_exists
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.league_server import league_guild
from leaguebot.results.services.publishing import results_post_service

if TYPE_CHECKING:
    from leaguebot.image.services.render.image_drawing import ImageDrawing
    from leaguebot.results.services.module.attendance_hook import AttendanceAfterReview

log = logging.getLogger(__name__)


class ApprovalFaultKind(Enum):
    """What an approval's faults are about, which says what would mend them."""

    SEASON = "season"
    """The season cannot be read: nothing was changed, and there is nothing to repair."""

    REACH = "reach"
    """The bot cannot reach the server, or its own member in it: the line carries its own next step."""

    CHANNELS = "channels"
    """One or more channels the approval must post to are missing or unpostable: the channel
    commands repair them."""


@dataclass(frozen=True)
class ApprovalFaults:
    """The faults that would stop an approval, and what kind they are.

    **The kinds never mix.** The season is read first and refuses alone; the bot's reach of the
    server is asked before any channel, and `repost_channel_faults` returns that line alone
    where it fails, as attendance's channel faults return nothing then. So *lines* is either
    every one of them channel faults, or exactly one fault of another kind, and a caller that
    offers the channel commands as the remedy need only look at *kind*.
    """

    lines: tuple[str, ...]
    kind: ApprovalFaultKind


async def approval_faults(
    db_path: str, season_id: int, bot: LeagueBot, *, module_service: ModuleService,
    attendance: AttendanceAfterReview, drawing: ImageDrawing,
) -> ApprovalFaults:
    """Everything that would stop an approved amendment being published.

    Returns the faults as lines a league can read, with their kind (`ApprovalFaultKind`), and no
    lines where the whole cascade could be carried out.

    **Called three times, from one reading.** `/results amend review` calls it to draw its
    panel, so a manager sees what is wrong while deciding rather than after pressing Approve;
    the points approval's check calls it again at the press and once more when the approval
    runs, before it writes anything, so the refusal cannot be stepped around by a panel drawn
    when the channels were still sound. This is how results' `validate_modification_ordering`
    (`amendment_mode_service`) is already used, and how
    `approval_checks.placement_confirmation_faults` serves the season's own review and
    confirmation — the report and the refusal are the same reading, so they cannot drift.

    **A cancelled division is included**, refused at the press: the approval rescores, reposts and
    recalculates its raced rounds as a live division's, so the results, standings, attendance and
    verdicts channels it configured are asked too, as the results specification's "every division's"
    requires.

    **Each module answers for its own channels.** Results knows what its repost needs, and
    attendance, asked through *attendance* (`AttendanceAfterReview.recalculation_faults`), what
    its recalculation needs; this function only composes them, and reaches into neither's
    configuration itself, importing nothing of attendance's.

    A fault reading whether the attendance module is on stands aside, logged on the host and read as
    off: an attendance switch that cannot be read never refuses an amendment. A switch nobody signed
    up for is not such a fault but one in the bot's assembly, and its `SwitchNotSignedUp` is raised.
    The switch is asked of *module_service* and whether a channel will be sent pictures of
    *drawing*, the image module's, which the caller hands in: a service asks the bot for no service
    of its own.
    """
    # Checked before the approval writes anything.
    if not await season_exists(db_path, season_id):
        return ApprovalFaults(("The season could not be read.",), ApprovalFaultKind.SEASON)

    guild = (await league_guild(bot)) if bot is not None else None
    _, reach = results_post_service.repost_reach_fault(guild, bot)
    if reach:
        return ApprovalFaults(tuple(reach), ApprovalFaultKind.REACH)

    faults = await results_post_service.repost_channel_faults(
        db_path, season_id, guild, bot, drawing=drawing
    )

    # The attendance module recalculates as part of the same cascade, so its channels are
    # part of the same question. Asked only where it is switched on: a league without it
    # must not be refused for an attendance channel it has never configured.
    attendance_on = False
    try:
        attendance_on = await module_service.is_enabled("attendance")
    except SwitchNotSignedUp:
        raise  # a module that never signed up is a fault in the bot's assembly, not a reading
    except Exception:  # noqa: BLE001 — never refuse an amendment on this reader
        log.exception("approval_faults: could not read the attendance module's state")
    if attendance_on:
        faults += await attendance.recalculation_faults(season_id, guild)

    return ApprovalFaults(tuple(faults), ApprovalFaultKind.CHANNELS)
