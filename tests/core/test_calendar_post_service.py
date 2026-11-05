"""Unit tests for calendar posting — T025 / T032.

Covers:
  1. The enabled/disabled branch: a graphic where the module and aspect are both on,
     the traditional textual posting otherwise.
  2. Byte-identical textual output when the module is off (SC-006).
  3. The heading both forms share.
  4. Fallback to text on a fatal render for an *uncommanded* posting; rejection with
     nothing posted for a commanded one (XIV.7).
  5. Posting deletes nothing: the message a calendar replaces is deleted by a job after its
     post (#439, slice 6; `tests/core/test_calendar_steps.py`).
  6. Test mode does not suppress the replacement deletion (FR-017).

The calendar is drawn through the drawing the builder hands core (`CalendarDrawingStub` stands in
for it here); the image module's side of the drawing is tested against its implementation, in
`tests/image/test_image_drawing_calendar.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

import leaguebot.core.services.calendar_post_service as cps
from tests.support.calendar_drawing import CalendarDrawingStub
from tests.support.change_queue import in_a_job
from tests.support.mentions import NOBODY, notified

#: Why the tests of the posting fail before the build.
HANDED_NO_DRAWING = "#283: the calendar's posting is handed the bot, not the drawing"

TRACKS = {
    "Silverstone Circuit": NS(
        name="Silverstone Circuit", gp_name="British Grand Prix", country="United Kingdom"
    )
}


def _rounds(count=2):
    return [
        NS(
            round_number=index,
            format="NORMAL",
            track_name="Silverstone Circuit",
            scheduled_at=datetime(2026, 6, 4 + 7 * (index - 1), 20, 0, tzinfo=timezone.utc),
        )
        for index in range(1, count + 1)
    ]


def _division(**overrides):
    values = dict(
        id=7, name="Elite", tier=1, calendar_channel_id=999, calendar_message_id=None
    )
    values.update(overrides)
    return NS(**values)


def _channel(message_id=555):
    channel = MagicMock()
    channel.send = AsyncMock(return_value=NS(id=message_id))
    partial = MagicMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    return channel, partial


def _guild(channel):
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    return guild


# ── 1–3. The branch, and the shared heading ───────────────────────────────


def test_heading_is_the_textual_calendar_s_own():
    assert cps.calendar_heading("Elite") == "\U0001f4c5 **Elite — Race Calendar**"


@pytest.mark.asyncio
async def test_the_textual_calendar_keeps_its_heading(monkeypatch):
    """Withdrawing the graphic's heading must not strip the text's, where it is the only
    thing naming the list of rounds."""
    drawing = CalendarDrawingStub(wanted=False)
    channel, _ = _channel()
    monkeypatch.setattr(cps, "replace_calendar_message", AsyncMock(return_value=555))

    await cps.post_division_calendar(
        drawing, _guild(channel), _division(), _rounds(), TRACKS
    )

    assert drawing.drawn == []

    content = cps.replace_calendar_message.await_args.kwargs["content"]
    assert content.startswith(cps.calendar_heading("Elite"))


def test_textual_calendar_matches_the_posting_it_replaced():
    """SC-006: byte-identical while the module is off."""
    rounds = _rounds(2)
    expected = "\n".join(
        ["\U0001f4c5 **Elite — Race Calendar**"]
        + [
            f"Round {r.round_number}: {r.track_name} — "
            f"<t:{int(r.scheduled_at.timestamp())}:F>"
            for r in rounds
        ]
    )
    assert cps.textual_calendar("Elite", rounds) == expected


@pytest.fixture
def summer_time_host(monkeypatch):
    """The host's clock set to a zone an hour ahead of UTC in summer, as a Pi in Britain runs;
    restored after the test."""
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("setting the host's zone needs time.tzset, which this platform lacks")
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_a_round_stored_as_plain_utc_is_shown_at_its_utc_moment(summer_time_host):
    """Rounds are stored as plain UTC, with no zone. On a host in British Summer Time, a round
    at 20:00 UTC on 30 July shows that moment, not one an hour early."""
    import calendar

    rounds = [NS(round_number=4, track_name="Silverstone Circuit",
                 scheduled_at=datetime(2026, 7, 30, 20, 0))]
    epoch = calendar.timegm((2026, 7, 30, 20, 0, 0))

    assert f"Round 4: Silverstone Circuit — <t:{epoch}:F>" in cps.textual_calendar(
        "Elite", rounds
    )


def test_a_mystery_round_reads_as_mystery_in_the_text():
    rounds = [
        NS(
            round_number=9,
            track_name=None,
            scheduled_at=datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc),
        )
    ]
    assert "Round 9: Mystery — " in cps.textual_calendar("Elite", rounds)


# ── 4. Fallback vs rejection ──────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=HANDED_NO_DRAWING)
@pytest.mark.asyncio
async def test_uncommanded_posting_falls_back_to_text_on_a_fatal_render(monkeypatch):
    drawing = CalendarDrawingStub(problem="template is missing a field")
    channel, _ = _channel()
    monkeypatch.setattr(cps, "replace_calendar_message", AsyncMock(return_value=555))

    result = await cps.post_division_calendar(
        drawing, _guild(channel), _division(), _rounds(), TRACKS
    )

    assert drawing.drawn == ["Elite"]
    assert result.fell_back is True
    assert result.problem == "template is missing a field"
    assert result.posted_as_image is False
    posted_content = cps.replace_calendar_message.await_args.kwargs["content"]
    assert "Round 1:" in posted_content, "the textual calendar must stand in"


@pytest.mark.xfail(strict=True, reason=HANDED_NO_DRAWING)
@pytest.mark.asyncio
async def test_commanded_posting_is_rejected_and_posts_nothing(monkeypatch):
    """XIV.7 — the one person able to fix the template is told, not fobbed off."""
    drawing = CalendarDrawingStub(problem="bad template")
    channel, _ = _channel()
    replace = AsyncMock()
    monkeypatch.setattr(cps, "replace_calendar_message", replace)

    result = await cps.post_division_calendar(
        drawing, _guild(channel), _division(), _rounds(), TRACKS, commanded=True
    )

    assert result.problem == "bad template"
    assert result.message_id is None
    replace.assert_not_awaited()


# ── 6. Test mode ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_replacement_deletion_is_not_test_mode_suppressed(tmp_path):
    """FR-017 — the forecast flow's test-mode guard does not extend to a replacement."""
    import inspect

    source = inspect.getsource(cps.replace_calendar_message)
    body = source.split('"""')[-1]  # past the docstring, which discusses the guard
    assert "test_mode" not in body
    assert "flush_pending_deletions" not in body
    assert "forecast_cleanup_service" not in body


