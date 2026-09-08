"""season_classification_service — the classifications a season is bracketed by.

A division's standings and its attendance record are posted after every round. This module
adds the two occasions that bracket those: the **opening** classification, posted once when
the season is approved, and the **final** one, posted once when it completes. Both are the
same two sheets a league already reads every round, differing only in the phrase they carry —
see :class:`models.classification_occasion.ClassificationOccasion`, which is where that phrase
and the behaviour around it live.

**Neither posting carries message text.** The phrase naming the occasion is drawn on the
sheet, so a heading above it would only say it twice; the lineup and calendar graphics posted
at approval already go out bare for the same reason. Where a graphic cannot be drawn at all,
the ordinary textual fallback stands in and *is* headed by the phrase, a bare table of names
and numbers otherwise saying nothing about what it is a table of (XIV.7 — a graphic is an
alternative beside the text, never an exception to it).

**One division never stops another.** Every problem is caught, collected and returned for the
caller to report to the logging channel, never to a channel a driver reads (XIV.4). Season
approval and season completion are both far too consequential to be failed by a picture.
"""

from __future__ import annotations

import logging

from models.classification_occasion import ClassificationOccasion

log = logging.getLogger(__name__)


async def post_opening_classifications(
    bot, guild, db_path: str, divisions, div_rounds
) -> list[str]:
    """Post every division's opening standings and attendance sheet.

    *div_rounds* maps a division id to its rounds, as ``/season review`` already assembled
    them. The division's **first** round is what the sheets are drawn against: not because
    they stand after it — they stand after nothing — but because the grid of rounds down the
    side of both sheets is read from the season's calendar, and because the heading context
    every posting resolves is keyed by a round. Every cell in that grid is empty, which is
    exactly what an opening sheet should show.
    """
    from services import standings_service
    from services.attendance_service import post_attendance_sheet
    from services.image_results_post import _driver_names
    from services.results_post_service import _get_show_reserves, post_standings

    problems: list[str] = []
    if bot is None or guild is None:
        return problems

    for division in divisions:
        rounds = div_rounds.get(division.id) or []
        if not rounds:
            # Approval refuses a division with no rounds, so this cannot normally happen.
            continue
        opener = rounds[0]

        try:
            # Twice, deliberately. The order is taken on the name the sheet will actually
            # draw, and the names are resolved from Discord by user id — so the roster has
            # to be known before it can be ordered. The first pass is read only for who is
            # in it; the second is the one that counts.
            roster = await standings_service.opening_driver_standings(db_path, division.id)
            names = await _driver_names(
                bot, guild, [snapshot.driver_user_id for snapshot in roster]
            )
            driver_snaps = await standings_service.opening_driver_standings(
                db_path, division.id, names
            )
            team_snaps = await standings_service.opening_team_standings(
                db_path, division.id
            )
        except Exception as exc:  # noqa: BLE001 — one division never stops the next
            log.exception("opening classification: %s could not be resolved", division.name)
            problems.append(f"{division.name}: {exc}")
            continue

        channel_id = getattr(division, "standings_channel_id", None)
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if channel is not None and driver_snaps:
            try:
                await post_standings(
                    db_path,
                    division.id,
                    opener.id,
                    opener.round_number,
                    getattr(opener, "track_name", None) or "",
                    channel,
                    driver_snaps,
                    team_snaps,
                    guild,
                    await _get_show_reserves(db_path, division.id),
                    "",
                    bot=bot,
                    occasion=ClassificationOccasion.SEASON_OPENING,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "opening classification: %s standings failed", division.name
                )
                problems.append(f"{division.name} standings: {exc}")

        try:
            await post_attendance_sheet(
                bot,
                guild,
                db_path,
                opener.id,
                division.id,
                occasion=ClassificationOccasion.SEASON_OPENING,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("opening classification: %s attendance failed", division.name)
            problems.append(f"{division.name} attendance: {exc}")

    return problems


async def post_final_classifications(bot, guild, db_path: str, season_id: int) -> list[str]:
    """Post every division's final standings and attendance sheet.

    Drawn against the division's **last round that has results**, whose classification the
    final sheet *is* — the season's last word, restated under its own heading rather than
    recomputed into something the round's own posting would disagree with. A division that
    ran no round at all is skipped: there is no classification to publish.
    """
    from db.database import get_connection
    from services import standings_service
    from services.attendance_service import post_attendance_sheet
    from services.results_post_service import _get_show_reserves, post_standings

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
            driver_snaps = await standings_service.compute_driver_standings(
                db_path, division_id, round_id
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
