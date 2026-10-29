"""Conveying a division's calendar, as a graphic where one can be produced.

Issue #208. `post_division_calendar` and `replace_calendar_message` were partly uncovered.
Between them they decide what a division's calendar channel holds, and both carry ordering
rules that are invisible until they are broken.

**Post the replacement; deleting what it replaces is the job after it** (#439, slice 6). The
functions here post and save nothing, deleting nothing: the calendar's post job saves the new id
and plans the deletion of the old one after itself (`tests/core/test_calendar_steps.py`), so a
failure can never leave the channel with no calendar at all.

**A commanded posting refuses; an uncommanded one falls back to the text** (Constitution
XIV.7). When a manager asks for a calendar and the template will not draw, they are told what is
wrong and nothing is posted — they are the one person able to fix it. When approval or a
schedule reaches the same fault, the division gets the textual calendar, because the calendar is
not the thing being commanded and a division with no calendar at all is the worse outcome.

**A graphic carries no message text.** The picture draws the division's own name and says what
it is, so a line above it repeats the picture rather than introducing it. The textual calendar
keeps its heading, where it is the only thing naming the list.

**A Discord fault raises, for the change queue to stop on** (#439, slice 6). The queue's next
try posts the textual calendar (Constitution XIV, rule 8). This is the one place the two kinds of
failure are told apart: a render fault is reported, a posting fault is retried.

**The rendered file is discarded whichever way this ends.** Posted, refused to a commanded
caller, or stopped by a Discord fault — the `finally` covers all three, because a rehearsal that
leaves its renders behind fills the Pi's disk.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.calendar_post_service import (
    post_division_calendar,
    replace_calendar_message,
)
from tests.support.change_queue import in_a_job

SERVER_ID = 12708
SEASON_ID = 1
DIVISION_ID = 11
CALENDAR_CHANNEL = 700
OLD_MESSAGE_ID = 8800
NEW_MESSAGE_ID = 9900


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "calendar") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, "
            "calendar_channel_id) VALUES (?, ?, 'Pro', 1, 555, ?)",
            (DIVISION_ID, SEASON_ID, CALENDAR_CHANNEL),
        )
        await db.commit()
    return db_path


def _division(*, channel=CALENDAR_CHANNEL, message_id=None):
    return SimpleNamespace(
        id=DIVISION_ID,
        name="Pro",
        tier=1,
        calendar_channel_id=channel,
        calendar_message_id=message_id,
    )


def _round(number: int = 1):
    from datetime import datetime, timedelta, timezone

    return SimpleNamespace(
        id=number,
        division_id=DIVISION_ID,
        round_number=number,
        track_name="Silverstone",
        status="NOT_RUN",
        format=SimpleNamespace(value="NORMAL"),
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=number * 7),
    )


def _channel(*, send_fails: bool = False):
    channel = MagicMock()
    channel.id = CALENDAR_CHANNEL
    posted = MagicMock()
    posted.id = NEW_MESSAGE_ID
    channel.send = AsyncMock(
        side_effect=RuntimeError("Discord is down") if send_fails else None,
        return_value=posted,
    )
    partial = MagicMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel._partial = partial
    return channel


def _guild(channel=None, *, missing: bool = False):
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=None if missing else channel)
    return guild


def _bot(db_path: str):
    bot = MagicMock()
    bot.db_path = db_path
    return bot


def _outcome(*, png=None, problem=None, notices=()):
    return SimpleNamespace(
        png_paths=[png] if png else [],
        problem=SimpleNamespace(detail=problem) if problem else None,
        notices=[SimpleNamespace(detail=n) for n in notices],
    )


async def _post(
    db_path,
    *,
    division=None,
    channel=None,
    guild=None,
    wanted: bool = False,
    outcome=None,
    render_error=None,
    commanded: bool = False,
    rounds=None,
):
    channel = channel if channel is not None else _channel()
    guild = guild if guild is not None else _guild(channel)
    with patch(
        "leaguebot.core.services.calendar_post_service.image_calendar_wanted",
        new=AsyncMock(return_value=wanted),
    ), patch(
        "leaguebot.core.services.calendar_post_service.render_calendar_image",
        new=AsyncMock(return_value=outcome or _outcome(), side_effect=render_error),
    ), patch(
        "leaguebot.image.services.image_render_service.discard_render", new=MagicMock()
    ) as discard, patch(
        "leaguebot.image.services.image_render_service.discard_attachment", new=MagicMock()
    ), patch(
        "leaguebot.core.services.retry_service.enqueue", new=AsyncMock()
    ) as enqueue:
        with in_a_job():
            result = await post_division_calendar(
                _bot(db_path),
                guild,
                division or _division(),
                rounds if rounds is not None else [_round()],
                {},
                commanded=commanded,
            )
    return result, channel, discard, enqueue


def _sent_text(channel) -> str:
    return str(channel.send.await_args.args[0]) if channel.send.await_args else ""


async def _stored_message_id(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT calendar_message_id FROM divisions WHERE id = ?", (DIVISION_ID,)
        )
        return (await cursor.fetchone())["calendar_message_id"]


# ---------------------------------------------------------------------------
# The textual calendar
# ---------------------------------------------------------------------------


async def test_a_league_without_the_graphic_gets_the_text(tmp_path):
    """The new message's id is returned and nothing is written: the calendar's post job saves
    the id in its own save; posting writes nothing."""
    db_path = await _make_db(tmp_path, name="cal_text")

    result, channel, _, _ = await _post(
        db_path, wanted=False, rounds=[_round(1), _round(2)]
    )

    assert result.problem is None
    assert result.posted_as_image is False
    assert "Pro" in _sent_text(channel)
    assert "Silverstone" in _sent_text(channel)
    assert result.message_id == NEW_MESSAGE_ID
    assert await _stored_message_id(db_path) is None


# ---------------------------------------------------------------------------
# The graphic
# ---------------------------------------------------------------------------


async def test_a_rendered_calendar_is_posted_as_an_attachment(tmp_path):
    """The graphic carries no message text: the picture draws the division's own name and
    says what it is, so a line above it repeats the picture rather than introducing it. A
    calendar that drew but dropped a round off the end is a notice, and the caller reports it
    to the manager. The rendered file is discarded after posting, because a rehearsal that
    leaves its renders behind fills the Pi's disk."""
    db_path = await _make_db(tmp_path, name="cal_image")
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")

    result, channel, discard, _ = await _post(
        db_path, wanted=True, outcome=_outcome(png=png, notices=["round 12 did not fit"])
    )

    assert result.posted_as_image is True
    assert channel.send.await_args.kwargs.get("file") is not None
    assert channel.send.await_args.args[0] is None
    assert result.notices == ["round 12 did not fit"]
    discard.assert_called_once()


