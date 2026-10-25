"""`/results standings sync` on the change queue (#439, slice 6b): a member's change carried out on
the queue whole (`results.standings.sync`), acknowledged at once and the acknowledgement updated
with the outcome.

The league is `review_league`'s: division 11 (Pro) of season 1, being raced, its standings channel
701 holding round 3's standings as message 8801, results switched on, images off. Round 3's stored
standings snapshot holds Lewis alone on 25 points, while its Feature Race results give Lewis 25 and
Max 18. A manager runs the command, the queue is run, and the test reads the channels, the database
and what the manager was told. Each round's standings are recomputed when the job runs, as the
command does today (decision 16), posted afresh, then the message they replace deleted by a job of
its own; the sync's gates are checked again when it runs (core spec :125).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.support.change_queue import (
    attach_queue,
    change_rows,
    discard_job,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.review_league import (
    AMEND_CHANNEL,
    DIVISION_ID,
    LATER_ROUND_ID,
    LEWIS,
    LEWIS_PROFILE,
    MAX,
    MAX_PROFILE,
    NOW,
    OLD_STANDINGS,
    ROUND_ID,
    STANDINGS_CHANNEL,
    ReviewLeague,
    block_queue,
    review_league,
)
from tests.support.undecorate import undecorate

KIND = "results.standings.sync"
#: Round 4's standings, standing where a test posts them.
OLD_LATER_STANDINGS = 8811


async def _league(tmp_path: Any) -> ReviewLeague:
    """`review_league`'s league with round 3 final, results switched on, and the real season
    and module services the command and its check read; the queue built again over them."""
    from leaguebot.core.services.season_service import SeasonService

    league = await review_league(tmp_path, round_status="FINAL")
    await _write(league, "INSERT OR REPLACE INTO results_module_config (id, module_enabled) "
                         "VALUES (1, 1)")
    league.bot.season_service = SeasonService(league.db_path)
    league.bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    attach_queue(league.bot, league.db_path, now=NOW)
    return league


async def _write(league: ReviewLeague, sql: str, *args: Any) -> None:
    from leaguebot.core.db.database import get_connection

    async with get_connection(league.db_path) as db:
        await db.execute(sql, args)
        await db.commit()


async def _round_4_posted(league: ReviewLeague) -> None:
    """Round 4 at Spa holds a Feature Race too, Lewis winning it, its standings standing as
    message 8811 in the standings channel and recorded on its snapshot."""
    from leaguebot.core.db.database import get_connection

    async with get_connection(league.db_path) as db:
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard')",
            (LATER_ROUND_ID, DIVISION_ID),
        )
        for position, (profile, driver, points) in enumerate(
            ((LEWIS_PROFILE, LEWIS, 25), (MAX_PROFILE, MAX, 18)), start=1,
        ):
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position, outcome, base_time_ms, points_awarded, "
                "driver_profile_id) VALUES (?, ?, 3001, ?, 'CLASSIFIED', ?, ?, ?)",
                (session.lastrowid, driver, position, 3_600_000 + position * 1000, points,
                 profile),
            )
        await db.execute(
            "UPDATE driver_standings_snapshots SET standings_message_id = ?, "
            "standings_message_ids = ? WHERE round_id = ?",
            (OLD_LATER_STANDINGS, f"[{OLD_LATER_STANDINGS}]", LATER_ROUND_ID),
        )
        await db.commit()
    league.channel(STANDINGS_CHANNEL).seed(OLD_LATER_STANDINGS, "round 4 standings")


async def _sync(league: ReviewLeague, name: str = "Pro") -> Any:
    """Run `/results standings sync` for *name* as the manager Admin (4242); the queue is not
    run. Gives the interaction, its acknowledgement's message able to be edited."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot)
    interaction.guild = league.guild
    interaction.command = SimpleNamespace(qualified_name="results standings sync")
    interaction.followup.send.return_value = MagicMock(edit=AsyncMock())
    await undecorate(ResultsCog.standings_sync)(cog, interaction, name)
    return interaction


