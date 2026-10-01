"""`OutputRouter.post_log` returns the message it posted.

It always held one and discarded it, while its sibling `post_forecast` returned one. A
caller that has just written a block to the log can now point a reader at it — which is
what `/images test` does with its notices.

The **first** message is returned where the content had to be split across Discord's
limit: a link is meant to land a reader at the top of the block, not at its tail. That is
the one respect in which it differs from `post_forecast`, whose callers store the returned
id to edit that message later — returning a different one there would repoint those edits,
so the difference is opt-in rather than a change to the shared sender.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.services.output_router import OutputRouter

#: What the queue and its log lines need of the router, not built yet (#439).
QUEUE_LINES_NOT_BUILT = "#439: the router cannot write a log line in a caller's save yet"
LAST_RESORT_NOT_BUILT = (
    "#439: a line the log channel refuses is still reported in the interaction channel, "
    "not to the member alone"
)


def _channel(sent):
    channel = MagicMock(spec=discord.TextChannel)

    async def _send(content, **_kwargs):
        message = MagicMock(name=f"msg{len(sent)}", jump_url=f"http://j/{len(sent)}")
        sent.append(content)
        return message

    channel.send = AsyncMock(side_effect=_send)
    return channel


def _bot(channel):
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(log_channel_id=99, interaction_channel_id=98)
    )
    return bot


@pytest.mark.asyncio
async def test_post_log_returns_the_posted_message():
    sent = []
    router = OutputRouter(_bot(_channel(sent)))

    message = await router.post_log("something happened")

    assert message is not None
    assert message.jump_url == "http://j/0"


@pytest.mark.asyncio
async def test_a_split_block_returns_its_first_message():
    """So a link lands a reader at the top of the block rather than its tail."""
    sent = []
    router = OutputRouter(_bot(_channel(sent)))

    message = await router.post_log("\n".join(f"line {i}" * 40 for i in range(200)))

    assert len(sent) > 1, "this content was meant to be split"
    assert message.jump_url == "http://j/0"


@pytest.mark.asyncio
async def test_post_forecast_still_returns_its_last_message():
    """Its callers store the id to edit that message later (calendar, constructor
    standings). Returning a different one would repoint those edits."""
    sent = []
    router = OutputRouter(_bot(_channel(sent)))
    division = MagicMock(forecast_channel_id=99)

    message = await router.post_forecast(
        division, "\n".join(f"line {i}" * 40 for i in range(200))
    )

    assert len(sent) > 1, "this content was meant to be split"
    assert message.jump_url == f"http://j/{len(sent) - 1}"


@pytest.mark.asyncio
async def test_post_log_returns_none_when_the_server_has_no_config():
    bot = MagicMock()
    bot.config_service.get_server_config = AsyncMock(return_value=None)

    assert await OutputRouter(bot).post_log("anything") is None


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=LAST_RESORT_NOT_BUILT)
async def test_post_log_returns_none_when_the_channel_cannot_be_reached():
    """Only the log channel is asked for: nothing is posted in the interaction channel (#439)."""
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(log_channel_id=99, interaction_channel_id=98)
    )

    assert await OutputRouter(bot).post_log("anything") is None
    asked = [call.args[0] for call in bot.get_channel.call_args_list]
    asked += [call.args[0] for call in bot.fetch_channel.await_args_list]
    assert set(asked) == {99}


@pytest.mark.asyncio
@pytest.mark.parametrize("mention", ["<@123>", "<@!123>", "<@&123>"])
async def test_every_mention_in_a_log_line_is_written_as_code(mention):
    """Named without notifying: the log channel mentions nobody (#362 builds the forms from the
    shared ones, which also wraps `<@!123>`)."""
    sent = []
    router = OutputRouter(_bot(_channel(sent)))

    await router.post_log(f"{mention} did something")

    assert f"`{mention}`" in sent[0]


