"""season_classification_service — the classifications a season is bracketed by.

A division's standings and its attendance record are posted after every round. This module
adds the two occasions that bracket those: the **opening** classification, posted once when
the season is approved, and the **final** one, posted once when it completes. Both are the
same two sheets a league already reads every round, differing only in the phrase they carry —
see :class:`leaguebot.core.models.classification_occasion.ClassificationOccasion`, which is where that phrase
and the behaviour around it live.

**Neither posting carries message text.** The phrase naming the occasion is drawn on the
sheet, so a heading above it would only say it twice; the lineup and calendar graphics posted
at approval already go out bare for the same reason. Where a graphic cannot be drawn at all,
the ordinary textual fallback stands in and *is* headed by the phrase, a bare table of names
and numbers otherwise saying nothing about what it is a table of (XIV.7 — a graphic is an
alternative beside the text, never an exception to it).

**The opening is posted one division at a time, by the season's approval, each posting a job
of the change queue** that raises where it cannot post (:func:`post_opening_standings`,
:func:`post_opening_sheet`). **The final one** catches every problem, collects and returns it for
the caller to report to the logging channel, never to a channel a driver reads (XIV.4), and one
division never stops another: season completion is far too consequential to be failed by a
picture.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


async def _opening_round(db_path: str, division_id: int):
    """The division's first round, read as the posting runs, or None where it has none.

    The sheets are drawn against it: not because they stand after it, they stand after nothing,
    but because the grid of rounds down the side of both is read from the season's calendar and
    the heading context every posting resolves is keyed by a round. Every cell in that grid is
    empty, which is exactly what an opening sheet should show. Approval refuses a division with no
    rounds, so None cannot normally happen.
    """
    from leaguebot.core.db.database import get_connection

    async with get_connection(db_path) as db:
        return await (
            await db.execute(
                "SELECT id, round_number, track_name FROM rounds WHERE division_id = ? "
                "ORDER BY round_number LIMIT 1",
                (division_id,),
            )
        ).fetchone()


async def _remove_earlier_try(guild, kept: dict[str, Any] | None) -> dict[str, Any] | None:
    """Take down the standings a try before this one left posted in part, or None where it left none.

    No id of an opening or final standings is recorded, so a table already sent when a later one
    failed is put on the failure for the job to keep (`kept`), and the next try removes it here,
    from the channel it was posted in, which the standings channel may since have left: a channel
    the guild no longer holds holds none of them. A message that will not delete raises, with
    what is still to remove. Once the copies are down, what is returned names none left, so that
    a failure of this try names what it left, even nothing, and the job no longer keeps the
    copies removed.
    """
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.results.services.results_post_service import remove_part_posted_messages

    earlier = [int(each) for each in (kept or {}).get("new") or []]
    if not earlier:
        return None
    posted_in = int((kept or {}).get("channel_id") or 0)
    earlier_channel = guild.get_channel(posted_in) if posted_in else None
    if earlier_channel is not None:
        left, failures = await remove_part_posted_messages(earlier_channel, earlier)
        if left:
            raise StepFailedOnDiscord(
                f"{len(left)} message(s) of the earlier try could not be removed",
                result={"new": left, "channel_id": posted_in},
            ) from (failures[0] if failures else None)
    return {"new": [], "channel_id": posted_in}


@asynccontextmanager
async def _keeping(removed: dict[str, Any] | None, what: str) -> AsyncIterator[None]:
    """Carry what the earlier try's removal left on any failure of this try.

    Where the earlier try's copies are down (*removed* is not None), a failure of this one that
    names no result is given *removed*, and a failure that is not a Discord one becomes one that
    does, so that the job keeps what is true: nothing left standing.
    """
    from leaguebot.core.models.change import StepFailedOnDiscord

    try:
        yield
    except StepFailedOnDiscord as failure:
        if removed is not None and failure.result is None:
            failure.result = removed
        raise
    except Exception as failure:
        if removed is None:
            raise
        raise StepFailedOnDiscord(
            f"the {what} standings could not be posted once the earlier try's were removed",
            result=removed,
        ) from failure


async def _produce_stranding(what: str, channel_id, posting) -> None:
    """Await *posting*, results' ``produce_standings``, naming on its failure a table already sent.

    No id of the classification is recorded, so a table already sent when a later one failed is
    put on the failure for the job to keep, and the next try removes it.
    """
    from leaguebot.core.models.change import StepFailedOnDiscord

    try:
        await posting
    except Exception as failure:
        stranded = [int(each) for each in getattr(failure, "left_standing", None) or []]
        if stranded:
            raise StepFailedOnDiscord(
                f"the {what} standings were posted in part and the rest was refused",
                result={"new": stranded, "channel_id": int(channel_id)},
            ) from failure
        raise


async def post_opening_standings(
    bot: LeagueBot,
    guild,
    db_path: str,
    division_id: int,
    *,
    as_text: bool = False,
    kept: dict[str, Any] | None = None,
) -> None:
    """Post one division's opening standings, both championships, raising where it cannot.

    **The standings channel is read here, from `division_results_config`,** where
    the results module's own settings keep it, as the posting runs. The approval used to look for
    it on a division read that never carries it, so no opening standings were ever posted and nothing
    said so. A division never given a channel posts nothing; one given a channel the guild no
    longer holds raises `StepFailedOnDiscord`, for the change queue to stop on. No message id is
    recorded (results specification: the opening classification is not kept), so a table already
    sent when a later one fails is named on the failure (`result`), and the job keeps it: the
    next try is handed it as *kept* and removes that copy, from the channel it was posted in, before
    it posts both again, failing again, with what it still has to remove, where a message will not
    delete. Once that copy is down, any failure of the try names what the try left, even nothing,
    so that the job no longer keeps the copy removed. *as_text* leaves the graphic out, which is
    how a job retries (Constitution XIV rule 8).
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.image.services.image_results_post import _driver_names
    from leaguebot.results.services import standings_service
    from leaguebot.results.services.results_post_service import (
        _get_show_reserves,
        produce_standings,
    )

    opener = await _opening_round(db_path, division_id)
    if opener is None:
        return
    removed = await _remove_earlier_try(guild, kept)
    async with _keeping(removed, "opening"):
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

        # Twice, deliberately. The order is taken on the name the sheet will actually draw, and the
        # names are resolved from Discord by user id, so the roster has to be known before it can be
        # ordered. The first pass is read only for who is in it; the second is the one that counts.
        roster = await standings_service.opening_driver_standings(db_path, division_id)
        names = await _driver_names(
            bot, guild, [snapshot.driver_user_id for snapshot in roster], division_id=division_id
        )
        driver_snaps = await standings_service.opening_driver_standings(db_path, division_id, names)
        team_snaps = await standings_service.opening_team_standings(db_path, division_id)
        if not driver_snaps:
            return
        await _produce_stranding(
            "opening",
            channel_id,
            produce_standings(
                db_path,
                division_id,
                opener["id"],
                opener["round_number"],
                opener["track_name"] or "",
                channel,
                driver_snaps,
                team_snaps,
                guild,
                await _get_show_reserves(db_path, division_id),
                "",
                bot=None if as_text else bot,
                occasion=ClassificationOccasion.SEASON_OPENING,
            ),
        )


