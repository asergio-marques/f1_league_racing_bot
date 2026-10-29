"""The season-review approve button, swept away by a restart.

The button expires five minutes after `/season placements-review` posts it. A bot restarted inside
that window leaves a public review offering a button whose five minutes nothing is counting, and
nothing else would ever clear it.

Every row surviving to startup is expired by definition: the bot was down, so the five minutes
cannot have been served. Since #439 (slice 6b, C98) the restart's sweep deletes and posts nothing
itself: it asks the change queue, before the queue starts, for one `season.review.take_down` per
row standing, and the queue's jobs delete the review's messages by id, post the notice pinging the
reviewer, record the lapse and drop the row. A message or a channel already gone completes its job;
a deletion Discord refuses stops the queue, the row standing until Retry or Discard, rather than
being lost quietly.

The league: `league_double`'s, its review channel 700 found on the league's server, Alex (4242) the
reviewer of season 1's placements.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


# `__main__.py` reads the token at import time and raises without it; `tests/conftest.py` gives it
# a placeholder before collection, so every file importing `bot` can do so at module level.
from leaguebot.__main__ import _recover_expired_review_prompts
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.change import ChangeOrigin
from tests.support.change_queue import (
    INTERACTION_CHANNEL_ID,
    LOG_CHANNEL_ID,
    SERVER_ID,
    Clock,
    attach_queue,
    change_rows,
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
CHANNEL_ID = 700
REVIEWER_ID = 4242
PLACEMENTS = "/season placements-review"
TAKE_DOWN = "season.review.take_down"
BOT_USER = SimpleNamespace(id=1, name="LeagueBot")



def _server(bot: Any, channel: Any | None, *, cached: bool = True) -> None:
    """Let *bot* and the league's server find review *channel* (None: it is gone) beside the log
    and interaction channels, and Alex on the server. *cached* False leaves the channel out of
    the cache, so only a fetch finds it."""
    known = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel}
    cache = dict(known)
    if channel is not None:
        known[channel.id] = channel
        if cached:
            cache[channel.id] = channel

    async def _fetch(channel_id: int) -> Any:
        if channel_id in known:
            return known[channel_id]
        raise http_error(discord.NotFound, status=404, text="Unknown Channel")

    alex = MagicMock()
    alex.id = REVIEWER_ID
    alex.display_name = "Alex"
    alex.mention = f"<@{REVIEWER_ID}>"
    guild = MagicMock(spec=discord.Guild)
    guild.id = SERVER_ID
    guild.me = BOT_USER
    guild.get_channel = MagicMock(side_effect=cache.get)
    guild.get_member = MagicMock(side_effect=lambda uid: alex if uid == REVIEWER_ID else None)
    if channel is not None:
        channel.guild = guild
    bot.get_channel = MagicMock(side_effect=cache.get)
    bot.fetch_channel = AsyncMock(side_effect=_fetch)
    bot.get_guild = MagicMock(return_value=guild)


def _review_channel() -> Any:
    """Review channel 700. Each message deleted is kept in `deleted`; what it is sent, in `sent`."""
    channel = history_channel(CHANNEL_ID)
    channel.sent = []

    async def _send(content: str = "", **kwargs: Any) -> Any:
        channel.sent.append(content)
        return post_in(channel, BOT_USER)

    channel.send = AsyncMock(side_effect=_send)
    return channel


@pytest.fixture
async def league(tmp_path):
    """The league's queue with every change type the builder registers, and review channel 700
    on its server, holding nothing yet."""
    db_path = str(tmp_path / "prompts.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    bot.user = BOT_USER
    channel = _review_channel()
    _server(bot, channel)
    attach_queue(bot, db_path, now=Clock(NOW))
    return SimpleNamespace(db_path=db_path, bot=bot, channel=channel)


async def _store(
    env: Any,
    message_id: int,
    *,
    review: str = PLACEMENTS,
    report_message_ids: tuple[int, ...] = (),
    verb: str | None = None,
) -> None:
    """One review prompt standing in channel 700, posted for Alex's review of season 1."""
    async with get_connection(env.db_path) as db:
        await store_review_prompt(
            db,
            season_id=1,
            channel_id=CHANNEL_ID,
            message_id=message_id,
            reviewer_id=REVIEWER_ID,
            review=review,
            report_message_ids=report_message_ids,
            verb=verb,
        )
        await db.commit()


async def _rows(path: str) -> list:
    async with get_connection(path) as db:
        cursor = await db.execute("SELECT * FROM season_review_prompts")
        return await cursor.fetchall()


async def _take_downs(env: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(env.db_path) if row["kind"] == TAKE_DOWN]


async def _restart(env: Any) -> None:
    """The bot starts: the sweep runs, deleting and posting nothing itself and asking one
    take-down of the queue for each review standing; then the queue runs."""
    standing = len(await _rows(env.db_path))
    asked = len(await _take_downs(env))
    deleted, sent = len(env.channel.deleted), len(env.channel.sent)
    await _recover_expired_review_prompts(env.bot)
    assert (len(env.channel.deleted), len(env.channel.sent)) == (deleted, sent)
    assert len(await _take_downs(env)) - asked == standing
    await run_queue(env.bot)


