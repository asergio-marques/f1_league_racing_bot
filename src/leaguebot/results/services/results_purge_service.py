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

The verdicts go with the results (decided 2026-09-21, issue #189). Every penalty and appeal
verdict already announced is taken down from the verdicts channel, by the message ids recorded
when it was posted. They were once left standing, and the league told so, only because no id was
recorded and nothing could find them — a limitation, never a rule. An attendance sanction card
in the same channel stays: it is the attendance module's, and recorded nowhere. The stewarding
module will meet the same question for verdicts of its own; see :func:`_delete_posted_verdicts`.
"""
from __future__ import annotations

import logging

import discord

from leaguebot.core.db.database import get_connection, sole_row
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild

log = logging.getLogger(__name__)


async def purge_season_results(db_path: str, bot: LeagueBot) -> dict:
    """Delete every result of the active season, from Discord and from the database.

    Discord first, while the message ids are still stored: once the rows are gone there is
    nothing left to find the messages by.

    Returns a report — ``rounds``, ``sessions``, ``standings``, ``messages``, ``verdicts`` and
    ``submission_channels`` — for the reply to the league and the line in the log channel.
    Where no season is active every count is zero and nothing is touched.

    A message counts as removed only where every part of it went. ``left_standing`` links each
    message the bot tried and failed to remove, so the league can delete it by hand — its record
    is gone once this returns, and nothing else could find it again (decided 2026-09-21, #189).
    """
    left_standing: list[str] = []
    report = {
        "rounds": 0,
        "sessions": 0,
        "standings": 0,
        "messages": 0,
        "verdicts": 0,
        "submission_channels": 0,
        "amend_channels": 0,
        "left_standing": left_standing,
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
        report["messages"], left = await _delete_posted_results(db_path, rounds, guild)
        left_standing.extend(left)
        report["verdicts"], left = await _delete_posted_verdicts(db_path, rounds, guild)
        left_standing.extend(left)
        report["submission_channels"] = await _close_open_submissions(db_path, rounds, guild)

    # **Not under the guild** (#345). Deleting the channel needs one; forgetting the amendment
    # does not, and a row that survived the purge would name a `session_results` row deleted
    # below — which the sweep then fails to revert, and retries every five minutes for ever.
    report["amend_channels"] = await _close_open_amendments(db_path, rounds, guild)

    report["sessions"], report["standings"] = await _delete_rows(
        db_path, [row["round_id"] for row in rounds]
    )
    log.info(
        "purge_season_results: %s rounds, %s sessions, %s standings rows, "
        "%s messages, %s verdicts, %s submission channels",
        report["rounds"],
        report["sessions"],
        report["standings"],
        report["messages"],
        report["verdicts"],
        report["submission_channels"],
    )
    return report


async def _delete_posted_results(
    db_path: str, rounds: list[dict], guild
) -> tuple[int, list[str]]:
    """Unpost every results and standings message of the season.

    Returns how many went, and a link to each message the bot could not remove — a posting
    counts only where every part of it went.

    The deletion helpers come from ``results_post_service`` rather than being written again
    here. A posted table longer than Discord's limit is split across several messages and every
    one of their ids is stored — ``_delete_posting`` is what knows that, and
    ``_clear_standings_messages`` is what knows that the image flow posts two championships
    where the textual flow posts one.
    """
    from leaguebot.results.services.results_post_service import (
        _clear_standings_messages,
        _delete_posting,
        _parse_ids,
    )

    deleted = 0
    left_standing: list[str] = []
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
                    "SELECT results_message_id, results_message_ids FROM session_results "
                    "WHERE round_id = ? AND results_message_id IS NOT NULL ORDER BY id",
                    (round_id,),
                )
                postings = [(r[0], r[1]) for r in await cursor.fetchall()]
            for message_id, chunk_ids in postings:
                # By what the posting recorded, not by what follows it (#345). The adjacency walk
                # cannot tell this posting's continuation from the next posting down, so purging a
                # season could destroy a message it was not asked to touch.
                left = await _delete_posting(
                    results_channel, message_id, _parse_ids(chunk_ids),
                    label="results message",
                )
                if left:
                    left_standing.extend(
                        _message_link(guild, results_channel, m) for m in left
                    )
                else:
                    deleted += 1

        standings_channel = (
            guild.get_channel(row["standings_channel_id"])
            if row["standings_channel_id"]
            else None
        )
        removed, left = await _clear_standings_messages(
            db_path, row["division_id"], round_id, standings_channel
        )
        deleted += removed
        left_standing.extend(_message_link(guild, standings_channel, m) for m in left)

    return deleted, left_standing


def _message_link(guild, channel, message_id: int) -> str:
    """A link that opens *message_id* where it stands, for a manager to delete it by hand."""
    return f"https://discord.com/channels/{guild.id}/{channel.id}/{message_id}"


async def _delete_posted_verdicts(
    db_path: str, rounds: list[dict], guild
) -> tuple[int, list[str]]:
    """Take down every penalty and appeal verdict the season announced.

    Returns how many went, and a link to each message the bot could not remove — a verdict
    counts only where every part of it went.

    Each is found by the channel and message ids recorded when it was posted (#189), and
    deleted through ``_delete_posting`` by every chunk it recorded — the same route an
    amendment's replay takes, so the two cannot disagree about what a verdict's messages are.
    A verdict whose channel is gone is logged and passed over: its record goes with the rest of
    the season all the same, and a channel the bot cannot reach holds nothing it could remove.

    The banner heading each round's run goes with it, and its record with it — unless the banner
    also heads an attendance sanction card. That card is the attendance module's, recorded
    nowhere and left where it is, so its header stays over it: the rule an amendment's replay
    keeps (decided 2026-09-21). Banners are not counted, the league being told of its verdicts;
    one the bot could not remove is linked all the same, and keeps its record.

    **One case is left standing, knowingly** (accepted 2026-09-21). An amendment still open past
    its report stage has rewritten the amended sessions' verdict records without ids, and holds
    the originals' ids only in ``round_amend_channels.superseded_announcements`` until its final
    stage. Nothing here reads that column, and ``_close_open_amendments`` then forgets it, so
    those announcements stay in the channel for the league's managers to delete by hand. It
    takes a disable inside an amendment's half-hour window to reach.

    **The stewarding module will have to face this too.** It is to announce and record verdicts
    of its own — reports, appeals and investigations alike
    (``docs/wip-specs/steward_module_specification.md``) — and disabling this module disables
    that one along with it (STW-MOD-009). What is taken down here is only what
    ``penalty_records`` and ``appeal_records`` hold, so a verdict stewarding records anywhere
    else would be left on display by this function, exactly as every verdict was before #189.
    Whether its verdicts go with the season's results, and what becomes of the announcement of
    a ban that itself survives the module being disabled (STW-MOD-006), are that module's to
    settle when it is built; neither is decided here.
    """
    from leaguebot.results.services.results_post_service import _delete_posting
    from leaguebot.results.services.verdict_announcement_service import (
        _banners_heading_sanctions,
        _banners_of,
        _forget_banners,
        _parse_chunk_ids,
    )
    from leaguebot.results.services.verdict_records import VERDICT_TABLES, select_verdicts

    deleted = 0
    left_standing: list[str] = []
    banners_taken_down: list[int] = []
    for row in rounds:
        round_id = row["round_id"]
        async with get_connection(db_path) as db:
            verdicts = [
                verdict
                for table in VERDICT_TABLES
                for verdict in await select_verdicts(
                    db, table,
                    "v.announcement_message_id AS anchor, "
                    "v.announcement_message_ids AS chunks, "
                    "v.announcement_channel_id AS channel_id",
                    round_id=round_id,
                    where=" AND v.announcement_message_id IS NOT NULL",
                )
            ]

        taken: set[int] = set()
        for verdict in verdicts:
            try:
                anchor = int(verdict["anchor"])
            except (TypeError, ValueError):
                continue
            # One announcement per anchor. No two records share one today, but deleting a
            # message twice would count it twice and log a failure for the second attempt.
            if anchor in taken:
                continue
            taken.add(anchor)
            channel = (
                guild.get_channel(int(verdict["channel_id"]))
                if verdict["channel_id"]
                else None
            )
            if channel is None:
                log.warning(
                    "_delete_posted_verdicts: verdict %s of round %s is in a channel no longer "
                    "reachable (%s); leaving it",
                    anchor, round_id, verdict["channel_id"],
                )
                continue
            left = await _delete_posting(
                channel, anchor, _parse_chunk_ids(verdict["chunks"]), label="verdict"
            )
            if left:
                left_standing.extend(_message_link(guild, channel, m) for m in left)
            else:
                deleted += 1

        banners = await _banners_of(db_path, round_id)
        kept = await _banners_heading_sanctions(
            db_path, [message_id for _, message_id in banners]
        )
        for channel_id, message_id in banners:
            if message_id in kept:
                continue
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            if channel is None:
                continue
            left = await _delete_posting(
                channel, message_id, [message_id], label="verdict banner"
            )
            if left:
                left_standing.extend(_message_link(guild, channel, m) for m in left)
            else:
                banners_taken_down.append(message_id)

    await _forget_banners(db_path, banners_taken_down)
    return deleted, left_standing


async def _close_open_submissions(db_path: str, rounds: list[dict], guild) -> int:
    """Delete any submission channel still open, so no wizard outlives the module.

    A round part-way through its submission has a live channel with a view in it. Left
    standing, the league would go on entering results into a module that is switched off.
    """
    from leaguebot.results.services.result_submission_service import close_submission_channel

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


async def _close_open_amendments(db_path: str, rounds: list[dict], guild) -> int:
    """Delete any amendment channel still open, and forget the round it was putting back.

    An amendment holds a snapshot of the round as it stood, and a deadline by which the sweep
    reverts it. Both outlive a purge that took no notice of them: the snapshot then names a
    ``session_results`` row the purge has deleted, so every sweep from then on fails the foreign
    key and retries five minutes later, for ever — and the channel it would have deleted stays
    open on a module that is off (#345).

    Nothing is reverted first. The classification the snapshot holds is being destroyed with the
    rest of the season, so putting it back would be work undone a moment later.
    """
    closed = 0
    for row in rounds:
        round_id = row["round_id"]
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT channel_id FROM round_amend_channels WHERE round_id = ?",
                (round_id,),
            )
            open_rows = [r[0] for r in await cursor.fetchall()]
            if not open_rows:
                continue
            await db.execute(
                "DELETE FROM round_amend_channels WHERE round_id = ?", (round_id,)
            )
            await db.commit()
        for channel_id in open_rows:
            channel = guild.get_channel(channel_id) if guild is not None else None
            if channel is not None:
                try:
                    await channel.delete(reason="Results module disabled")
                except discord.HTTPException as exc:
                    log.warning(
                        "_close_open_amendments: could not delete channel %s for round %s: %s",
                        channel_id, round_id, exc,
                    )
            closed += 1
    return closed


async def _delete_rows(db_path: str, round_ids: list[int]) -> tuple[int, int]:
    """Delete the season's result rows in foreign-key order. Returns (sessions, standings).

    ``penalty_records`` and ``appeal_records`` go first. Each points at a driver's row in
    ``race_session_results`` or ``qualifying_session_results``, and neither reference carries
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
        sessions = (await sole_row(cursor))[0]
        cursor = await db.execute(
            f"""
            SELECT (SELECT COUNT(*) FROM driver_standings_snapshots
                     WHERE round_id IN ({placeholders}))
                 + (SELECT COUNT(*) FROM team_standings_snapshots
                     WHERE round_id IN ({placeholders}))
            """,
            round_ids + round_ids,
        )
        standings = (await sole_row(cursor))[0]

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
