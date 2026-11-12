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

Results' note of a round or a division called off goes with them (#455): its message is recorded
in ``called_off_notes`` when it is posted and read here, before the rows go, as a verdict's is. A
season's own note is not recorded, and so not taken down: only the live season is read here, and a
season called off is an archive the purge never reaches.

So is the opening classification posted to each division's standings channel when placements are
confirmed (decided 2026-11-12, #284): its ids are recorded in ``classification_posts``, read here
before the rows go, and the tables are taken down as a round's standings are, counted with them.
The final classification belongs to a completed season, which the purge never reads, so its ids are
recorded but not taken down.

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
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import sole_row
from leaguebot.core.models.change import GuildUnavailable, StepFailedOnDiscord
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.services.posting.notice_handler import delete_notice
from leaguebot.core.services.posting.standing_post_handler import delete_standing
from leaguebot.core.services.seasons.season_service import active_season_rounds_on
from leaguebot.core.services.settings.channel_registry_service import as_text_channel
from leaguebot.core.services.settings.config_service import league_server_id_on
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild

log = logging.getLogger(__name__)

#: The kinds of take-down item that are messages, and the kinds that are channels.
MESSAGE_KINDS = frozenset(
    {"results", "standings", "classification", "verdict", "banner", "called_off"}
)
CHANNEL_KINDS = frozenset({"submission", "amendment"})
#: Of the message kinds, the tables, which are standing posts; the rest are notices.
TABLE_KINDS = frozenset({"results", "standings", "classification"})


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
    too, through core's `league_server_id_on`, so that a message left standing can be linked
    even where the server is out of the bot's cache.

    Nothing here touches Discord, and nothing opens a connection: the caller's save holds the
    flag, the rows and the closing of the rounds together. Where no season is active, nothing is
    read further and nothing is touched.

    A banner heading an attendance sanction card is not an item: the card is the attendance
    module's, recorded nowhere, and its header stays over it (decided 2026-09-21). The banners'
    records are not forgotten here but by the change's closing step, for those taken down, so a
    banner left standing keeps its record.
    """
    from leaguebot.results.services.publishing.results_post_service import (
        _STANDINGS_ID_COLUMNS,
        _STANDINGS_IDS_COLUMNS,
        STANDINGS_CONSTRUCTORS,
        STANDINGS_DRIVERS,
        _parse_ids,
    )
    from leaguebot.results.services.review.verdict_announcement_service import (
        _banners_heading_sanctions_on,
        _banners_of_on,
        _parse_chunk_ids,
    )
    from leaguebot.results.services.review.verdict_records import VERDICT_TABLES, select_verdicts

    active_rounds = await active_season_rounds_on(db)
    if not active_rounds:
        return SeasonErase()

    channels: dict[int, Any] = {}
    for division_id in sorted({division for _, division in active_rounds}):
        cursor = await db.execute(
            "SELECT results_channel_id, standings_channel_id FROM division_results_config "
            "WHERE division_id = ?",
            (division_id,),
        )
        channels[division_id] = await cursor.fetchone()
    rounds: list[dict[str, Any]] = [
        {
            "round_id": round_id,
            "division_id": division_id,
            "results_channel_id": None if channels[division_id] is None
            else channels[division_id]["results_channel_id"],
            "standings_channel_id": None if channels[division_id] is None
            else channels[division_id]["standings_channel_id"],
        }
        for round_id, division_id in active_rounds
    ]

    server_id = await league_server_id_on(db)

    items: list[dict[str, Any]] = []

    def item(kind: str, label: str, channel_id: Any, anchor: int | None,
             message_ids: list[int], round_id: int | None) -> None:
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
                    FROM round_standings_posts
                    WHERE division_id = ? AND round_id = ? AND {anchor_column} IS NOT NULL
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

    # The opening classification of each division the live season's rounds name: the drivers' and
    # the teams' tables, one item each, as a round's standings are. Only the opening is read: a
    # final classification belongs to a completed season, which the purge never reaches.
    for division_id in sorted({int(row["division_id"]) for row in rounds}):
        standings_channel = channels[division_id]
        if standings_channel is None or not standings_channel["standings_channel_id"]:
            continue
        for championship in (STANDINGS_DRIVERS, STANDINGS_CONSTRUCTORS):
            anchor_column = _STANDINGS_ID_COLUMNS[championship]
            list_column = _STANDINGS_IDS_COLUMNS[championship]
            cursor = await db.execute(
                f"""
                SELECT {anchor_column} AS anchor, {list_column} AS message_ids
                FROM classification_posts
                WHERE division_id = ? AND occasion = ? AND {anchor_column} IS NOT NULL
                """,  # noqa: S608 — columns come from constant maps
                (division_id, ClassificationOccasion.SEASON_OPENING.value),
            )
            found = await cursor.fetchone()
            if found is None or not found["anchor"]:
                continue
            anchor = int(found["anchor"])
            item("classification", "opening classification",
                 standings_channel["standings_channel_id"], anchor,
                 _posting_ids(anchor, _parse_ids(found["message_ids"])), None)

    # A round's or a division's note of being called off: one item for each, the division's with
    # no round, read for the divisions the live season's rounds name. The rows go with the rest,
    # their messages carried by the items.
    divisions = sorted({int(row["division_id"]) for row in rounds})
    named = ", ".join("?" for _ in divisions)
    cursor = await db.execute(
        "SELECT round_id, channel_id, message_id FROM called_off_notes "  # noqa: S608
        f"WHERE division_id IN ({named}) ORDER BY id",
        divisions,
    )
    for note in await cursor.fetchall():
        anchor = int(note["message_id"])
        item("called_off", "called-off note", note["channel_id"], anchor, [anchor],
             None if note["round_id"] is None else int(note["round_id"]))
    await db.execute(
        f"DELETE FROM called_off_notes WHERE division_id IN ({named})",  # noqa: S608
        divisions,
    )

    await db.execute(
        f"DELETE FROM classification_posts WHERE division_id IN ({named})",  # noqa: S608
        divisions,
    )

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
        messages=count("results", "standings", "classification"),
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

    **Messages go through `_delete_posting`, channels through `delete_round_channel`**, the routes
    an amendment's replay and a finished submission take, so none can disagree about what a
    posting's messages are. The switch-off's save has already erased the rows that name the
    channels, so a channel is only deleted here. No new direct post is made. A message already gone is not left.
    """
    from leaguebot.results.services.publishing.results_post_service import _delete_posting, delete_round_channel

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
            delete=delete_standing if kind in TABLE_KINDS else delete_notice, failures=failures,
        )
        if left:
            raise StepFailedOnDiscord(
                f"{len(left)} message(s) could not be removed", result={"left": left}
            ) from (failures[0] if failures else None)
        return {"removed": True}
    try:
        await delete_round_channel(channel, reason="Results module disabled")
    except discord.NotFound:
        pass
    except discord.HTTPException as error:
        raise StepFailedOnDiscord(f"channel {channel_id} could not be deleted") from error
    return {"removed": True}


