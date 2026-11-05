"""season_classification_service — the classification a season closes on.

A division's standings and its attendance record are posted after every round. This module
adds the occasion that closes those: the **final** classification, posted once when the season
completes. It is the same two sheets a league already reads every round, differing only in the
phrase they carry — see
:class:`leaguebot.core.models.classification_occasion.ClassificationOccasion`, which is where
that phrase and the behaviour around it live. The **opening** classification, posted once when
the season is approved, is results' and attendance's own
(:func:`leaguebot.results.services.season_standings_posts.post_opening_standings`,
:func:`leaguebot.attendance.services.sheet_posting.post_opening_sheet`), signed up to the hook
core declares for placements confirmed (#283).

**Neither posting carries message text.** The phrase naming the occasion is drawn on the
sheet, so a heading above it would only say it twice; the lineup and calendar graphics posted
at approval already go out bare for the same reason. Where a graphic cannot be drawn at all,
the ordinary textual fallback stands in and *is* headed by the phrase, a bare table of names
and numbers otherwise saying nothing about what it is a table of (XIV.7 — a graphic is an
alternative beside the text, never an exception to it).

**The final classification is posted one division at a time, by the season's completion, each
posting a job of the change queue** that raises where it cannot post
(:func:`post_final_standings`, :func:`post_final_sheet`), so the queue stops there and a league
admin retries or discards it; a picture Discord refuses is posted as text in the same try, never
failing the completion on its own. Its standings share the earlier-try bookkeeping results'
opening standings use (:func:`leaguebot.results.services.season_standings_posts.remove_earlier_try`,
``keeping``, ``produce_stranding``), imported from there rather than kept twice; that import is
listed under ``.importlinter`` with #283 until the final posts move there too.
"""

from __future__ import annotations

import logging
from typing import Any

from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.results.services.season_standings_posts import (
    keeping,
    produce_stranding,
    remove_earlier_try,
)

log = logging.getLogger(__name__)


async def _final_round(db_path: str, division_id: int, round_id: int):
    """The round a division's final classification is drawn against, read as the posting runs."""
    from leaguebot.core.db.database import get_connection

    async with get_connection(db_path) as db:
        return await (
            await db.execute(
                "SELECT id, round_number, track_name FROM rounds WHERE id = ? AND division_id = ?",
                (round_id, division_id),
            )
        ).fetchone()


async def post_final_standings(
    bot: LeagueBot,
    guild,
    db_path: str,
    division_id: int,
    round_id: int,
    *,
    as_text: bool = False,
    kept: dict[str, Any] | None = None,
) -> None:
    """Post one division's final standings, both championships, raising where it cannot.

    Drawn against *round_id*, the division's last round that has results, whose classification
    the final standings *are*: the season's last word, restated under its own heading rather than
    recomputed into something the round's own posting would disagree with. **They are posted
    beside the round's own standings, replacing nothing** (results and image specifications): the
    text is a new message, never an edit of the round's, and no id is recorded. The standings
    channel is read here, from `division_results_config`, as the posting runs. A division never
    given one posts nothing; one given a channel the guild no longer holds raises
    `StepFailedOnDiscord`, for the change queue to stop on. A division with no driver to rank
    posts nothing. A table already sent when a later one failed is named on the failure
    (`result`), and the job keeps it: the next try is handed it as *kept* and removes it before it
    posts both again, as results' opening standings do. *as_text* leaves the graphic out,
    which is how a job retries (Constitution XIV rule 8).
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.results.services import standings_service
    from leaguebot.results.services.results_post_service import (
        driver_standings_for_display,
        produce_standings,
        reserves_in_standings,
    )

    last = await _final_round(db_path, division_id, round_id)
    if last is None:
        return
    removed = await remove_earlier_try(guild, kept)
    async with keeping(removed, "final"):
        async with get_connection(db_path) as db:
            config = await (
                await db.execute(
                    "SELECT standings_channel_id FROM division_results_config "
                    "WHERE division_id = ?",
                    (division_id,),
                )
            ).fetchone()
        channel_id = config["standings_channel_id"] if config is not None else None
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            raise StepFailedOnDiscord(
                f"the standings channel (id {channel_id}) is not in the server"
            )

        driver_snaps = await driver_standings_for_display(
            db_path, division_id, round_id, guild, bot
        )
        team_snaps = await standings_service.compute_team_standings(
            db_path, division_id, round_id
        )
        if not driver_snaps:
            return
        await produce_stranding(
            "final",
            channel_id,
            produce_standings(
                db_path,
                division_id,
                round_id,
                last["round_number"],
                last["track_name"] or "",
                channel,
                driver_snaps,
                team_snaps,
                guild,
                await reserves_in_standings(db_path, division_id),
                "",
                bot=None if as_text else bot,
                occasion=ClassificationOccasion.SEASON_FINAL,
                fresh=True,
            ),
        )


async def post_final_sheet(
    bot: LeagueBot, guild, db_path: str, division_id: int, round_id: int, *, as_text: bool = False
) -> dict[str, Any]:
    """Post one division's final attendance sheet, drawn against *round_id*, raising where it
    cannot be posted, and give the job its result (`attendance_service.sheet_result`).

    Attendance's own posting, which always raises and saves nothing: a division with no
    attendance channel stops the queue, its failure naming `/attendance channel attendance`, and
    so does one given a channel the guild no longer holds.
    The final sheet stands beside the last round's and takes no live slot, so nothing is saved for
    it and nothing deleted. *as_text* leaves the graphic out, which is how a job retries.
    """
    from leaguebot.attendance.services.attendance_service import (
        post_attendance_sheet,
        sheet_result,
    )

    return sheet_result(await post_attendance_sheet(
        bot,
        guild,
        db_path,
        round_id,
        division_id,
        occasion=ClassificationOccasion.SEASON_FINAL,
        as_text=as_text,
    ))
