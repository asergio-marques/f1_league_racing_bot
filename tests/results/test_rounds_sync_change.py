"""`/results rounds sync` on the change queue (#439, slice 6): a member's change carried out on the
queue whole (`results.rounds.sync`), acknowledged at once and the acknowledgement updated with
the outcome.

The league is `review_league`'s: division 11 (Pro) of season 1, being raced, its results channel
700 holding round 3's Feature Race results as message 8800 and its standings channel 701 round 3's
standings, results switched on, images off unless a test turns them on. A manager runs the
command, the queue is run, and the test reads the channels, the database and what the manager was
told. Each session is posted again, then the message it replaces deleted by a job of its own, in
round order; a table Discord refuses stops the queue, and its next try posts the text. The sync's
gates are checked again when it runs (core spec :125), so one waiting behind a stopped queue is
refused as the command would be.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.models.season import SeasonStage, status_of_stage
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
    OLD_RESULTS,
    RESULTS_CHANNEL,
    ROUND_ID,
    SEASON_ID,
    STANDINGS_CHANNEL,
    ReviewLeague,
    block_queue,
    review_league,
)
from tests.support.undecorate import undecorate

KIND = "results.rounds.sync"
#: Round 4's Feature Race results, standing where a test posts them.
OLD_LATER_RESULTS = 8810


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
    """Round 4 at Spa holds a Feature Race too, Lewis winning it, its results standing as
    message 8810 in the results channel."""
    from leaguebot.core.db.database import get_connection

    async with get_connection(league.db_path) as db:
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, results_message_id, results_message_ids) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard', ?, ?)",
            (LATER_ROUND_ID, DIVISION_ID, OLD_LATER_RESULTS, f"[{OLD_LATER_RESULTS}]"),
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
        await db.commit()
    league.channel(RESULTS_CHANNEL).seed(OLD_LATER_RESULTS, "round 4 results")


async def _sync(league: ReviewLeague, name: str = "Pro") -> Any:
    """Run `/results rounds sync` for *name* as the manager Admin (4242); the queue is not run.
    Gives the interaction, its acknowledgement's message able to be edited."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot)
    interaction.guild = league.guild
    interaction.command = SimpleNamespace(qualified_name="results rounds sync")
    interaction.followup.send.return_value = MagicMock(edit=AsyncMock())
    await undecorate(ResultsCog.rounds_sync)(cog, interaction, name)
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


async def _session_ids(league: ReviewLeague, round_id: int = ROUND_ID) -> Any:
    from leaguebot.core.db.database import get_connection

    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            "SELECT results_message_id FROM session_results WHERE round_id = ?", (round_id,),
        )).fetchone()
    return row["results_message_id"]


def _results_events(league: ReviewLeague) -> list[tuple[str, int]]:
    return [(kind, mid) for kind, ch, mid in league.events if ch == RESULTS_CHANNEL]


async def _sync_states(league: ReviewLeague) -> list[str]:
    return [row["state"] for row in await change_rows(league.db_path) if row["kind"] == KIND]


def _results_drawn(league: ReviewLeague, tmp_path: Any) -> tuple[ExitStack, AsyncMock]:
    """Image generation on for the results aspect, its template valid and its render giving a
    PNG: the real picture poster runs. Gives the patches, to enter, and the render, to count."""
    png = tmp_path / "results.png"
    png.write_bytes(b"\x89PNG")
    league.bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    league.bot.image_config_service.get_toggles = AsyncMock(return_value={"results": True})
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={"results_race_template": MagicMock(valid=True)}
    )
    decision = MagicMock(posts_image=True, rejects=False, png_paths=[png], notices=[],
                         problem=None)
    drawing = MagicMock(template_key="results_race_template", session_name="Race")
    render = AsyncMock(return_value=decision)
    stack = ExitStack()
    stack.enter_context(patch("leaguebot.image.services.image_results_post.build_drawing",
                              new=AsyncMock(return_value=drawing)))
    stack.enter_context(patch("leaguebot.image.services.image_results_post.render_png",
                              new=render))
    return stack, render


