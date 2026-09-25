"""Conveying a division's calendar, as a graphic where one can be produced.

Issue #208. `post_division_calendar` and `replace_calendar_message` were partly uncovered.
Between them they decide what a division's calendar channel holds, and both carry ordering
rules that are invisible until they are broken.

**Post the replacement, then delete what it replaces — in that order.** The previous message
goes only once its replacement is up, so a failure can never leave the channel with no calendar
at all. `test_the_previous_calendar_goes_only_after_the_new_one_is_up` is what holds it, and the
reverse order is the obvious "tidy" rewrite.

**A commanded posting refuses; an uncommanded one falls back to the text** (Constitution
XIV.7). When a manager asks for a calendar and the template will not draw, they are told what is
wrong and nothing is posted — they are the one person able to fix it. When approval or a
schedule reaches the same fault, the division gets the textual calendar, because the calendar is
not the thing being commanded and a division with no calendar at all is the worse outcome.

**A graphic carries no message text.** The picture draws the division's own name and says what
it is, so a line above it repeats the picture rather than introducing it. The textual calendar
keeps its heading, where it is the only thing naming the list.

**A Discord fault sends the *textual* calendar to the retry queue** (FR-020). The graphic is
gone by the time the retry runs — the file is discarded in the `finally` — and a retry that
could not attach anything would post an empty message. This is the one place the two kinds of
failure are told apart: a render fault is reported, a posting fault is retried.

**The rendered file is discarded whichever way this ends.** Posted, refused to a commanded
caller, or lost to a Discord fault — the `finally` covers all three, because a rehearsal that
leaves its renders behind fills the Pi's disk.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.calendar_post_service import (
    post_division_calendar,
    replace_calendar_message,
)

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


def _channel(*, send_fails: bool = False, delete_fails: bool = False):
    channel = MagicMock()
    channel.id = CALENDAR_CHANNEL
    posted = MagicMock()
    posted.id = NEW_MESSAGE_ID
    channel.send = AsyncMock(
        side_effect=RuntimeError("Discord is down") if send_fails else None,
        return_value=posted,
    )
    partial = MagicMock()
    partial.delete = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=404), "gone")
        if delete_fails
        else None
    )
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
    db_path = await _make_db(tmp_path, name="cal_text")

    result, channel, _, _ = await _post(db_path, wanted=False)

    assert result.problem is None
    assert result.posted_as_image is False
    assert "Pro" in _sent_text(channel)


async def test_the_textual_calendar_lists_the_rounds(tmp_path):
    db_path = await _make_db(tmp_path, name="cal_text_rounds")

    _, channel, _, _ = await _post(
        db_path, wanted=False, rounds=[_round(1), _round(2)]
    )

    text = _sent_text(channel)
    assert "Silverstone" in text


async def test_the_new_message_id_is_stored(tmp_path):
    """The next posting deletes by it; without it the channel accumulates a calendar per
    round added."""
    db_path = await _make_db(tmp_path, name="cal_store")

    result, _, _, _ = await _post(db_path)

    assert result.message_id == NEW_MESSAGE_ID
    assert await _stored_message_id(db_path) == str(NEW_MESSAGE_ID)


# ---------------------------------------------------------------------------
# The graphic
# ---------------------------------------------------------------------------


async def test_a_rendered_calendar_is_posted_as_an_attachment(tmp_path):
    db_path = await _make_db(tmp_path, name="cal_image")
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")

    result, channel, _, _ = await _post(
        db_path, wanted=True, outcome=_outcome(png=png)
    )

    assert result.posted_as_image is True
    assert channel.send.await_args.kwargs.get("file") is not None


async def test_a_graphic_carries_no_message_text(tmp_path):
    """The picture draws the division's own name and says what it is; a line above it
    repeats the picture rather than introducing it."""
    db_path = await _make_db(tmp_path, name="cal_notext")
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")

    _, channel, _, _ = await _post(db_path, wanted=True, outcome=_outcome(png=png))

    assert channel.send.await_args.args[0] is None


async def test_a_render_notice_is_carried_back(tmp_path):
    """A calendar that drew but dropped a round off the end is a notice, and the caller
    reports it to the manager."""
    db_path = await _make_db(tmp_path, name="cal_notice")
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")

    result, _, _, _ = await _post(
        db_path, wanted=True, outcome=_outcome(png=png, notices=["round 12 did not fit"])
    )

    assert result.notices == ["round 12 did not fit"]


async def test_the_rendered_file_is_discarded_after_posting(tmp_path):
    """A rehearsal that leaves its renders behind fills the Pi's disk."""
    db_path = await _make_db(tmp_path, name="cal_discard")
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")

    _, _, discard, _ = await _post(db_path, wanted=True, outcome=_outcome(png=png))

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
    fix it, rather than handed a text calendar they did not ask for."""
    db_path = await _make_db(tmp_path, name="cal_commanded")

    result, channel, _, _ = await _post(
        db_path,
        wanted=True,
        outcome=_outcome(problem="the template has no rows"),
        commanded=True,
    )

    assert result.problem == "the template has no rows"
    channel.send.assert_not_awaited()


async def test_a_commanded_refusal_deletes_nothing(tmp_path):
    """The standing calendar is still the best thing in the channel, and removing it for a
    replacement that was never posted would leave the division with none."""
    db_path = await _make_db(tmp_path, name="cal_commanded_keep")

    _, channel, _, _ = await _post(
        db_path,
        division=_division(message_id=OLD_MESSAGE_ID),
        wanted=True,
        outcome=_outcome(problem="no template"),
        commanded=True,
    )

    channel.get_partial_message.assert_not_called()


async def test_a_commanded_refusal_still_discards_the_render(tmp_path):
    """The `finally` covers the refusal too — a file that was drawn and not posted is
    exactly as much clutter as one that was."""
    db_path = await _make_db(tmp_path, name="cal_commanded_discard")

    _, _, discard, _ = await _post(
        db_path, wanted=True, outcome=_outcome(problem="no template"), commanded=True
    )

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


async def test_a_posting_failure_enqueues_the_textual_calendar(tmp_path):
    """FR-020. The graphic is gone by the time the retry runs — the file is discarded in
    the `finally` — and a retry that could not attach anything would post an empty
    message."""
    db_path = await _make_db(tmp_path, name="cal_retry")

    result, _, _, enqueue = await _post(db_path, channel=_channel(send_fails=True))

    enqueue.assert_awaited_once()
    assert "Pro" in str(enqueue.await_args.args[2])
    assert result.message_id is None


async def test_a_failed_graphic_post_enqueues_text_rather_than_a_picture(tmp_path):
    """Which is the whole point of the distinction: a render fault is reported, a posting
    fault is retried, and only one of the two can be retried as an attachment."""
    db_path = await _make_db(tmp_path, name="cal_retry_image")
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")

    _, _, _, enqueue = await _post(
        db_path,
        wanted=True,
        outcome=_outcome(png=png),
        channel=_channel(send_fails=True),
    )

    assert "Silverstone" in str(enqueue.await_args.args[2])


async def test_a_failing_retry_queue_does_not_raise(tmp_path):
    """Called from an approval; a failure here would fail a season for the want of a
    retry row."""
    db_path = await _make_db(tmp_path, name="cal_retry_fails")
    channel = _channel(send_fails=True)

    with patch(
        "leaguebot.core.services.calendar_post_service.image_calendar_wanted",
        new=AsyncMock(return_value=False),
    ), patch("leaguebot.image.services.image_render_service.discard_render", new=MagicMock()), patch(
        "leaguebot.core.services.retry_service.enqueue", new=AsyncMock(side_effect=RuntimeError("no db"))
    ):
        result = await post_division_calendar(
            _bot(db_path), _guild(channel), _division(), [_round()], {}
        )

    assert result.problem is not None


async def test_a_division_with_no_calendar_channel_is_reported(tmp_path):
    """Configuring one is optional, and the caller reports it rather than failing."""
    db_path = await _make_db(tmp_path, name="cal_nochannel")

    result, channel, _, _ = await _post(db_path, guild=_guild(missing=True))

    assert "no calendar channel is configured" in result.problem
    channel.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Replacing the standing calendar
# ---------------------------------------------------------------------------


async def test_the_previous_calendar_goes_only_after_the_new_one_is_up(tmp_path):
    """The ordering is the contract: a failure can never leave the channel with no
    calendar at all."""
    db_path = await _make_db(tmp_path, name="cal_order")
    channel = _channel()
    order: list[str] = []
    channel.send = AsyncMock(
        side_effect=lambda *a, **k: order.append("send")
        or SimpleNamespace(id=NEW_MESSAGE_ID)
    )
    channel._partial.delete = AsyncMock(side_effect=lambda: order.append("delete"))

    await replace_calendar_message(
        _bot(db_path),
        channel,
        DIVISION_ID,
        content="calendar",
        image_path=None,
        previous_message_id=OLD_MESSAGE_ID,
    )

    assert order == ["send", "delete"]


async def test_a_failed_post_deletes_nothing(tmp_path):
    """The standing calendar is the best thing the channel has until a replacement is
    actually up."""
    db_path = await _make_db(tmp_path, name="cal_faildelete")
    channel = _channel(send_fails=True)

    with pytest.raises(RuntimeError):
        await replace_calendar_message(
            _bot(db_path),
            channel,
            DIVISION_ID,
            content="calendar",
            image_path=None,
            previous_message_id=OLD_MESSAGE_ID,
        )

    channel._partial.delete.assert_not_awaited()


async def test_a_first_calendar_deletes_nothing(tmp_path):
    """There is no previous message, and asking Discord to delete message `None` would
    raise."""
    db_path = await _make_db(tmp_path, name="cal_first")
    channel = _channel()

    await replace_calendar_message(
        _bot(db_path),
        channel,
        DIVISION_ID,
        content="calendar",
        image_path=None,
        previous_message_id=None,
    )

    channel.get_partial_message.assert_not_called()


async def test_a_message_that_is_its_own_replacement_is_not_deleted(tmp_path):
    """An edit-in-place would otherwise delete the message it had just posted."""
    db_path = await _make_db(tmp_path, name="cal_same")
    channel = _channel()

    await replace_calendar_message(
        _bot(db_path),
        channel,
        DIVISION_ID,
        content="calendar",
        image_path=None,
        previous_message_id=NEW_MESSAGE_ID,
    )

    channel.get_partial_message.assert_not_called()


async def test_a_previous_message_already_gone_does_not_fail_the_replacement(tmp_path):
    """The replacement is already up; a stale or hand-deleted message is not worth failing
    over, and the stored id is overwritten either way."""
    db_path = await _make_db(tmp_path, name="cal_delgone")
    channel = _channel(delete_fails=True)

    message_id = await replace_calendar_message(
        _bot(db_path),
        channel,
        DIVISION_ID,
        content="calendar",
        image_path=None,
        previous_message_id=OLD_MESSAGE_ID,
    )

    assert message_id == NEW_MESSAGE_ID
    assert await _stored_message_id(db_path) == str(NEW_MESSAGE_ID)


async def test_the_attachment_is_released_before_the_caller_removes_the_file(tmp_path):
    """Closed where the attachment is, so the handle is gone before the caller's `finally`
    removes the path — Windows refuses to delete a file that is still open."""
    db_path = await _make_db(tmp_path, name="cal_release")
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")
    channel = _channel()

    with patch("leaguebot.image.services.image_render_service.discard_attachment") as discard:
        await replace_calendar_message(
            _bot(db_path),
            channel,
            DIVISION_ID,
            content=None,
            image_path=Path(png),
            previous_message_id=None,
        )

    discard.assert_called_once()


async def test_the_attachment_is_released_even_when_the_post_fails(tmp_path):
    """The `finally` covers it, or a failed post leaves a file handle open for the life of
    the process."""
    db_path = await _make_db(tmp_path, name="cal_release_fail")
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")
    channel = _channel(send_fails=True)

    with patch("leaguebot.image.services.image_render_service.discard_attachment") as discard:
        with pytest.raises(RuntimeError):
            await replace_calendar_message(
                _bot(db_path),
                channel,
                DIVISION_ID,
                content=None,
                image_path=Path(png),
                previous_message_id=None,
            )

    discard.assert_called_once()
