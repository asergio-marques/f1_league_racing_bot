"""A season review on the change queue (#439, slice 6b, C92–C98, C97a).

Today `/season placements-review` and `/season config-review` draw and post their review at once,
off the queue: the pictures and the report as the command's follow-ups, the question bound to an
`_ApproveView` whose five minutes run in memory, and the view, a refused press, a confirmation and
the restart's sweep each delete the review themselves. On the queue (decision 5, "Whole review on
the queue"; decision 19):

- `season.review.take_down` (C92), the bot's: one `delete_message` job per message of the review,
  by id, a message already gone or a channel gone completing; then `post_expiry`, the notice
  pinging the reviewer, but where the review was confirmed; then `forget`, which drops the
  `season_review_prompts` row and, but where confirmed, records the review's lapse. A refused
  delete stops the queue; a Discard names the message for deletion by hand. Its payload is
  `{channel_id, question_id, report_ids, reviewer_id, review, ending}`, *ending* one of
  `confirmed`, `expired`, `restart` and `refused`.
- `season.review.post` (C93), the reviewer's: `post_drawing_notice` where pictures are due, one
  `draw_and_post` per picture in order, `delete_drawing_notice`, one `post_report` per section,
  `post_question` (whose record writes the prompt row with every report id and the evidence the
  report was computed from) and `arm_expiry`, which arms the `review_expiry` timer five minutes
  from the question's posting. A picture is drawn through the callable the builder hands the
  factory (`drawing`, run off the event loop), and posted as text in the same try where Discord
  refuses it; one that will not draw is no stop: the Approve button is withheld and the reviewer
  told why, in their reply or, once that can no longer be updated, the log channel.

The factories are `season_review_take_down_change()` and `season_review_post_change(drawing=...,
approve_view=...)` in `leaguebot.core.services.season_review_changes`; the builder registers them
with the image module's drawing and the season cog's view factory. Everything of them is imported
inside a test, so this file collects while it is unbuilt.

The league: `league_double`'s, its review channel 700 found on the league's server, Alex (4242)
the reviewer of season 1's placements.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    INTERACTION_CHANNEL_ID,
    LOG_CHANNEL_ID,
    MEMBER_ID,
    SERVER_ID,
    Clock,
    attach_queue,
    change_rows,
    discard_job,
    history_channel,
    http_error,
    league_double,
    post_in,
    run_queue,
    seed_server,
    stopped_job,
)
from tests.support.review_prompts import store_review_prompt

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
REVIEW_CHANNEL = 700
REVIEWER = MEMBER_ID
PLACEMENTS = "/season placements-review"
TAKE_DOWN = "season.review.take_down"
BOT_USER = SimpleNamespace(id=1, name="LeagueBot")

NO_TAKE_DOWN = (
    "#439: a season review is taken down by its view and the restart's sweep at once, off the "
    "change queue"
)


def _alex() -> Any:
    alex = MagicMock()
    alex.id = REVIEWER
    alex.display_name = "Alex"
    alex.mention = f"<@{REVIEWER}>"
    return alex


def _server(bot: Any, *channels: Any) -> None:
    """Let *bot* and the league's server find *channels* beside the log and interaction channels,
    and Alex on the server; any other channel is gone."""
    known = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel}
    known.update({channel.id: channel for channel in channels})

    async def _fetch(channel_id: int) -> Any:
        if channel_id in known:
            return known[channel_id]
        raise http_error(discord.NotFound, status=404, text="Unknown Channel")

    alex = _alex()
    guild = MagicMock(spec=discord.Guild)
    guild.id = SERVER_ID
    guild.me = BOT_USER
    guild.get_channel = MagicMock(side_effect=known.get)
    guild.get_member = MagicMock(side_effect=lambda uid: alex if uid == REVIEWER else None)
    for channel in channels:
        channel.guild = guild
    bot.get_channel = MagicMock(side_effect=known.get)
    bot.fetch_channel = AsyncMock(side_effect=_fetch)
    bot.get_guild = MagicMock(return_value=guild)


def _standing_review_channel() -> Any:
    """Review channel 700, holding a review the bot posted: its report R1 and R2, then its
    question Q. Each message deleted is kept in `deleted`; what the channel is sent, in `sent`."""
    channel = history_channel(REVIEW_CHANNEL)
    channel.sent = []

    async def _send(content: str = "", **kwargs: Any) -> Any:
        channel.sent.append((content, kwargs))
        return post_in(channel, BOT_USER)

    channel.send = AsyncMock(side_effect=_send)
    channel.r1 = post_in(channel, BOT_USER)
    channel.r2 = post_in(channel, BOT_USER)
    channel.q = post_in(channel, BOT_USER)
    return channel


@pytest.fixture
async def standing(tmp_path):
    """The league's queue with every change type the builder registers, and Alex's placements
    review of season 1 standing in channel 700, recorded in `season_review_prompts`."""
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    bot.user = BOT_USER
    channel = _standing_review_channel()
    _server(bot, channel)
    clock = Clock(NOW)
    attach_queue(bot, db_path, now=clock)
    async with get_connection(db_path) as db:
        await store_review_prompt(
            db, season_id=1, channel_id=REVIEW_CHANNEL, message_id=channel.q.id,
            reviewer_id=REVIEWER, review=PLACEMENTS,
            report_message_ids=(channel.r1.id, channel.r2.id),
        )
        await db.commit()
    return SimpleNamespace(db_path=db_path, bot=bot, channel=channel, clock=clock)


async def _take_down(env: Any, ending: str) -> None:
    """The bot asks for Alex's review in channel 700 to be taken down, as *ending* has it."""
    from leaguebot.core.models.change import ChangeOrigin

    channel = env.channel
    await env.bot.change_queue.ask(
        TAKE_DOWN,
        {"channel_id": REVIEW_CHANNEL, "question_id": channel.q.id,
         "report_ids": [channel.r1.id, channel.r2.id], "reviewer_id": REVIEWER,
         "review": PLACEMENTS, "ending": ending},
        what="taking the placements review down", origin=ChangeOrigin.BOT,
    )