def _lapses(env: Any) -> list[str]:
    return [line for line in env.bot.log_channel.sent if line.startswith("⌛ ")]


async def test_the_notice_pings_the_reviewer(league):
    """Alex's question stands at a restart. Once the queue runs, channel 700 is told the review
    expired, pinging Alex and naming `/season placements-review`."""
    question = post_in(league.channel, BOT_USER)
    await _store(league, question.id)

    await _restart(league)

    [notice] = league.channel.sent
    assert f"<@{REVIEWER_ID}>" in notice
    assert PLACEMENTS in notice


async def test_the_row_is_cleared(league):
    """Alex's review question stands in channel 700 when the bot starts. The sweep deletes
    nothing itself and asks one take-down of the queue; once the queue runs, the question is
    gone, and so is its row — left behind, it would be swept again on every restart for ever —
    and the queue is not stopped."""
    question = post_in(league.channel, BOT_USER)
    await _store(league, question.id)

    await _restart(league)

    assert league.channel.deleted == [question]
    assert await _rows(league.db_path) == []
    assert await stopped_job(league.db_path) is None


async def test_a_message_already_gone_still_clears_its_row(league):
    """Alex's question was deleted by hand while the bot was down: its row stands, the message
    does not. The take-down's delete completes as gone, the notice is still posted once, the row
    is cleared and the queue is not stopped."""
    gone = post_in(league.channel, BOT_USER)
    await gone.delete()
    await _store(league, gone.id)

    await _restart(league)

    assert await _rows(league.db_path) == []
    assert len(league.channel.sent) == 1
    assert await stopped_job(league.db_path) is None


async def test_an_unreachable_channel_still_clears_its_row(league):
    """Channel 700 itself is gone: neither the cache nor a fetch finds it. The take-down completes
    with the channel gone, nothing is posted anywhere in its place, the row is cleared and the
    queue is not stopped."""
    question = post_in(league.channel, BOT_USER)
    await _store(league, question.id)
    _server(league.bot, None)

    await _restart(league)

    assert await _rows(league.db_path) == []
    assert league.channel.sent == []
    assert await stopped_job(league.db_path) is None


async def test_a_channel_missing_from_the_cache_is_fetched(league):
    """The sweep runs during startup, where the cache may not yet hold every channel.

    A miss and a deleted channel look alike to the cache, so trusting it alone would drop the
    only record of a message still standing. Channel 700 is missing from the cache but found by a
    fetch: Alex's question is deleted, the notice posted and the row cleared.
    """
    question = post_in(league.channel, BOT_USER)
    await _store(league, question.id)
    _server(league.bot, league.channel, cached=False)

    await _restart(league)

    assert league.channel.deleted == [question]
    assert len(league.channel.sent) == 1
    assert await _rows(league.db_path) == []


async def test_a_fetch_that_fails_stops_the_queue_keeping_its_row(league):
    """Channel 700 is missing from the cache, and Discord fails the fetch (500). The take-down
    stops the queue rather than clearing the row: Alex's review still stands in the record, to
    be taken down by the try after, Retry or Discard."""
    question = post_in(league.channel, BOT_USER)
    await _store(league, question.id)
    _server(league.bot, league.channel, cached=False)
    league.bot.fetch_channel = AsyncMock(side_effect=http_error(status=500, text="boom"))

    await _restart(league)

    assert await stopped_job(league.db_path) is not None
    assert len(await _rows(league.db_path)) == 1


async def test_a_refused_delete_stops_the_queue(league):
    """Discord refuses to delete Alex's question (403). The take-down stops the queue at that
    delete, nothing lost quietly: the question still stands, and so does its row."""
    question = post_in(
        league.channel, BOT_USER,
        fails=http_error(discord.Forbidden, status=403, text="Missing Permissions"),
    )
    await _store(league, question.id)

    await _restart(league)

    assert (await stopped_job(league.db_path))["name"] == "delete_message"
    assert question in league.channel.messages
    assert len(await _rows(league.db_path)) == 1


async def test_nothing_standing_does_nothing(league):
    """No review stands when the bot starts: no take-down is asked and nothing is posted."""
    await _restart(league)

    assert await _take_downs(league) == []
    assert league.channel.sent == []


async def test_an_unreadable_database_is_survived():
    """A sweep that raises would stop the bot finishing its startup."""
    bot = MagicMock()
    bot.db_path = "/nonexistent/nowhere.db"
    await _recover_expired_review_prompts(bot)


# ── #482: what the sweep records, and one row per standing prompt ─────────────────────────


