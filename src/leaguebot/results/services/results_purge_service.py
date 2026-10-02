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
in the same channel stays: it is the attendance module's, and recorded nowhere.

**One case is left standing, knowingly** (accepted 2026-09-21). An amendment still open past its
report stage has rewritten the amended sessions' verdict records without ids, and holds the
originals' ids only in ``round_amend_channels.superseded_announcements`` until its final stage.
Nothing here reads that column, and :func:`erase_season_on` then deletes the row, so those
announcements stay in the channel for the league's managers to delete by hand. It takes a disable
inside an amendment's half-hour window to reach.

**The stewarding module will have to face the verdicts too.** It is to announce and record verdicts
of its own — reports, appeals and investigations alike
(``docs/wip-specs/steward_module_specification.md``) — and disabling this module disables that one
along with it (STW-MOD-009). What is taken down here is only what ``penalty_records`` and
``appeal_records`` hold, so a verdict stewarding records anywhere else would be left on display,
exactly as every verdict was before #189. Whether its verdicts go with the season's results, and
what becomes of the announcement of a ban that itself survives the module being disabled
(STW-MOD-006), are that module's to settle when it is built; neither is decided here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import sole_row
from leaguebot.core.models.change import GuildUnavailable, StepFailedOnDiscord
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild

log = logging.getLogger(__name__)

#: The kinds of take-down item that are messages, and the kinds that are channels.
MESSAGE_KINDS = frozenset({"results", "standings", "verdict", "banner"})
CHANNEL_KINDS = frozenset({"submission", "amendment"})


@dataclass(frozen=True)
class SeasonErase:
    """What `erase_season_on` did, and what is left to take down in Discord.

    The counts are of what was erased from the database and of what *is to be removed* from
    Discord: the messages and verdicts and the channels are not yet gone. *items* are the
    take-down items, one for each posting, verdict, banner and open channel, as plain data so that
    each can be saved in a step of its own (see `erase_season_on`).
    """

    rounds: int = 0
    sessions: int = 0
    standings: int = 0
    messages: int = 0
    verdicts: int = 0
    submission_channels: int = 0
    amend_channels: int = 0
    items: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def message_link(server_id: int | str, channel_id: int | str, message_id: int | str) -> str:
    """A link that opens *message_id* where it stands, for a manager to delete it by hand."""
    return f"https://discord.com/channels/{server_id}/{channel_id}/{message_id}"


def _posting_ids(anchor: int, recorded: list[int] | None) -> list[int]:
    """Every message a posting occupies: what it recorded, and always its anchor.

    The same rule `_delete_posting` applies, so that what a take-down item names is what it tries.
    """
    ids = list(recorded or [anchor])
    if anchor not in ids:
        ids = [anchor, *ids]
    return ids