async def test_a_rounds_sync_is_acknowledged_and_updated_with_synced(tmp_path):
    league = await _league(tmp_path)

    interaction = await _sync(league)
    acknowledged = _acknowledged(interaction)
    await run_queue(league.bot)

    assert acknowledged.startswith("⏳ Syncing the results of every round of **Pro**.")
    assert _outcome(interaction) == (
        "✅ Results for all rounds in **Pro** synced to the results channel."
    )
    [line] = [line for line in league.bot.log_channel.sent
              if "| /results rounds sync |" in line]
    assert line.startswith("Admin (`<@4242>`) | /results rounds sync | Success\n")
    assert "division: Pro" in line
    [new] = league.sent_to(RESULTS_CHANNEL)
    assert set(league.channel(RESULTS_CHANNEL).messages) == {new}


async def test_every_session_is_posted_in_round_order_each_before_its_old_message_is_deleted(
    tmp_path,
):
    """Rounds 3 and 4 each have a Feature Race standing (8800, 8810): round 3's is posted
    before round 4's, each old message is deleted only after its replacement is posted, by a
    `delete_message` job of the sync's, and the channel ends holding the two new tables, their
    ids saved."""
    league = await _league(tmp_path)
    await _round_4_posted(league)

    await _sync(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    events = _results_events(league)
    sends = [mid for kind, mid in events if kind == "send"]
    assert len(sends) == 2
    new_3, new_4 = sends
    assert await _session_ids(league, ROUND_ID) == new_3
    assert await _session_ids(league, LATER_ROUND_ID) == new_4
    assert events.index(("send", new_3)) < events.index(("delete", OLD_RESULTS))
    assert events.index(("send", new_4)) < events.index(("delete", OLD_LATER_RESULTS))
    assert set(league.channel(RESULTS_CHANNEL).messages) == {new_3, new_4}
    deleted = sorted(
        json.loads(row["payload"])["message_id"] for row in await step_rows(league.db_path)
        if row["name"] == "delete_message" and row["done_at"] is not None
    )
    assert deleted == [OLD_RESULTS, OLD_LATER_RESULTS]


async def test_a_repost_discord_refuses_stops_the_queue_and_leaves_the_old_table(tmp_path):
    league = await _league(tmp_path)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )

    await _sync(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_session_results"
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages
    assert await _session_ids(league) == OLD_RESULTS
    assert ("delete", OLD_RESULTS) not in _results_events(league)


async def test_a_rounds_sync_posts_no_standings(tmp_path):
    """The sync, carried out on the queue, reposts the results; the standings are
    `/results standings sync`'s."""
    league = await _league(tmp_path)

    await _sync(league)
    await run_queue(league.bot)

    assert await _sync_states(league) == ["DONE"]
    assert league.sent_to(RESULTS_CHANNEL)
    assert league.sent_to(STANDINGS_CHANNEL) == []
    assert [row for row in await step_rows(league.db_path)
            if row["name"] == "post_standings"] == []


async def test_a_retried_repost_posts_the_text(tmp_path):
    """Images on: Discord refuses every post, so the picture and the text in its place both
    fail, and the queue stops; once Discord takes posts again, Retry posts the table as text,
    drawing nothing again."""
    league = await _league(tmp_path)
    results = league.channel(RESULTS_CHANNEL)
    results.send_fails = http_error(discord.Forbidden, status=403, text="Missing Access")
    stack, render = _results_drawn(league, tmp_path)
    with stack:
        await _sync(league)
        await run_queue(league.bot)
        assert (await stopped_job(league.db_path))["name"] == "post_session_results"
        drawn = render.await_count

        results.send_fails = None
        await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert render.await_count == drawn
    last = results.send.await_args
    assert not last.kwargs.get("file") and not last.kwargs.get("files")
    [new] = league.sent_to(RESULTS_CHANNEL)
    assert await _session_ids(league) == new


async def test_a_rounds_sync_waiting_while_an_amendment_opens_is_refused_when_it_runs(tmp_path):
    """The queue is stopped ahead of the sync; meanwhile an amendment of round 3 opens in
    channel 705. Once the queue moves, the sync is refused in the command's own words, and
    nothing is posted (results spec :473; core spec :125)."""
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
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert await _sync_states(league) == ["REFUSED"]


async def test_a_rounds_sync_waiting_while_the_season_reaches_pending_completion_is_refused_when_it_runs(
    tmp_path,
):
    """The queue is stopped ahead of the sync; meanwhile every division finishes, and the season
    reaches Pending completion. Once the queue moves, the sync is refused in the command's own
    words, and nothing is posted (results spec :576)."""
    from leaguebot.core.utils.season_gate import stage_refusal

    league = await _league(tmp_path)
    holder = await block_queue(league)
    interaction = await _sync(league)
    await _write(
        league, "UPDATE seasons SET stage = ?, status = ? WHERE id = ?",
        SeasonStage.PENDING_COMPLETION.value,
        status_of_stage(SeasonStage.PENDING_COMPLETION).value, SEASON_ID,
    )

    holder["fail"] = False
    await retry_job(league.bot)

    refusal = stage_refusal(SeasonStage.PENDING_COMPLETION, "results rounds sync")
    assert refusal is not None
    assert refusal in _outcome(interaction)
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert await _sync_states(league) == ["REFUSED"]


async def test_a_rounds_sync_with_a_discarded_table_says_what_was_not_synced(tmp_path):
    """Discord refuses round 3's table and a league admin discards it: the reply says the
    results were not all synced, naming the table and `/results rounds sync`, the old table
    still standing, and the log line says Incomplete."""
    league = await _league(tmp_path)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )

    interaction = await _sync(league)
    await run_queue(league.bot)
    await discard_job(league.bot)

    outcome = _outcome(interaction)
    assert outcome.startswith("⚠️ The results of **Pro** were not all synced:")
    assert "/results rounds sync" in outcome.removeprefix(
        "⚠️ The results of **Pro** were not all synced:"
    )
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages
    [line] = [line for line in league.bot.log_channel.sent
              if "| /results rounds sync |" in line]
    assert line.startswith("Admin (`<@4242>`) | /results rounds sync | Incomplete")


