"""What stops a mid-season points amendment being published.

A round amendment is a change on the queue (`round_amend_change`), not a method here. The
amendment's own tables are results': its readers are in results' `amendment_mode_service`.
"""

from __future__ import annotations

import logging

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.modules.module_service import ModuleService, SwitchNotSignedUp
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild

log = logging.getLogger(__name__)


async def _season_exists(db_path: str, season_id: int) -> bool:
    """Whether the season can be read, checked before the approval writes anything."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT 1 FROM seasons WHERE id = ?", (season_id,))
        return await cursor.fetchone() is not None


async def approval_faults(
    db_path: str, season_id: int, bot: LeagueBot, *, module_service: ModuleService
) -> list[str]:
    """Everything that would stop an approved amendment being published.

    Returns the faults as lines a league can read, and an empty list where the whole
    cascade could be carried out.

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

    **Each module answers for its own channels.** The results module knows what its repost
    needs and the attendance module knows what its recalculation needs; this function only
    composes them, and reaches into neither's configuration itself.

    A fault reading whether the attendance module is on stands aside, logged on the host and read as
    off: an attendance switch that cannot be read never refuses an amendment. A switch nobody signed
    up for is not such a fault but one in the bot's assembly, and its `SwitchNotSignedUp` is raised.
    The switch is asked of *module_service*, which the caller hands in: a service asks the bot for
    no service of its own.
    """
    from leaguebot.results.services.publishing import results_post_service

    if not await _season_exists(db_path, season_id):
        return ["The season could not be read, so nothing was changed."]

    guild = (await league_guild(bot)) if bot is not None else None
    faults = await results_post_service.repost_channel_faults(
        db_path, season_id, guild, bot
    )

    # The attendance module recalculates as part of the same cascade, so its channels are
    # part of the same question. Asked only where it is switched on: a league without it
    # must not be refused for an attendance channel it has never configured.
    attendance_on = False
    try:
        attendance_on = await module_service.is_attendance_enabled()
    except SwitchNotSignedUp:
        raise  # a module that never signed up is a fault in the bot's assembly, not a reading
    except Exception:  # noqa: BLE001 — never refuse an amendment on this reader
        log.exception("approval_faults: could not read the attendance module's state")
    if attendance_on:
        from leaguebot.attendance.services import attendance_service

        faults += await attendance_service.recalculation_faults(
            db_path, season_id, guild, bot, cancelled_too=True
        )

    return faults