async def rounds_deleted_on(db: aiosqlite.Connection, round_ids: list[int]) -> None:
    """Delete results' rows of *round_ids* on *db*, committing nothing: the classifications of the
    rounds' sessions with every verdict on them, the standings taken after each round, the
    submission channels' records, an amendment's, a held resubmission's, the banners' records and
    the round's called-off note.

    Core hands it the round ids as it deletes the rounds, in the delete's own save, so that the
    module's rows go with the round or not at all (architecture.md, "The database"). The messages
    and channels are not taken down here: the rows are all it deletes.

    **Not `erase_season_on`'s erasure**, which keeps a verdict banner's record and a held
    resubmission with their rounds: here the rounds themselves are going, and nothing is left to
    hold them.
    """
    marks = ", ".join("?" for _ in round_ids)
    await db.execute(
        f"DELETE FROM round_amend_channels WHERE round_id IN ({marks})", round_ids  # noqa: S608
    )
    await db.execute(
        f"DELETE FROM resubmission_drafts WHERE round_id IN ({marks})", round_ids  # noqa: S608
    )
    await db.execute(
        f"DELETE FROM verdict_banner_messages WHERE round_id IN ({marks})", round_ids  # noqa: S608
    )
    await db.execute(
        f"DELETE FROM called_off_notes WHERE round_id IN ({marks})", round_ids  # noqa: S608
    )
    await _delete_rows(db, round_ids)


async def divisions_deleted_on(db: aiosqlite.Connection, division_ids: list[int]) -> None:
    """Delete results' rows of *division_ids* on *db*, committing nothing: each division's
    results, standings and verdicts channels, the note of the division called off and the postings
    of its opening and final classifications."""
    marks = ",".join("?" * len(division_ids))
    await db.execute(
        f"DELETE FROM called_off_notes WHERE division_id IN ({marks})",  # noqa: S608
        division_ids,
    )
    await db.execute(
        f"DELETE FROM classification_posts WHERE division_id IN ({marks})",  # noqa: S608
        division_ids,
    )
    await db.execute(
        f"DELETE FROM division_results_config WHERE division_id IN ({marks})",  # noqa: S608
        division_ids,
    )