async def test_a_forecast_for_a_division_with_no_forecast_channel_posts_nothing():
    """Nothing to post to. It came to the same before — the router asked Discord for a channel
    with no id and logged the failure — less the request (#228)."""
    from leaguebot.core.services.output_router import ForecastChannel

    bot = MagicMock()
    bot.fetch_channel = AsyncMock()
    router = OutputRouter(bot)

    assert await router.post_forecast(ForecastChannel(None), "forecast") is None
    bot.get_channel.assert_not_called()
    bot.fetch_channel.assert_not_awaited()


# ── The factory reset's closing line (#482) ───────────────────────────────
#
# A factory reset posts one line once its clean-up has finished, after the wipe has taken the
# configuration and with it any way to find the log channel. The cog asks the router where the
# log goes before the wipe, and hands that back with the line. A line that cannot be posted is
# not queued: a queued row would sit in the fresh database of a bot serving no server. Nor does
# it fall back to the interaction channel, which no configuration is left to name.


def _unconfigured_bot(channel) -> MagicMock:
    """A bot just wiped: no configuration, but the channel it held is still in Discord."""
    bot = _bot(channel)
    bot.config_service.get_server_config = AsyncMock(return_value=None)
    return bot


async def test_the_router_says_where_the_log_goes():
    router = OutputRouter(_bot(_channel([])))

    assert await router.log_destination() == 99


async def test_the_router_says_there_is_no_log_before_the_bot_is_set_up():
    bot = MagicMock()
    bot.config_service.get_server_config = AsyncMock(return_value=None)

    assert await OutputRouter(bot).log_destination() is None


async def test_a_line_for_a_given_channel_is_written_as_any_other():
    """Its mentions name without notifying and it carries the separator, with no configuration
    left to read."""
    sent = []
    bot = _unconfigured_bot(_channel(sent))

    message = await OutputRouter(bot).post_log("<@77> reset the league", channel=555)

    assert message is not None
    bot.get_channel.assert_called_once_with(555)
    [line] = sent
    assert line.startswith("`<@77>` reset the league")
    assert line.endswith("\n" + "―" * 36)


async def test_a_long_line_for_a_given_channel_is_split_and_returns_its_first_message():
    sent = []
    bot = _unconfigured_bot(_channel(sent))

    message = await OutputRouter(bot).post_log(
        "\n".join(f"line {i}" * 40 for i in range(200)), channel=555
    )

    assert len(sent) > 1, "this content was meant to be split"
    assert message.jump_url == "http://j/0"


def _refusing_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no access")
    )
    return channel


@pytest.mark.parametrize(
    "where",
    [
        pytest.param(
            "refuses",
            id="channel-refuses-the-post",
        ),
        pytest.param(
            "gone",
            id="channel-is-gone",
        ),
    ],
)
async def test_a_line_for_a_given_channel_that_fails_is_neither_queued_nor_redirected(
    tmp_path, monkeypatch, where
):
    """No retry row, no notice in the interaction channel, and no member answered either: the
    caller puts the line in the host's log instead."""
    from leaguebot.core.services import retry_service

    enqueue = AsyncMock()
    monkeypatch.setattr(retry_service, "enqueue", enqueue)
    bot = _bot(_refusing_channel())
    if where == "gone":
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
    router = OutputRouter(bot, retry_db_path=str(tmp_path / "retry.db"))

    assert await router.post_log("the league was reset", channel=555) is None

    enqueue.assert_not_awaited()
    assert [call.args[0] for call in bot.get_channel.call_args_list] == [555]


# ── A line written in a caller's save (#439) ──────────────────────────────
#
# The change queue writes a step's log lines on the step's own connection, so that they are saved
# with what they record or not at all. The line goes on the retry queue as one never tried, and is
# delivered once the save is committed.


async def _league(tmp_path, *, configured: bool = True):
    from leaguebot.core.db.database import run_migrations
    from tests.support.change_queue import league_double, seed_server

    db_path = str(tmp_path / "league.db")
    await run_migrations(db_path)
    if configured:
        await seed_server(db_path)
    return db_path, league_double(db_path)


async def _pending(db_path: str) -> list[dict]:
    from leaguebot.core.db.database import get_connection

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM pending_messages ORDER BY id")
        return [dict(row) for row in await cursor.fetchall()]