# ── A cancelled round (#175) ──────────────────────────────────────────────


def test_a_cancelled_round_is_struck_through_in_the_text():
    """The calendar keeps the round the league planned, and says it was called off; a round
    still on beside it is not struck through."""
    rounds = _rounds(2)
    rounds[0].status = "NOT_RUN"
    rounds[1].status = "CANCELLED"
    unix = int(rounds[1].scheduled_at.timestamp())
    text = cps.textual_calendar("Elite", rounds)
    assert (
        f"~~Round {rounds[1].round_number}: {rounds[1].track_name} — <t:{unix}:F>~~ — Cancelled"
        in text.splitlines()
    )
    assert "~~" not in text.splitlines()[1]
    assert "Cancelled" not in text.splitlines()[1]


# ── The form a job on the change queue calls (#439, slices 4a and 6) ─────

def _discord_refusal():
    import discord

    return discord.HTTPException(MagicMock(status=500, reason="Server Error"), "refused")


@pytest.mark.parametrize("fault", [
    pytest.param("channel gone"),
    pytest.param("send refused"),
])
@pytest.mark.asyncio
async def test_a_raising_post_raises_and_queues_no_retry(tmp_path, monkeypatch, fault):
    """A job on the queue stops where the calendar cannot be posted, rather than handing the
    text to the old retry queue: the channel set and no longer on the server, or Discord
    refusing the send."""
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.core.services import retry_service

    enqueue = AsyncMock()
    monkeypatch.setattr(retry_service, "enqueue", enqueue)
    channel, partial = _channel()
    if fault == "channel gone":
        guild = _guild(None)
    else:
        channel.send = AsyncMock(side_effect=_discord_refusal())
        guild = _guild(channel)

    with pytest.raises(StepFailedOnDiscord):
        with in_a_job():
            await cps.post_division_calendar(
                CalendarDrawingStub(wanted=False), guild, _division(calendar_message_id=111),
                _rounds(), TRACKS,
            )

    enqueue.assert_not_awaited()
    partial.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_post_as_text_draws_nothing(monkeypatch):
    """A retried post goes as text (Constitution XIV rule 8): with the league drawing its
    calendar, nothing is drawn and the textual calendar is posted."""
    drawing = CalendarDrawingStub(wanted=True)
    monkeypatch.setattr(cps, "replace_calendar_message", AsyncMock(return_value=555))
    channel, _ = _channel()

    result = await cps.post_division_calendar(
        drawing, _guild(channel), _division(), _rounds(), TRACKS, as_text=True,
    )

    assert drawing.drawn == []
    kwargs = cps.replace_calendar_message.await_args.kwargs
    assert kwargs["image_path"] is None
    assert kwargs["content"] == cps.textual_calendar("Elite", _rounds())
    assert result.posted_as_image is False


@pytest.mark.parametrize("as_graphic", [False, True], ids=["text", "graphic"])
async def test_the_calendar_post_notifies_nobody(tmp_path, as_graphic):
    """Elite's calendar is posted to its calendar channel, as text or as a graphic. The post
    notifies nobody, and gives back the new message's id."""
    png = tmp_path / "calendar.png"
    png.write_bytes(b"\x89PNG")
    channel, _ = _channel(message_id=555)
    with in_a_job():
        posted = await cps.replace_calendar_message(
            channel,
            content=None if as_graphic else cps.calendar_heading("Elite"),
            image_path=png if as_graphic else None,
        )

    assert posted == 555
    assert notified(channel.send.await_args.kwargs.get("allowed_mentions")) == NOBODY
