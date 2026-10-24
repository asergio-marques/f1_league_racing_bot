"""`/division calendar-sync` on the change queue (#439, slice 6): the command only posts, so it is a
member's change carried out on the queue whole (`calendar.sync`), acknowledged at once and the
acknowledgement updated with the outcome.

The league is `ongoing_league`'s: season 3 being raced, Pro's calendar standing as message 6001 in
its calendar channel (601), images off unless a test turns them on. Admin 77 runs the command, the
queue is run, and the test reads the channel, the database and what the admin was told. A calendar
Discord refuses stops the queue rather than going to the old retry queue, and its next try posts the
text.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.models.season import SeasonStage, status_of_stage
from tests.support.change_queue import (
    change_rows,
    discard_job,
    http_error,
    member_interaction,
    register,
    retry_job,
    run_queue,
    stopped_job,
    tier_member,
)
from tests.support.season_league import (
    ADMIN_ID,
    CALENDAR_MESSAGE,
    DIVISIONS,
    PRO,
    SEASON_ID,
    ongoing_league,
)
from tests.support.undecorate import undecorate

KIND = "calendar.sync"
PRO_CALENDAR = DIVISIONS[PRO][3].calendar


async def _sync(league: Any, name: str = "Pro") -> Any:
    """Run `/division calendar-sync` for *name* as admin 77; the queue is not run. Gives the
    interaction, its acknowledgement's message able to be edited."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    interaction = member_interaction(
        league.bot, user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    interaction.guild = league.guild
    interaction.command = SimpleNamespace(qualified_name="division calendar-sync")
    interaction.followup.send.return_value = MagicMock(edit=AsyncMock())
    await undecorate(SeasonCog.division_calendar_sync)(league.cog, interaction, name)
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


async def _calendar_id(league: Any) -> str | None:
    [row] = await league.rows("SELECT calendar_message_id FROM divisions WHERE id = ?", PRO)
    return row["calendar_message_id"]


async def _retried_posts(league: Any) -> list[dict[str, Any]]:
    """What waits on the old retry queue for Pro's calendar channel."""
    return await league.rows("SELECT * FROM pending_messages WHERE channel_id = ?", PRO_CALENDAR)


def _calendar_texts(league: Any) -> list[str]:
    from leaguebot.core.services.calendar_post_service import calendar_heading

    return [text for text in league.texts(PRO_CALENDAR) if calendar_heading("Pro") in text]


async def _queue_stopped_ahead(league: Any) -> None:
    """A change of the bot's, asked before anything else, whose job Discord refuses: the queue
    stops at it, and whatever is asked after it waits."""
    from leaguebot.core.models.change import PlannedStep, StepFailedOnDiscord, StepKind, Verdict
    from leaguebot.core.services.change_queue import ChangeType, Step

    async def _refused(_ctx: Any) -> Any:
        raise StepFailedOnDiscord("Missing Access")

    async def _go(_ctx: Any) -> Any:
        return Verdict.go()

    register(league.bot, ChangeType(
        kind="blocker", opening=(PlannedStep("block", {}),),
        steps={"block": Step("block", StepKind.ACT, _refused)}, check=_go,
        key=lambda _payload: "blocker", doing=lambda _payload: "Blocking",
        outcome=lambda _ctx: "",
    ))
    await league.bot.change_queue.ask("blocker", {}, what="the blocker", origin=ChangeOrigin.BOT)
    await run_queue(league.bot)
    assert (await stopped_job(league.db_path))["name"] == "block"


def _pictures_on(league: Any, monkeypatch: Any, tmp_path: Any) -> list[str]:
    """Images on with the calendar aspect, the calendar drawn as a PNG; gives the divisions
    drawn, one entry per drawing."""
    from leaguebot.core.services import calendar_post_service

    league.bot.image_config_service.is_aspect_enabled = AsyncMock(
        side_effect=lambda aspect: aspect == "calendar"
    )
    drawn: list[str] = []

    async def _draw(_bot: Any, division: Any, *_args: Any, **_kwargs: Any) -> Any:
        drawn.append(division.name)
        png = tmp_path / f"calendar-{len(drawn)}.png"
        png.write_bytes(b"\x89PNG")
        return SimpleNamespace(problem=None, notices=[], png_paths=[png])

    monkeypatch.setattr(calendar_post_service, "render_calendar_image", _draw)
    return drawn


async def test_a_calendar_sync_is_acknowledged_and_updated_with_its_outcome(tmp_path):
    league = await ongoing_league(tmp_path)

    interaction = await _sync(league)
    acknowledged = _acknowledged(interaction)
    await run_queue(league.bot)

    assert acknowledged.startswith("⏳ Posting the calendar of **Pro** again.")
    assert _outcome(interaction) == "✅ Calendar for **Pro** reposted as text."
    assert len(_calendar_texts(league)) == 1
    [line] = [line for line in league.bot.log_channel.sent
              if "| /division calendar-sync | Success" in line]
    assert "division: Pro" in line
    assert "posted as: text" in line