def _content(call: Any) -> str:
    return str(call.args[0]) if call.args else str(call.kwargs.get("content", ""))


def _acknowledged(interaction: Any) -> str:
    """What the deferred command was first answered with: its first follow-up."""
    calls = interaction.followup.send.await_args_list
    return _content(calls[0]) if calls else ""


def _outcome(interaction: Any) -> str:
    """What the acknowledgement was last updated to say."""
    edits = interaction.followup.send.return_value.edit.await_args_list
    return _content(edits[-1]) if edits else ""


async def _standings_id(league: ReviewLeague, round_id: int) -> Any:
    from leaguebot.core.db.database import get_connection

    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            "SELECT standings_message_id FROM driver_standings_snapshots WHERE round_id = ?",
            (round_id,),
        )).fetchone()
    return row["standings_message_id"]


async def _sync_states(league: ReviewLeague) -> list[str]:
    return [row["state"] for row in await change_rows(league.db_path) if row["kind"] == KIND]


def _standings_events(league: ReviewLeague) -> list[tuple[str, int]]:
    return [(kind, mid) for kind, ch, mid in league.events if ch == STANDINGS_CHANNEL]


async def test_a_standings_sync_is_acknowledged_and_updated_with_synced(tmp_path):
    """The manager Admin syncs Pro's standings: the command is answered at once with a "⏳"
    acknowledgement naming Pro, which the queue updates, once it has run, to today's success
    reply; the log records the sync as a Success naming the division, and round 3's new table
    stands in the channel in place of 8801."""
    league = await _league(tmp_path)

    interaction = await _sync(league)
    acknowledged = _acknowledged(interaction)
    await run_queue(league.bot)

    assert acknowledged.startswith("⏳") and "**Pro**" in acknowledged
    assert _outcome(interaction) == (
        "✅ Standings for **Pro** synced to the standings channel."
    )
    [line] = [line for line in league.bot.log_channel.sent
              if "| /results standings sync |" in line]
    assert line.startswith("Admin (`<@4242>`) | /results standings sync | Success\n")
    assert "division: Pro" in line
    [new] = league.sent_to(STANDINGS_CHANNEL)
    assert OLD_STANDINGS not in league.channel(STANDINGS_CHANNEL).messages
    assert await _standings_id(league, ROUND_ID) == new


async def test_a_standings_sync_recomputes_every_round_s_standings_when_it_runs(tmp_path):
    """The queue is stopped ahead of the sync; meanwhile Max's 18 points in round 3's Feature
    Race are corrected to 37. Once the queue moves, the table posted shows the standings
    recomputed from the results as they then stand — Max on 37 — not the stored snapshot, which
    holds Lewis alone on 25 (decision 16: recompute, as today)."""
    league = await _league(tmp_path)
    holder = await block_queue(league)
    await _sync(league)
    await _write(
        league, "UPDATE race_session_results SET points_awarded = 37 WHERE driver_user_id = ?",
        MAX,
    )

    holder["fail"] = False
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [new] = league.sent_to(STANDINGS_CHANNEL)
    posted = league.channel(STANDINGS_CHANNEL).messages[new].content
    assert "37" in posted