async def _prompts(db_path: str) -> list[Any]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM season_review_prompts")
        return list(await cursor.fetchall())


async def _changes(env: Any, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(env.db_path) if row["kind"] == kind]


def _log(env: Any) -> str:
    return "\n".join(env.bot.log_channel.sent)


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_a_take_down_deletes_each_message_by_id_and_forgets_the_prompt(standing):
    """Alex's review stands in channel 700: report R1 and R2, question Q. The bot asks for it to
    be taken down, confirmed. Nothing is deleted until the queue runs; then Q, R1 and R2 are each
    deleted, the prompt's row is gone and the change is done."""
    channel = standing.channel

    await _take_down(standing, "confirmed")
    deleted_before = list(channel.deleted)
    await run_queue(standing.bot)

    assert deleted_before == []
    assert {m.id for m in channel.deleted} == {channel.q.id, channel.r1.id, channel.r2.id}
    assert channel.messages == []
    assert await _prompts(standing.db_path) == []
    [change] = await _changes(standing, TAKE_DOWN)
    assert change["state"] == "DONE"


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_an_expired_take_down_posts_the_expiry_notice_pinging_the_reviewer(standing):
    """Alex's review ran its five minutes and the bot asks for it to be taken down, expired. Once
    the queue runs the review is deleted, then one notice is posted in channel 700 pinging Alex
    alone and naming `/season placements-review` to run again, and the log channel records the
    review's lapse, started by Alex."""
    channel = standing.channel

    await _take_down(standing, "expired")
    await run_queue(standing.bot)

    assert channel.messages == []
    [(notice, kwargs)] = channel.sent
    assert f"<@{REVIEWER}>" in notice and PLACEMENTS in notice
    mentions = kwargs.get("allowed_mentions")
    assert mentions is not None and mentions.roles is False and mentions.everyone is False
    assert "lapsed" in _log(standing) and PLACEMENTS in _log(standing)
    assert "Alex" in _log(standing)


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_a_confirmed_take_down_posts_no_notice(standing):
    """Alex's review was approved and the bot asks for it to be taken down, confirmed. Once the
    queue runs the review is deleted, nothing is posted in channel 700 and no lapse is recorded."""
    await _take_down(standing, "confirmed")
    await run_queue(standing.bot)

    assert standing.channel.sent == []
    assert "lapsed" not in _log(standing)


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_a_refused_delete_stops_the_queue_and_a_discard_names_it(standing):
    """Discord refuses to delete report message R1. The bot asks for the review to be taken down,
    expired: the queue stops at R1's delete job. A league admin discards it: the change goes on,
    Q and R2 are deleted, R1 stands, the prompt's row is gone, and the log channel names R1 for
    deletion by hand."""
    channel = standing.channel
    channel.r1.fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")

    await _take_down(standing, "expired")
    await run_queue(standing.bot)

    job = await stopped_job(standing.db_path)
    assert job is not None and job["name"] == "delete_message"

    await discard_job(standing.bot)
    await run_queue(standing.bot)

    assert channel.messages == [channel.r1]
    assert await _prompts(standing.db_path) == []
    assert str(channel.r1.id) in _log(standing) and "by hand" in _log(standing)
    [change] = await _changes(standing, TAKE_DOWN)
    assert change["state"] == "DONE"


@pytest.mark.xfail(strict=True, reason=NO_TAKE_DOWN)
async def test_a_take_down_whose_channel_is_gone_completes(standing):
    """Channel 700 has been deleted from the server, Alex's review with it. The bot asks for the
    review to be taken down, expired: the change completes without stopping the queue, posts no
    notice, and the prompt's row is gone."""
    _server(standing.bot)

    await _take_down(standing, "expired")
    await run_queue(standing.bot)

    assert await stopped_job(standing.db_path) is None
    assert standing.channel.sent == []
    assert await _prompts(standing.db_path) == []
    [change] = await _changes(standing, TAKE_DOWN)
    assert change["state"] == "DONE"