@pytest.mark.parametrize("failure", [
    pytest.param("send refused", id="send refused"),
    pytest.param("id not saved", id="its id cannot be saved"),
])
async def test_a_calendar_sync_that_cannot_post_stops_the_queue_and_queues_nothing_on_the_old_retry_queue(
    tmp_path, failure,
):
    """Discord refuses the calendar, or the new calendar's id cannot be saved once it is sent:
    the queue stops at the post, and no textual calendar waits on the old retry queue to land
    later, untracked, beside the old one."""
    league = await ongoing_league(tmp_path)
    if failure == "send refused":
        league.channel(PRO_CALENDAR).send_fails = http_error(
            discord.Forbidden, status=403, text="Missing Access"
        )
    else:
        await league.write(
            "CREATE TRIGGER refuse_calendar_id BEFORE UPDATE OF calendar_message_id ON divisions "
            "BEGIN SELECT RAISE(ABORT, 'the disk is full'); END"
        )

    await _sync(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_calendar"
    assert await _retried_posts(league) == []


async def test_a_retried_calendar_sync_posts_the_text(tmp_path, monkeypatch):
    """Images on: Discord refuses the picture, and the queue stops; Retry posts the textual
    calendar, drawing nothing again."""
    league = await ongoing_league(tmp_path, images=True)
    drawn = _pictures_on(league, monkeypatch, tmp_path)
    league.channel(PRO_CALENDAR).fail_when = lambda _content, kwargs: bool(
        kwargs.get("file") or kwargs.get("files")
    )

    interaction = await _sync(league)
    await run_queue(league.bot)
    assert (await stopped_job(league.db_path))["name"] == "post_calendar"

    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert drawn == ["Pro"]
    assert len(_calendar_texts(league)) == 1
    assert _outcome(interaction) == "✅ Calendar for **Pro** reposted as text."


async def test_a_calendar_sync_whose_render_fails_posts_nothing_and_says_the_old_calendar_stands(
    tmp_path, monkeypatch,
):
    """A commanded posting: the render failing posts nothing and deletes nothing (XIV.7)."""
    from leaguebot.core.services import calendar_post_service

    league = await ongoing_league(tmp_path, images=True)
    league.bot.image_config_service.is_aspect_enabled = AsyncMock(
        side_effect=lambda aspect: aspect == "calendar"
    )
    monkeypatch.setattr(
        calendar_post_service, "render_calendar_image",
        AsyncMock(side_effect=RuntimeError("the template has no rows")),
    )

    interaction = await _sync(league)
    await run_queue(league.bot)

    assert league.texts(PRO_CALENDAR) == []
    assert CALENDAR_MESSAGE in league.channel(PRO_CALENDAR).messages
    assert await _calendar_id(league) == str(CALENDAR_MESSAGE)
    assert _outcome(interaction) == (
        "❌ The calendar for **Pro** was not posted — the template has no rows\n"
        "Nothing was deleted; the previous calendar still stands."
    )
    refusals = [line for line in league.bot.log_channel.sent
                if "`/division calendar-sync` refused for" in line]
    assert len(refusals) == 1
    assert "the template has no rows" in refusals[0]


async def test_a_calendar_sync_asked_before_the_season_is_done_is_refused_when_it_runs(tmp_path):
    """The queue stopped ahead of the sync, and every division finished meanwhile: once the
    queue moves, the sync is refused in the command's own words, and nothing is posted."""
    from leaguebot.core.utils.season_gate import stage_refusal

    league = await ongoing_league(tmp_path)
    await _queue_stopped_ahead(league)
    interaction = await _sync(league)
    await league.write(
        "UPDATE seasons SET stage = ?, status = ? WHERE id = ?",
        SeasonStage.PENDING_COMPLETION.value,
        status_of_stage(SeasonStage.PENDING_COMPLETION).value, SEASON_ID,
    )

    await discard_job(league.bot)

    refusal = stage_refusal(SeasonStage.PENDING_COMPLETION, "division calendar-sync")
    assert refusal is not None
    assert refusal in _outcome(interaction)
    assert _calendar_texts(league) == []
    assert [row["state"] for row in await change_rows(league.db_path)
            if row["kind"] == KIND] == ["REFUSED"]


async def test_a_second_calendar_sync_is_not_refused_as_a_repeat(tmp_path):
    """Two syncs asked while the queue is stopped: a repost reads what it posts when it runs,
    so the second is queued too."""
    league = await ongoing_league(tmp_path)
    await _queue_stopped_ahead(league)

    await _sync(league)
    await _sync(league)

    assert [row["state"] for row in await change_rows(league.db_path)
            if row["kind"] == KIND] == ["QUEUED", "QUEUED"]


async def test_a_discarded_calendar_sync_says_the_previous_calendar_still_stands(tmp_path):
    league = await ongoing_league(tmp_path)
    league.channel(PRO_CALENDAR).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )

    interaction = await _sync(league)
    await run_queue(league.bot)
    await discard_job(league.bot)

    assert _outcome(interaction).startswith(
        "⛔ The calendar for **Pro** was not posted: a league admin discarded it. The previous "
        "calendar still stands; run `/division calendar-sync` again."
    )
    assert CALENDAR_MESSAGE in league.channel(PRO_CALENDAR).messages
    assert await _calendar_id(league) == str(CALENDAR_MESSAGE)
