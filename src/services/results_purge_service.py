"""Erasing a season's results when the results & standings module is switched off.

Switching the module off part-way through a season destroys that season's results entire —
every classification recorded, every standing computed, and every results and standings
message already posted (decided 2026-09-14, issue #167). It is not a tidy-up: it is what
"disabled" means here.

The rule it serves is the core one, that a disabled module produces and holds nothing
(``core_specification.md``, "A disabled module shall produce nothing"). Results left sitting
in a channel are the module's output still on display while the module is off, and standings
left in the database are its work waiting to be found already done the next time it is
enabled. So the league is warned, confirms, and then the season's results are gone.

What survives is the module's *configuration*: the points configurations, the season's own
copy of them, and each division's results, standings and verdicts channels. Those are settings
rather than output, and the bot has always promised they survive a disable.

One thing cannot be undone. Penalty and appeal verdicts already announced stay where they were
posted: ``penalty_records`` and ``appeal_records`` record the channel they went to but never
the message id, so there is nothing to delete them by. The confirmation says so rather than
pretending otherwise.
"""
from __future__ import annotations

import logging

import discord

from db.database import get_connection
from utils.league_server import league_guild

log = logging.getLogger(__name__)


async def purge_season_results(db_path: str, bot) -> dict:
    """Delete every result of the active season, from Discord and from the database.

    Discord first, while the message ids are still stored: once the rows are gone there is
    nothing left to find the messages by.

    Returns a report — ``rounds``, ``sessions``, ``standings``, ``messages`` and
    ``submission_channels`` — for the reply to the league and the line in the log channel.
    Where no season is active every count is zero and nothing is touched.
    """
    report = {
        "rounds": 0,
        "sessions": 0,
        "standings": 0,
        "messages": 0,
        "submission_channels": 0,
    }

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id            AS round_id,
                   d.id            AS division_id,
                   drc.results_channel_id,
                   drc.standings_channel_id
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons   s ON s.id = d.season_id
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            WHERE s.status    = 'ACTIVE'
            ORDER BY r.id
            """,
        )
        rounds = [dict(row) for row in await cursor.fetchall()]

    if not rounds:
        return report

    report["rounds"] = len(rounds)
    guild = (await league_guild(bot)) if bot is not None else None
    if guild is None:
        # The rows still go. A guild out of cache is a bot that cannot reach the messages, not
        # a reason to leave the season's results half-erased in the database.
        log.warning(
            "purge_season_results: the league's server is not in the cache — deleting rows but no messages",
        )

    if guild is not None:
        report["messages"] = await _delete_posted_results(db_path, rounds, guild)
        report["submission_channels"] = await _close_open_submissions(db_path, rounds, guild)

    report["sessions"], report["standings"] = await _delete_rows(
        db_path, [row["round_id"] for row in rounds]
    )
    log.info(
        "purge_season_results: %s rounds, %s sessions, %s standings rows, "
        "%s messages, %s submission channels",
        report["rounds"],
        report["sessions"],
        report["standings"],
        report["messages"],
        report["submission_channels"],
    )
    return report


async def _delete_posted_results(db_path: str, rounds: list[dict], guild) -> int:
    """Unpost every results and standings message of the season, and count them.

    The deletion helpers come from ``results_post_service`` rather than being written again
    here. A posted table longer than Discord's limit is split across several messages and only
    the first id is stored, so deleting by the stored id alone would leave the continuations
    behind — ``_delete_with_continuations`` is what knows that, and ``_clear_standings_messages``
    is what knows that the image flow posts two championships where the textual flow posts one.
    """
    from services.results_post_service import (
        _clear_standings_messages,
        _delete_with_continuations,
    )

    deleted = 0
    for row in rounds:
        round_id = row["round_id"]

        results_channel = (
            guild.get_channel(row["results_channel_id"])
            if row["results_channel_id"]
            else None
        )
        if results_channel is not None:
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT results_message_id FROM session_results "
                    "WHERE round_id = ? AND results_message_id IS NOT NULL ORDER BY id",
                    (round_id,),
                )
                message_ids = [r[0] for r in await cursor.fetchall()]
            for message_id in message_ids:
                await _delete_with_continuations(
                    results_channel, message_id, label="results message"
                )
                deleted += 1

        standings_channel = (
            guild.get_channel(row["standings_channel_id"])
            if row["standings_channel_id"]
            else None
        )
        # Counted before the call, which clears the ids as it goes.
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT DISTINCT standings_message_id FROM driver_standings_snapshots"
                "   WHERE round_id = ? AND standings_message_id IS NOT NULL"
                "  UNION"
                "  SELECT DISTINCT constructor_standings_message_id"
                "    FROM driver_standings_snapshots"
                "   WHERE round_id = ? AND constructor_standings_message_id IS NOT NULL"
                ")",
                (round_id, round_id),
            )
            standings_messages = (await cursor.fetchone())[0]
        if standings_messages:
            await _clear_standings_messages(
                db_path, row["division_id"], round_id, standings_channel
            )
            if standings_channel is not None:
                deleted += standings_messages

    return deleted


async def _close_open_submissions(db_path: str, rounds: list[dict], guild) -> int:
    """Delete any submission channel still open, so no wizard outlives the module.

    A round part-way through its submission has a live channel with a view in it. Left
    standing, the league would go on entering results into a module that is switched off.
    """
    from services.result_submission_service import close_submission_channel

    closed = 0
    for row in rounds:
        round_id = row["round_id"]
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT channel_id FROM round_submission_channels "
                "WHERE round_id = ? AND closed = 0",
                (round_id,),
            )
            open_row = await cursor.fetchone()
        if open_row is None:
            continue
        try:
            await close_submission_channel(open_row[0], round_id, guild, db_path)
        except discord.HTTPException as exc:
            log.warning(
                "_close_open_submissions: could not close channel %s for round %s: %s",
                open_row[0], round_id, exc,
            )
        closed += 1
    return closed


async def _delete_rows(db_path: str, round_ids: list[int]) -> tuple[int, int]:
    """Delete the season's result rows in foreign-key order. Returns (sessions, standings).

    ``penalty_records`` and ``appeal_records`` go first. Each points at a driver's row in
    ``race_session_results`` or ``qualifying_session_results`` — migration 036 moved them off
    the old ``driver_session_results`` and onto those two — and neither reference carries
    ``ON DELETE CASCADE``. ``PRAGMA foreign_keys`` is ON, so deleting a session's results while
    a verdict still points into them fails the whole transaction. The per-format tables
    themselves cascade from ``session_results`` and need no statement of their own.
    """
    placeholders = ", ".join("?" for _ in round_ids)
    race_scope = f"""
        SELECT rsr.id FROM race_session_results rsr
        JOIN session_results sr ON sr.id = rsr.session_result_id
        WHERE sr.round_id IN ({placeholders})
    """
    qual_scope = f"""
        SELECT qsr.id FROM qualifying_session_results qsr
        JOIN session_results sr ON sr.id = qsr.session_result_id
        WHERE sr.round_id IN ({placeholders})
    """

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT COUNT(*) FROM session_results WHERE round_id IN ({placeholders})",
            round_ids,
        )
        sessions = (await cursor.fetchone())[0]
        cursor = await db.execute(
            f"""
            SELECT (SELECT COUNT(*) FROM driver_standings_snapshots
                     WHERE round_id IN ({placeholders}))
                 + (SELECT COUNT(*) FROM team_standings_snapshots
                     WHERE round_id IN ({placeholders}))
            """,
            round_ids + round_ids,
        )
        standings = (await cursor.fetchone())[0]

        for table in ("penalty_records", "appeal_records"):
            await db.execute(
                f"DELETE FROM {table} WHERE race_result_id IN ({race_scope}) "  # noqa: S608
                f"OR qual_result_id IN ({qual_scope})",
                round_ids + round_ids,
            )
        for table in (
            "session_results",
            "driver_standings_snapshots",
            "team_standings_snapshots",
            "round_submission_channels",
        ):
            await db.execute(
                f"DELETE FROM {table} WHERE round_id IN ({placeholders})",  # noqa: S608
                round_ids,
            )
        await db.commit()

    return sessions, standings