async def test_a_standings_sync_deletes_each_old_table_after_its_new_one(tmp_path):
    """Rounds 3 and 4 each have standings standing (8801, 8811): round 3's are posted afresh
    before round 4's, each old table is deleted only after its replacement is posted, by a
    `delete_message` job of the sync's, and the channel ends holding the two new tables, their
    ids saved on the rounds' snapshots."""
    import json

    league = await _league(tmp_path)
    await _round_4_posted(league)

    await _sync(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    events = _standings_events(league)
    sends = [mid for kind, mid in events if kind == "send"]
    assert len(sends) == 2
    new_3, new_4 = sends
    assert await _standings_id(league, ROUND_ID) == new_3
    assert await _standings_id(league, LATER_ROUND_ID) == new_4
    assert events.index(("send", new_3)) < events.index(("delete", OLD_STANDINGS))
    assert events.index(("send", new_4)) < events.index(("delete", OLD_LATER_STANDINGS))
    assert set(league.channel(STANDINGS_CHANNEL).messages) == {new_3, new_4}
    deleted = sorted(
        json.loads(row["payload"])["message_id"] for row in await step_rows(league.db_path)
        if row["name"] == "delete_message" and row["done_at"] is not None
    )
    assert deleted == [OLD_STANDINGS, OLD_LATER_STANDINGS]


async def test_a_standings_sync_with_no_standings_channel_is_refused(tmp_path):
    """The queue is stopped ahead of the sync; meanwhile Pro's standings channel is unset. Once
    the queue moves, the sync is refused in the command's own words, "❌ Division 'Pro' has no
    standings channel configured.", and nothing is posted."""
    league = await _league(tmp_path)
    holder = await block_queue(league)
    interaction = await _sync(league)
    await _write(
        league,
        "UPDATE division_results_config SET standings_channel_id = NULL WHERE division_id = ?",
        DIVISION_ID,
    )

    holder["fail"] = False
    await retry_job(league.bot)

    assert "❌ Division 'Pro' has no standings channel configured." in _outcome(interaction)
    assert league.sent_to(STANDINGS_CHANNEL) == []
    assert await _sync_states(league) == ["REFUSED"]


async def test_a_standings_sync_while_an_amendment_is_open_is_refused_when_it_runs(tmp_path):
    """The queue is stopped ahead of the sync; meanwhile an amendment of round 3 opens in
    channel 705. Once the queue moves, the sync is refused in the command's own words, and
    nothing is posted (results spec :473)."""
    league = await _league(tmp_path)
    holder = await block_queue(league)
    interaction = await _sync(league)
    await _write(
        league,
        "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
        "VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-10-05T11:00:00+00:00')",
        ROUND_ID, AMEND_CHANNEL,
    )

    holder["fail"] = False
    await retry_job(league.bot)

    assert f"Round 3 of **Pro** is being amended in <#{AMEND_CHANNEL}>" in _outcome(interaction)
    assert league.sent_to(STANDINGS_CHANNEL) == []
    assert await _sync_states(league) == ["REFUSED"]


async def test_a_standings_sync_asked_twice_is_not_refused_as_a_repeat(tmp_path):
    """The queue is stopped; the manager syncs Pro's standings twice while it waits. Neither is
    refused as a repeat: both are queued, and both are carried out once the queue moves."""
    league = await _league(tmp_path)
    holder = await block_queue(league)
    first = await _sync(league)
    second = await _sync(league)

    assert await _sync_states(league) == ["QUEUED", "QUEUED"]
    holder["fail"] = False
    await retry_job(league.bot)

    assert await _sync_states(league) == ["DONE", "DONE"]
    for interaction in (first, second):
        assert _outcome(interaction) == (
            "✅ Standings for **Pro** synced to the standings channel."
        )


async def test_a_refused_standings_post_stops_the_queue_and_a_discard_says_what_was_not_synced(
    tmp_path,
):
    """Discord refuses round 3's new standings table: the queue stops, 8801 still standing and
    still recorded. A league admin discards the job: the reply names round 3's standings and
    `/results standings sync`, 8801 still stands, and the log line says Incomplete."""
    league = await _league(tmp_path)
    league.channel(STANDINGS_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )

    interaction = await _sync(league)
    await run_queue(league.bot)
    assert await stopped_job(league.db_path) is not None
    assert OLD_STANDINGS in league.channel(STANDINGS_CHANNEL).messages
    assert await _standings_id(league, ROUND_ID) == OLD_STANDINGS

    await discard_job(league.bot)

    outcome = _outcome(interaction)
    assert "Round 3's standings" in outcome
    assert "/results standings sync" in outcome
    assert OLD_STANDINGS in league.channel(STANDINGS_CHANNEL).messages
    [line] = [line for line in league.bot.log_channel.sent
              if "| /results standings sync |" in line]
    assert line.startswith("Admin (`<@4242>`) | /results standings sync | Incomplete")