# ---------------------------------------------------------------------------
# Commanded, and not
# ---------------------------------------------------------------------------


async def test_an_uncommanded_posting_falls_back_to_the_text(tmp_path):
    """Approval or a schedule reached this, and a division with no calendar at all is the
    worse outcome — the calendar is not the thing being commanded (XIV.7)."""
    db_path = await _make_db(tmp_path, name="cal_fallback")

    result, channel, _, _ = await _post(
        db_path, wanted=True, outcome=_outcome(problem="the template has no rows")
    )

    assert result.problem == "the template has no rows"
    assert result.posted_as_image is False
    assert "Pro" in _sent_text(channel)


async def test_a_commanded_posting_refuses_and_posts_nothing(tmp_path):
    """A manager asked for a calendar; they are told what is wrong and given the chance to
    fix it, rather than handed a text calendar they did not ask for.

    It deletes nothing: the standing calendar is still the best thing in the channel, and
    removing it for a replacement that was never posted would leave the division with none.
    And it still discards the render: the `finally` covers the refusal too — a file that was
    drawn and not posted is exactly as much clutter as one that was."""
    db_path = await _make_db(tmp_path, name="cal_commanded")

    result, channel, discard, _ = await _post(
        db_path,
        division=_division(message_id=OLD_MESSAGE_ID),
        wanted=True,
        outcome=_outcome(problem="the template has no rows"),
        commanded=True,
    )

    assert result.problem == "the template has no rows"
    channel.send.assert_not_awaited()
    channel.get_partial_message.assert_not_called()
    discard.assert_called_once()


async def test_a_render_that_raises_is_reported_rather_than_propagated(tmp_path):
    """A render fault reaching the approval would fail a season over a picture."""
    db_path = await _make_db(tmp_path, name="cal_render_raises")

    result, channel, _, _ = await _post(
        db_path, wanted=True, render_error=RuntimeError("Inkscape is not installed")
    )

    assert "Inkscape is not installed" in result.problem
    assert "Pro" in _sent_text(channel)


# ---------------------------------------------------------------------------
# When posting itself fails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", [
    pytest.param("set and gone", id="set and gone"),
    pytest.param("never set", id="never set"),
])
async def test_a_division_with_no_calendar_channel_is_reported(tmp_path, channel):
    """A calendar channel never set is optional, and the caller reports it rather than failing.
    One set and no longer on the server raises for the change queue to stop on: a league can put
    it right and press Retry."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _make_db(tmp_path, name="cal_nochannel")

    if channel == "set and gone":
        with pytest.raises(StepFailedOnDiscord):
            await _post(db_path, guild=_guild(missing=True))
        return
    result, sent_to, _, _ = await _post(db_path, division=_division(channel=None),
                                        guild=_guild(missing=True))

    assert "no calendar channel is configured" in result.problem
    sent_to.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# The attachment
# ---------------------------------------------------------------------------


async def test_the_attachment_is_released_before_the_caller_removes_the_file(tmp_path):
    """Closed where the attachment is, so the handle is gone before the caller's `finally`
    removes the path — Windows refuses to delete a file that is still open."""
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")
    channel = _channel()

    with patch("leaguebot.image.services.image_render_service.discard_attachment") as discard:
        with in_a_job():
            await replace_calendar_message(channel, content=None, image_path=Path(png))

    discard.assert_called_once()


async def test_the_attachment_is_released_even_when_the_post_fails(tmp_path):
    """The `finally` covers it, or a failed post leaves a file handle open for the life of
    the process."""
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")
    channel = _channel(send_fails=True)

    with patch("leaguebot.image.services.image_render_service.discard_attachment") as discard:
        with pytest.raises(RuntimeError):
            with in_a_job():
                await replace_calendar_message(channel, content=None, image_path=Path(png))

    discard.assert_called_once()