OLD_CANCELLED_LINE = 8820


async def test_a_rounds_sync_replaces_a_session_cancelled_line(tmp_path):
    """Round 3's Feature Qualifying was cancelled, its line standing in the results channel as
    message 8820, recorded as the qualifying's. `/results rounds sync` for Pro posts a new line in
    the qualifying's place, before round 3's race table, records it as the qualifying's, and
    deletes 8820 after it."""
    from leaguebot.core.db.database import get_connection

    league = await _league(tmp_path)
    await _write(
        league,
        "INSERT INTO session_results (round_id, division_id, session_type, status, "
        "results_message_id, results_message_ids) "
        "VALUES (?, ?, 'FEATURE_QUALIFYING', 'CANCELLED', ?, ?)",
        ROUND_ID, DIVISION_ID, OLD_CANCELLED_LINE, f"[{OLD_CANCELLED_LINE}]",
    )
    league.channel(RESULTS_CHANNEL).seed(
        OLD_CANCELLED_LINE, "🚫 Round 3 — Qualifying (Pro): This session was cancelled.")

    await _sync(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    line, _table = league.sent_to(RESULTS_CHANNEL)
    assert "This session was cancelled" in league.channel(RESULTS_CHANNEL).messages[line].content
    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            "SELECT results_message_id FROM session_results "
            "WHERE round_id = ? AND session_type = 'FEATURE_QUALIFYING'", (ROUND_ID,),
        )).fetchone()
    assert row["results_message_id"] == line
    assert OLD_CANCELLED_LINE in league.deleted_in(RESULTS_CHANNEL)
    assert league.events.index(("send", RESULTS_CHANNEL, line)) < league.events.index(
        ("delete", RESULTS_CHANNEL, OLD_CANCELLED_LINE))