async def season_deleted_on(db: aiosqlite.Connection, season_id: int) -> None:
    """Delete results' rows of *season_id* on *db*, committing nothing: the season's own copy of
    the points configuration, its amendment state and the modifications an amendment holds."""
    await db.execute("DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,))
    await db.execute("DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,))
    await db.execute("DELETE FROM season_amendment_state WHERE season_id = ?", (season_id,))
    await db.execute("DELETE FROM season_points_fl WHERE season_id = ?", (season_id,))
    await db.execute("DELETE FROM season_points_entries WHERE season_id = ?", (season_id,))
    await db.execute("DELETE FROM season_points_links WHERE season_id = ?", (season_id,))


async def profiles_deleted_on(db: aiosqlite.Connection, profile_ids: list[int]) -> None:
    """Let go of the drivers *profile_ids* in results' rows on *db*, committing nothing: their
    race and qualifying results and the standings that name them lose the profile, and keep
    naming the driver by their user id, as they always did.

    Core hands it the profile ids as it deletes the drivers, in the delete's own save, so that
    the rows let go of the profile with the drivers' deletion or not at all (architecture.md,
    "The database").
    """
    marks = ",".join("?" * len(profile_ids))
    await db.execute(
        "UPDATE race_session_results SET driver_profile_id = NULL "
        f"WHERE driver_profile_id IN ({marks})",  # noqa: S608
        profile_ids,
    )
    await db.execute(
        "UPDATE qualifying_session_results SET driver_profile_id = NULL "
        f"WHERE driver_profile_id IN ({marks})",  # noqa: S608
        profile_ids,
    )
    await db.execute(
        "UPDATE driver_standings_snapshots SET driver_profile_id = NULL "
        f"WHERE driver_profile_id IN ({marks})",  # noqa: S608
        profile_ids,
    )


async def profiles_merged_on(db: aiosqlite.Connection, kept_id: int, absorbed_id: int) -> None:
    """Move results' rows of the driver *absorbed_id* onto the driver *kept_id* on *db*,
    committing nothing: their race and qualifying results and the standings that name them.

    Core hands it both profile ids as it merges the drivers, inside the merge's one transaction,
    before the absorbed profile is deleted (architecture.md, "The database").
    """
    moved = (kept_id, absorbed_id)
    await db.execute(
        "UPDATE race_session_results SET driver_profile_id = ? WHERE driver_profile_id = ?",
        moved,
    )
    await db.execute(
        "UPDATE qualifying_session_results SET driver_profile_id = ? WHERE driver_profile_id = ?",
        moved,
    )
    await db.execute(
        "UPDATE driver_standings_snapshots SET driver_profile_id = ? WHERE driver_profile_id = ?",
        moved,
    )


async def divisions_raced_on(db: aiosqlite.Connection, accounts: Sequence[str]) -> set[int]:
    """The ids of the divisions in which any of *accounts* holds a race result, a qualifying
    result or a standing, read on *db*; none where *accounts* is empty.

    Signed up to core's `divisions_raced_read`, which `/driver reassign` asks on the connection
    its check and its save hold, handing the accounts the driver has held: core joins what it
    answers to the divisions the driver holds a seat in (architecture.md, "How modules and core
    fit together"). A division belongs to one season, so a division both sides of a merge raced in
    is one person in one season's standings twice.
    """
    if not accounts:
        return set()
    # Handed as text, as accounts are kept: the INTEGER column converts a numeral on comparing, and
    # an account that is no numeral matches nothing.
    held = list(accounts)
    marks = ",".join("?" for _ in held)
    cursor = await db.execute(
        f"""
        SELECT sr.division_id FROM race_session_results x
        JOIN session_results sr ON sr.id = x.session_result_id
        WHERE x.driver_user_id IN ({marks})
        UNION
        SELECT sr.division_id FROM qualifying_session_results x
        JOIN session_results sr ON sr.id = x.session_result_id
        WHERE x.driver_user_id IN ({marks})
        UNION
        SELECT division_id FROM driver_standings_snapshots
        WHERE driver_user_id IN ({marks})
        """,  # noqa: S608
        (*held, *held, *held),
    )
    return {int(row[0]) for row in await cursor.fetchall()}


async def bot_packed_on(db: aiosqlite.Connection) -> None:
    """Clear results' rows tied to the server the bot leaves, on *db*, committing nothing: the id
    of each submission channel's prompt and resubmit prompt, by which they are edited.

    Core hands it the pack's connection, so that results' rows go with the rest of the pack or
    not at all (architecture.md, "The database"). Only the ids go: a prompt already posted stays
    where it is, and every result stays with its round.
    """
    await db.execute(
        "UPDATE round_submission_channels "
        "SET prompt_message_id = NULL, resubmit_prompt_message_id = NULL"
    )


async def _delete_rows(db: aiosqlite.Connection, round_ids: list[int]) -> tuple[int, int]:
    """Delete the result rows of *round_ids* in foreign-key order on *db*, committing nothing.
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
        "round_standings_posts",
        "driver_standings_snapshots",
        "team_standings_snapshots",
        "round_submission_channels",
    ):
        await db.execute(
            f"DELETE FROM {table} WHERE round_id IN ({placeholders})",  # noqa: S608
            round_ids,
        )

    return sessions, standings