async def post_opening_sheet(
    bot: LeagueBot, guild, db_path: str, division_id: int, *, as_text: bool = False
) -> None:
    """Post one division's opening attendance sheet, raising where it cannot be posted.

    Attendance's own posting, asked to raise (`raise_on_failure`): it saves its own message id,
    posts nothing for a division never given a channel and raises for one given a channel the
    guild no longer holds. *as_text* leaves the graphic out, which is how a job retries.
    """
    from leaguebot.attendance.services.attendance_service import post_attendance_sheet

    opener = await _opening_round(db_path, division_id)
    if opener is None:
        return
    await post_attendance_sheet(
        bot,
        guild,
        db_path,
        opener["id"],
        division_id,
        occasion=ClassificationOccasion.SEASON_OPENING,
        raise_on_failure=True,
        as_text=as_text,
    )


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
    posts both again, as :func:`post_opening_standings` does. *as_text* leaves the graphic out,
    which is how a job retries (Constitution XIV rule 8).
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.results.services import standings_service
    from leaguebot.results.services.results_post_service import (
        _get_show_reserves,
        driver_standings_for_display,
        produce_standings,
    )

    last = await _final_round(db_path, division_id, round_id)
    if last is None:
        return
    removed = await _remove_earlier_try(guild, kept)
    async with _keeping(removed, "final"):
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
        await _produce_stranding(
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
                await _get_show_reserves(db_path, division_id),
                "",
                bot=None if as_text else bot,
                occasion=ClassificationOccasion.SEASON_FINAL,
                fresh=True,
            ),
        )