@pytest.mark.xfail(strict=True, reason=QUEUE_LINES_NOT_BUILT)
async def test_a_line_queued_in_a_save_is_written_for_the_log_channel_and_not_committed(tmp_path):
    from leaguebot.core.db.database import get_connection
    from tests.support.change_queue import LOG_CHANNEL_ID

    db_path, bot = await _league(tmp_path)

    async with get_connection(db_path) as db:
        row_id = await bot.output_router.queue_log_on(db, "Admin (<@4242>) | /x | Success")
        assert isinstance(row_id, int)
        assert await _pending(db_path) == [], "the line was committed before the caller's save"
        await db.commit()

    [row] = await _pending(db_path)
    assert row["id"] == row_id
    assert row["channel_id"] == LOG_CHANNEL_ID
    assert row["content"].startswith("Admin (`<@4242>`) | /x | Success")
    assert row["content"].endswith("\n" + "―" * 36)
    assert (row["failure_reason"], row["retry_count"]) == ("", 0)
    assert bot.log_channel.sent == [], "the line is delivered after the save, not in it"


@pytest.mark.xfail(strict=True, reason=QUEUE_LINES_NOT_BUILT)
async def test_a_line_queued_before_the_bot_is_set_up_is_not_written(tmp_path):
    from leaguebot.core.db.database import get_connection

    db_path, bot = await _league(tmp_path, configured=False)

    async with get_connection(db_path) as db:
        assert await bot.output_router.queue_log_on(db, "something happened") is None
        await db.commit()

    assert await _pending(db_path) == []


# ── The log channel's last resort: the member, seen by them alone (#439) ──
#
# Where the log channel cannot take a line, the warning goes to the member whose command, button
# or form the line records, through their interaction, once, and never to the interaction
# channel. The router finds the interaction through `league_server.admits`, which every command,
# view and form passes before its callback runs in the same task.


async def _admitted(bot, interaction) -> None:
    from leaguebot.core.utils.league_server import admits

    bot.config_service.get_league_server_id = AsyncMock(return_value=interaction.guild_id)
    assert await admits(bot, interaction) is True


async def _settle() -> None:
    """Let the tasks the router started on the way finish."""
    for _ in range(20):
        await asyncio.sleep(0)


def _refusing_log(bot) -> None:
    from tests.support.change_queue import http_error

    bot.log_channel.fails = http_error(discord.Forbidden, status=403, text="Missing Access")


def _warnings(interaction) -> list:
    from tests.support.change_queue import LOG_CHANNEL_WARNING

    return [
        call for call in interaction.followup.send.await_args_list
        if (call.args[0] if call.args else call.kwargs.get("content")) == LOG_CHANNEL_WARNING
    ]


def _interaction(bot, *, age: timedelta = timedelta(seconds=5)):
    from tests.support.change_queue import member_interaction

    return member_interaction(bot, created_at=datetime.now(timezone.utc) - age)


@pytest.mark.xfail(strict=True, reason=LAST_RESORT_NOT_BUILT)
async def test_a_line_the_log_channel_refuses_is_told_to_the_member_alone(tmp_path):
    db_path, bot = await _league(tmp_path)
    _refusing_log(bot)
    interaction = _interaction(bot)

    async def command():
        await _admitted(bot, interaction)
        await interaction.response.defer(ephemeral=True)
        await bot.output_router.post_log("Admin (<@4242>) | /x | Success")

    await asyncio.create_task(command())
    await _settle()

    [warning] = _warnings(interaction)
    assert warning.kwargs.get("ephemeral") is True
    assert bot.interaction_channel.sent == []
    assert len(await _pending(db_path)) == 1, "the line still waits on the retry queue"


@pytest.mark.xfail(strict=True, reason=LAST_RESORT_NOT_BUILT)
async def test_a_warning_for_an_interaction_not_yet_answered_follows_its_reply(tmp_path):
    """Sending the warning first would take the command's one response."""
    _db_path, bot = await _league(tmp_path)
    _refusing_log(bot)
    interaction = _interaction(bot)

    async def command():
        await _admitted(bot, interaction)
        await bot.output_router.post_log("Admin (<@4242>) | /x | Success")
        await asyncio.sleep(0)
        assert _warnings(interaction) == [], "warned before the command had answered"
        await interaction.response.send_message("✅ Done.", ephemeral=True)

    await asyncio.create_task(command())
    await _settle()

    assert [call.args[0] for call in interaction.response.send_message.await_args_list] == [
        "✅ Done."
    ]
    [warning] = _warnings(interaction)
    assert warning.kwargs.get("ephemeral") is True
    assert bot.interaction_channel.sent == []