async def erase_season_on(db: aiosqlite.Connection) -> SeasonErase:
    """Erase the active season's results from the database on *db*, committing nothing, and say
    what is left to take down in Discord.

    **Everything it needs of Discord is read first, from the rows it then deletes.** Once they
    are gone nothing could find a message by, so each posting, verdict, banner and open channel
    becomes one take-down item carrying the server's id, the channel's, the message ids, the
    round and a label (decided with #439): the items travel in the steps of the change that
    turns results off, one step to an item, so a stop part-way through the removals is finished
    from what the first step saved, not from rows long since gone. The server's id is read here
    too, from `server_configs`, so that a message left standing can be linked even where the
    server is out of the bot's cache.

    Nothing here touches Discord, and nothing opens a connection: the caller's save holds the
    flag, the rows and the closing of the rounds together. Where no season is active, nothing is
    read further and nothing is touched.

    A banner heading an attendance sanction card is not an item: the card is the attendance
    module's, recorded nowhere, and its header stays over it (decided 2026-09-21). The banners'
    records are not forgotten here but by the change's closing step, for those taken down, so a
    banner left standing keeps its record.
    """
    from leaguebot.results.services.results_post_service import (
        _STANDINGS_ID_COLUMNS,
        _STANDINGS_IDS_COLUMNS,
        STANDINGS_CONSTRUCTORS,
        STANDINGS_DRIVERS,
        _parse_ids,
    )
    from leaguebot.results.services.verdict_announcement_service import (
        _banners_heading_sanctions_on,
        _banners_of_on,
        _parse_chunk_ids,
    )
    from leaguebot.results.services.verdict_records import VERDICT_TABLES, select_verdicts

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
        return SeasonErase()

    cursor = await db.execute(
        "SELECT server_id FROM server_configs WHERE server_id IS NOT NULL LIMIT 1"
    )
    server_row = await cursor.fetchone()
    server_id = None if server_row is None else int(server_row["server_id"])

    items: list[dict[str, Any]] = []

    def item(kind: str, label: str, channel_id: Any, anchor: int | None,
             message_ids: list[int], round_id: int) -> None:
        items.append({
            "kind": kind, "label": label, "server_id": server_id, "channel_id": int(channel_id),
            "anchor": anchor, "message_ids": message_ids, "round_id": round_id,
        })

    for row in rounds:
        round_id = row["round_id"]

        if row["results_channel_id"]:
            cursor = await db.execute(
                "SELECT results_message_id, results_message_ids FROM session_results "
                "WHERE round_id = ? AND results_message_id IS NOT NULL ORDER BY id",
                (round_id,),
            )
            for posting in await cursor.fetchall():
                anchor = int(posting["results_message_id"])
                item("results", "results message", row["results_channel_id"], anchor,
                     _posting_ids(anchor, _parse_ids(posting["results_message_ids"])), round_id)

        if row["standings_channel_id"]:
            for championship in (STANDINGS_DRIVERS, STANDINGS_CONSTRUCTORS):
                anchor_column = _STANDINGS_ID_COLUMNS[championship]
                list_column = _STANDINGS_IDS_COLUMNS[championship]
                cursor = await db.execute(
                    f"""
                    SELECT {anchor_column} AS anchor, {list_column} AS message_ids
                    FROM driver_standings_snapshots
                    WHERE division_id = ? AND round_id = ? AND {anchor_column} IS NOT NULL
                    ORDER BY standing_position ASC
                    LIMIT 1
                    """,  # noqa: S608 — columns come from constant maps
                    (row["division_id"], round_id),
                )
                found = await cursor.fetchone()
                if found is None or not found["anchor"]:
                    continue
                anchor = int(found["anchor"])
                item("standings", "standings message", row["standings_channel_id"], anchor,
                     _posting_ids(anchor, _parse_ids(found["message_ids"])), round_id)

        # One announcement per anchor. No two records share one today, but deleting a message
        # twice would count it twice and log a failure for the second attempt.
        taken: set[int] = set()
        for table in VERDICT_TABLES:
            for verdict in await select_verdicts(
                db, table,
                "v.announcement_message_id AS anchor, "
                "v.announcement_message_ids AS chunks, "
                "v.announcement_channel_id AS channel_id",
                round_id=round_id,
                where=" AND v.announcement_message_id IS NOT NULL",
            ):
                try:
                    anchor = int(verdict["anchor"])
                except (TypeError, ValueError):
                    continue
                if anchor in taken or not verdict["channel_id"]:
                    continue
                taken.add(anchor)
                item("verdict", "verdict", verdict["channel_id"], anchor,
                     _posting_ids(anchor, _parse_chunk_ids(verdict["chunks"])), round_id)

        banners = await _banners_of_on(db, round_id)
        kept = await _banners_heading_sanctions_on(db, [message_id for _, message_id in banners])
        for channel_id, message_id in banners:
            if message_id in kept or not channel_id:
                continue
            item("banner", "verdict banner", channel_id, message_id, [message_id], round_id)

        cursor = await db.execute(
            "SELECT channel_id FROM round_submission_channels WHERE round_id = ? AND closed = 0",
            (round_id,),
        )
        for opened in await cursor.fetchall():
            item("submission", "submission channel", opened["channel_id"], None, [], round_id)
        cursor = await db.execute(
            "SELECT channel_id FROM round_amend_channels WHERE round_id = ?", (round_id,)
        )
        for opened in await cursor.fetchall():
            item("amendment", "amendment channel", opened["channel_id"], None, [], round_id)

    round_ids = [row["round_id"] for row in rounds]
    # **Forgotten with the season** (#345): an amendment's snapshot names a `session_results` row
    # deleted below, which the sweep would then fail to revert and retry every five minutes for
    # ever. Nothing is reverted first: the classification the snapshot holds is being destroyed
    # with the rest of the season, so putting it back would be work undone a moment later.
    placeholders = ", ".join("?" for _ in round_ids)
    await db.execute(
        f"DELETE FROM round_amend_channels WHERE round_id IN ({placeholders})",  # noqa: S608
        round_ids,
    )
    sessions, standings = await _delete_rows(db, round_ids)
    count = lambda *kinds: sum(1 for entry in items if entry["kind"] in kinds)  # noqa: E731
    return SeasonErase(
        rounds=len(rounds),
        sessions=sessions,
        standings=standings,
        messages=count("results", "standings"),
        verdicts=count("verdict"),
        submission_channels=count("submission"),
        amend_channels=count("amendment"),
        items=tuple(items),
    )