async def post_final_sheet(
    bot: LeagueBot, guild, db_path: str, division_id: int, round_id: int, *, as_text: bool = False
) -> None:
    """Post one division's final attendance sheet, drawn against *round_id*, raising where it
    cannot be posted.

    Attendance's own posting, asked to raise (`raise_on_failure`): it saves its own message id,
    posts nothing for a division never given a channel and raises for one given a channel the
    guild no longer holds. *as_text* leaves the graphic out, which is how a job retries.
    """
    from leaguebot.attendance.services.attendance_service import post_attendance_sheet

    await post_attendance_sheet(
        bot,
        guild,
        db_path,
        round_id,
        division_id,
        occasion=ClassificationOccasion.SEASON_FINAL,
        raise_on_failure=True,
        as_text=as_text,
    )


async def post_final_classifications(bot: LeagueBot, guild, db_path: str, season_id: int) -> list[str]:
    """Post every division's final standings and attendance sheet.

    Drawn against the division's **last round that has results**, whose classification the
    final sheet *is* — the season's last word, restated under its own heading rather than
    recomputed into something the round's own posting would disagree with. A division that
    ran no round at all is skipped: there is no classification to publish. Each posting is
    :func:`post_final_standings` or :func:`post_final_sheet`, whose failure is collected here as
    a problem line until the completion on the change queue carries them as jobs.
    """
    from leaguebot.core.db.database import get_connection

    problems: list[str] = []
    if bot is None or guild is None:
        return problems

    async with get_connection(db_path) as db:
        rows = await (
            await db.execute(
                """
                SELECT d.id AS division_id, d.name AS division_name,
                       (SELECT r.id FROM rounds r
                         JOIN session_results sr ON sr.round_id = r.id
                        WHERE r.division_id = d.id AND sr.status = 'ACTIVE'
                        ORDER BY r.round_number DESC LIMIT 1) AS last_round_id
                FROM divisions d
                WHERE d.season_id = ?
                ORDER BY d.id
                """,
                (season_id,),
            )
        ).fetchall()

    for row in rows:
        division_id = row["division_id"]
        division_name = row["division_name"]
        round_id = row["last_round_id"]
        if round_id is None:
            log.info(
                "final classification: %s ran no round, so there is none to publish",
                division_name,
            )
            continue

        try:
            await post_final_standings(bot, guild, db_path, division_id, round_id)
        except Exception as exc:  # noqa: BLE001 — one division never stops the next
            log.exception("final classification: %s standings failed", division_name)
            problems.append(f"{division_name} standings: {exc}")

        try:
            await post_final_sheet(bot, guild, db_path, division_id, round_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("final classification: %s attendance failed", division_name)
            problems.append(f"{division_name} attendance: {exc}")

    return problems