@pytest.mark.parametrize("review", [PLACEMENTS, "/season config-review"])
async def test_a_cleared_prompt_records_its_lapse_naming_the_reviewer_and_the_review(
    league, review
):
    """Criterion 10: Alex's review, of either kind, stands at a restart. Once the queue has taken
    it down, the notice is posted once and one lapse line is recorded, naming the review, saying
    it lapsed unconfirmed and started by Alex, and saying beneath to run the review again."""
    question = post_in(league.channel, BOT_USER)
    await _store(league, question.id, review=review)

    await _restart(league)

    assert len(league.channel.sent) == 1
    lines = _lapses(league)
    assert len(lines) == 1, lines
    head, _, beneath = lines[0].partition("\n")
    assert review in head, head
    assert head.replace("`", "").endswith(
        "lapsed unconfirmed (started by Alex (<@4242>))"
    ), head
    assert review in beneath and "again" in beneath, lines[0]


async def test_an_older_review_lapsing_leaves_a_newer_ones_record_for_the_restart(league):
    """F1. Two placements reviews stand in channel 700, Alex's older question and his newer one.
    The older's five minutes run, and its timer asks its take-down; once the queue runs, only the
    newer's row is left. The bot then restarts with the newer still standing, and the sweep
    finds it: the newer is deleted, its notice pings Alex, one more lapse naming
    `/season placements-review` is recorded, and no row is left."""
    older = post_in(league.channel, BOT_USER)
    newer = post_in(league.channel, BOT_USER)
    await _store(league, older.id)
    await _store(league, newer.id)
    await league.bot.change_queue.ask(
        TAKE_DOWN,
        {"channel_id": CHANNEL_ID, "question_id": older.id, "report_ids": [],
         "reviewer_id": REVIEWER_ID, "review": PLACEMENTS, "ending": "expired"},
        what="taking the placements review down", origin=ChangeOrigin.BOT,
    )
    await run_queue(league.bot)

    assert [int(row["message_id"]) for row in await _rows(league.db_path)] == [newer.id]

    lapsed = len(_lapses(league))
    sent = len(league.channel.sent)
    await _restart(league)

    assert newer in league.channel.deleted
    [notice] = league.channel.sent[sent:]
    assert f"<@{REVIEWER_ID}>" in notice
    later = _lapses(league)[lapsed:]
    assert len(later) == 1, later
    assert PLACEMENTS in later[0].partition("\n")[0]
    assert await _rows(league.db_path) == []


async def test_a_review_standing_at_a_restart_has_its_report_deleted_with_its_prompt(league):
    """F2. Alex's placements review stands in channel 700: its report (two messages) and its
    question. The bot restarts; once the queue runs, the report goes with the question, each
    deleted once, the notice posted once and the row cleared."""
    first = post_in(league.channel, BOT_USER)
    second = post_in(league.channel, BOT_USER)
    question = post_in(league.channel, BOT_USER)
    await _store(league, question.id, report_message_ids=(first.id, second.id))

    await _restart(league)

    assert sorted(m.id for m in league.channel.deleted) == sorted(
        [first.id, second.id, question.id]
    )
    assert len(league.channel.sent) == 1
    assert await _rows(league.db_path) == []


async def test_a_prompt_whose_report_list_cannot_be_read_is_still_cleared(league):
    """A standing prompt whose recorded report is unreadable (written by hand, say) is still
    swept: its question is deleted, the notice posted, its lapse recorded and its row cleared,
    and the start-up goes on rather than stopping on it."""
    question = post_in(league.channel, BOT_USER)
    await _store(league, question.id)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE season_review_prompts SET report_message_ids = 'not a list'")
        await db.commit()

    await _restart(league)

    assert league.channel.deleted == [question]
    assert len(league.channel.sent) == 1
    assert _lapses(league)
    assert await _rows(league.db_path) == []


@pytest.mark.parametrize(
    ("review", "verb", "custom_id"),
    [
        (PLACEMENTS, "approved", "season_review:approve"),
        (PLACEMENTS, "confirmed", "season_review:confirm_placements"),
        ("/season config-review", "confirmed", None),
    ],
)
async def test_each_standing_review_is_routed_under_its_own_button(league, review, verb, custom_id):
    """At a restart the sweep routes each standing question's button again by its message id. A
    placements approval keeps `season_review:approve`, a mid-season confirmation keeps
    `season_review:confirm_placements`: routed under the other's id, a press finds no item and
    Discord drops it without a refusal."""
    question = post_in(league.channel, BOT_USER)
    await _store(league, question.id, review=review, verb=verb)
    league.bot.add_view = MagicMock()

    await _recover_expired_review_prompts(league.bot)

    league.bot.add_view.assert_called_once()
    routed = league.bot.add_view.call_args.args[0]
    assert league.bot.add_view.call_args.kwargs["message_id"] == question.id
    ids = [item.custom_id for item in routed.children]
    if custom_id is None:
        assert "season_review:approve" not in ids
        assert "season_review:confirm_placements" not in ids
    else:
        assert ids == [custom_id]