@pytest.mark.xfail(strict=True, reason=LAST_RESORT_NOT_BUILT)
async def test_a_member_is_warned_once_however_many_of_their_lines_fail(tmp_path):
    db_path, bot = await _league(tmp_path)
    _refusing_log(bot)
    interaction = _interaction(bot)

    async def command():
        await _admitted(bot, interaction)
        await interaction.response.defer(ephemeral=True)
        for n in range(3):
            await bot.output_router.post_log(f"Admin (<@4242>) | /x | line {n}")

    await asyncio.create_task(command())
    await _settle()

    assert len(_warnings(interaction)) == 1
    assert bot.interaction_channel.sent == []
    assert len(await _pending(db_path)) == 3


@pytest.mark.xfail(strict=True, reason=LAST_RESORT_NOT_BUILT)
async def test_a_line_with_no_interaction_to_answer_goes_to_the_host_log_alone(tmp_path, caplog):
    """A timed job: no member asked, so the failure is the host's log's alone."""
    db_path, bot = await _league(tmp_path)
    _refusing_log(bot)

    async def timed_job():
        await bot.output_router.post_log("Weather | phase 1 posted")

    with caplog.at_level(logging.ERROR):
        await asyncio.create_task(timed_job())
        await _settle()

    assert len(await _pending(db_path)) == 1
    assert bot.log_channel.sent == []
    assert bot.interaction_channel.sent == []
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.mark.xfail(strict=True, reason=LAST_RESORT_NOT_BUILT)
async def test_an_interaction_over_fourteen_minutes_old_is_not_answered(tmp_path):
    """Discord's token lasts fifteen minutes; the router leaves itself one to spare."""
    db_path, bot = await _league(tmp_path)
    _refusing_log(bot)
    interaction = _interaction(bot, age=timedelta(minutes=14, seconds=30))

    async def command():
        await _admitted(bot, interaction)
        await interaction.response.defer(ephemeral=True)
        await bot.output_router.post_log("Admin (<@4242>) | /x | Success")

    await asyncio.create_task(command())
    await _settle()

    assert interaction.followup.send.await_count == 0
    assert bot.interaction_channel.sent == []
    assert len(await _pending(db_path)) == 1


@pytest.mark.xfail(strict=True, reason=LAST_RESORT_NOT_BUILT)
async def test_an_interaction_the_league_s_check_admits_is_the_one_its_lines_answer(tmp_path):
    """Two commands at once, and a timed job beside them: each command's lines answer that
    command's member alone, and the job's answer nobody."""
    from tests.support.change_queue import member

    _db_path, bot = await _league(tmp_path)
    _refusing_log(bot)
    first = _interaction(bot)
    second = _interaction(bot)
    second.user = member(5151, "Steward", "Steward#0002")
    first_admitted, second_done = asyncio.Event(), asyncio.Event()

    async def first_command():
        await _admitted(bot, first)
        await first.response.defer(ephemeral=True)
        first_admitted.set()
        await second_done.wait()
        await bot.output_router.post_log("Admin (<@4242>) | /first | Success")

    async def second_command():
        await first_admitted.wait()
        await _admitted(bot, second)
        await second.response.defer(ephemeral=True)
        await bot.output_router.post_log("Steward (<@5151>) | /second | Success")
        second_done.set()

    async def timed_job():
        await first_admitted.wait()
        await bot.output_router.post_log("Weather | phase 1 posted")

    await asyncio.gather(
        asyncio.create_task(first_command()),
        asyncio.create_task(second_command()),
        asyncio.create_task(timed_job()),
    )
    await _settle()

    assert len(_warnings(first)) == 1
    assert len(_warnings(second)) == 1
    assert bot.interaction_channel.sent == []