async def take_down(bot: LeagueBot, item: dict[str, Any]) -> dict[str, Any]:
    """Take one item of an erased season down in Discord, and say what became of it.

    Returns ``{"removed": True}``, or ``{"removed": True, "gone": True}`` where its channel is
    gone: a channel deleted since holds nothing the bot could remove, and the item is passed over,
    not counted. Raises `GuildUnavailable` where the league's server is not in the cache, and
    `StepFailedOnDiscord` where anything is left standing: for a message, with ``left``, the ids
    still posted, which the change names to the league with their links; for a channel, with none.

    **Messages go through `_delete_posting`, channels through the channel closers**, the routes
    an amendment's replay and a finished submission take, so none can disagree about what a
    posting's messages are. No new direct post is made. A message already gone is not left.
    """
    from leaguebot.results.services.result_submission_service import (
        _close_amend_channel_record,
        close_submission_channel,
    )
    from leaguebot.results.services.results_post_service import _delete_posting

    guild = await league_guild(bot)
    if guild is None:
        raise GuildUnavailable("the league's server is not in the cache")
    channel_id = int(item["channel_id"])
    channel = as_text_channel(guild.get_channel(channel_id))
    if channel is None:
        log.warning(
            "take_down: the %s of round %s is in a channel no longer reachable (%s); leaving it",
            item["label"], item["round_id"], channel_id,
        )
        return {"removed": True, "gone": True}
    kind = item["kind"]
    if kind in MESSAGE_KINDS:
        failures: list[discord.HTTPException] = []
        left = await _delete_posting(
            channel, item["anchor"], item["message_ids"], label=item["label"],
            failures=failures,
        )
        if left:
            raise StepFailedOnDiscord(
                f"{len(left)} message(s) could not be removed", result={"left": left}
            ) from (failures[0] if failures else None)
        return {"removed": True}
    if kind == "submission":
        gone = await close_submission_channel(channel_id, item["round_id"], guild, bot.db_path)
    else:
        gone = await _close_amend_channel_record(
            bot.db_path, item["round_id"], channel_id, channel, reason="Results module disabled"
        )
    if not gone:
        raise StepFailedOnDiscord(f"channel {channel_id} could not be deleted")
    return {"removed": True}


async def _delete_rows(db: aiosqlite.Connection, round_ids: list[int]) -> tuple[int, int]:
    """Delete the season's result rows in foreign-key order on *db*, committing nothing.
    Returns (sessions, standings).

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

    return sessions, standings
