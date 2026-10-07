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
    next try is handed it as *kept* and removes that copy before it posts both again, failing
    again, with what it still has to remove, where a message will not delete. *as_text* leaves
    the graphic out, which is how a job retries (Constitution XIV rule 8).
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.image.services.image_results_post import _driver_names
    from leaguebot.results.services import standings_service
    from leaguebot.results.services.results_post_service import (
        _get_show_reserves,
        produce_standings,
        remove_part_posted_messages,
    )

    opener = await _opening_round(db_path, division_id)
    if opener is None:
        return
    async with get_connection(db_path) as db:
        config = await (
            await db.execute(
                "SELECT standings_channel_id FROM division_results_config WHERE division_id = ?",
                (division_id,),
            )
        ).fetchone()
    channel_id = config["standings_channel_id"] if config is not None else None
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        raise StepFailedOnDiscord(f"the standings channel (id {channel_id}) is not in the server")
    earlier = [int(each) for each in (kept or {}).get("new") or []]
    if earlier:
        left, failures = await remove_part_posted_messages(channel, earlier)
        if left:
            raise StepFailedOnDiscord(
                f"{len(left)} message(s) of the earlier try could not be removed",
                result={"new": left, "channel_id": int(channel_id)},
            ) from (failures[0] if failures else None)

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
    try:
        await produce_standings(
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
        )
    except Exception as failure:
        # No id of the opening classification is recorded, so a table already sent when a later
        # one failed is put on the failure for the job to keep, and the next try removes it.
        stranded = [int(each) for each in getattr(failure, "left_standing", None) or []]
        if stranded:
            raise StepFailedOnDiscord(
                "the opening standings were posted in part and the rest was refused",
                result={"new": stranded, "channel_id": int(channel_id)},
            ) from failure
        raise


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


async def post_final_classifications(bot: LeagueBot, guild, db_path: str, season_id: int) -> list[str]:
    """Post every division's final standings and attendance sheet.

    Drawn against the division's **last round that has results**, whose classification the
    final sheet *is* — the season's last word, restated under its own heading rather than
    recomputed into something the round's own posting would disagree with. A division that
    ran no round at all is skipped: there is no classification to publish.
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services import standings_service
    from leaguebot.attendance.services.attendance_service import post_attendance_sheet
    from leaguebot.results.services.results_post_service import (
        _get_show_reserves,
        driver_standings_for_display,
        post_standings,
    )

    problems: list[str] = []
    if bot is None or guild is None:
        return problems

    async with get_connection(db_path) as db:
        rows = await (
            await db.execute(
                """
                SELECT d.id AS division_id, d.name AS division_name,
                       drc.standings_channel_id AS standings_channel_id,
                       (SELECT r.id FROM rounds r
                         JOIN session_results sr ON sr.round_id = r.id
                        WHERE r.division_id = d.id AND sr.status = 'ACTIVE'
                        ORDER BY r.round_number DESC LIMIT 1) AS last_round_id,
                       (SELECT r.round_number FROM rounds r
                         JOIN session_results sr ON sr.round_id = r.id
                        WHERE r.division_id = d.id AND sr.status = 'ACTIVE'
                        ORDER BY r.round_number DESC LIMIT 1) AS last_round_number,
                       (SELECT r.track_name FROM rounds r
                         JOIN session_results sr ON sr.round_id = r.id
                        WHERE r.division_id = d.id AND sr.status = 'ACTIVE'
                        ORDER BY r.round_number DESC LIMIT 1) AS last_track_name
                FROM divisions d
                LEFT JOIN division_results_config drc ON drc.division_id = d.id
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
            driver_snaps = await driver_standings_for_display(
                db_path, division_id, round_id, guild, bot
            )
            team_snaps = await standings_service.compute_team_standings(
                db_path, division_id, round_id
            )
        except Exception as exc:  # noqa: BLE001 — one division never stops the next
            log.exception("final classification: %s could not be resolved", division_name)
            problems.append(f"{division_name}: {exc}")
            continue

        channel_id = row["standings_channel_id"]
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if channel is not None and driver_snaps:
            try:
                await post_standings(
                    db_path,
                    division_id,
                    round_id,
                    row["last_round_number"],
                    row["last_track_name"] or "",
                    channel,
                    driver_snaps,
                    team_snaps,
                    guild,
                    await _get_show_reserves(db_path, division_id),
                    "",
                    bot=bot,
                    occasion=ClassificationOccasion.SEASON_FINAL,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("final classification: %s standings failed", division_name)
                problems.append(f"{division_name} standings: {exc}")

        try:
            await post_attendance_sheet(
                bot,
                guild,
                db_path,
                round_id,
                division_id,
                occasion=ClassificationOccasion.SEASON_FINAL,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("final classification: %s attendance failed", division_name)
            problems.append(f"{division_name} attendance: {exc}")

    return problems
