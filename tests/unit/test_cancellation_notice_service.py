"""What each module says when a round, a division or a season is called off (#175).

Before this, a cancellation posted one notice, to the forecast channel, whether or not the
weather module was on — so a league without weather heard nothing, and a league with it
switched off heard from a module that should have produced nothing. These tests pin each
module to its own channel and its own enabled state, and the calendar to being refreshed.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services import cancellation_notice_service as cns  # noqa: E402

SEASON_ID = 1
DIVISION_ID = 11
ROLE_ID = 555
FORECAST, RESULTS, RSVP = 701, 702, 703


async def _make_db(tmp_path, *, results=True, rsvp=True) -> str:
    db_path = os.path.join(str(tmp_path), "cancel.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, "
            "forecast_channel_id) VALUES (?, ?, 'Pro', 1, ?, ?)",
            (DIVISION_ID, SEASON_ID, ROLE_ID, FORECAST),
        )
        if results:
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id) "
                "VALUES (?, ?)",
                (DIVISION_ID, RESULTS),
            )
        if rsvp:
            await db.execute(
                "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
                "VALUES (?, ?)",
                (DIVISION_ID, str(RSVP)),
            )
        await db.commit()
    return db_path


def _bot(db_path, *, weather=True, results=True, attendance=True):
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather)
    bot.module_service.is_results_enabled = AsyncMock(return_value=results)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    return bot


def _guild(missing=()):
    channels = {cid: NS(id=cid, send=AsyncMock()) for cid in (FORECAST, RESULTS, RSVP)}
    guild = MagicMock()
    guild.get_channel = MagicMock(
        side_effect=lambda cid: None if cid in missing else channels.get(cid)
    )
    return guild, channels


def _division(message_id=None):
    return NS(
        id=DIVISION_ID, name="Pro", mention_role_id=ROLE_ID, calendar_message_id=message_id
    )


async def _announce(bot, guild, scope=cns.SCOPE_ROUND):
    return await cns.announce_cancellation(
        bot, guild, [_division()], scope=scope, round_number=3, track_name="Monza"
    )


# ── Each module in its own channel, only while enabled ─────────────────────


async def test_nothing_is_posted_to_the_forecast_channel_with_weather_off(tmp_path):
    """The defect itself: the notice went to the forecast channel whatever the module."""
    bot = _bot(await _make_db(tmp_path), weather=False)
    guild, channels = _guild()
    await _announce(bot, guild)
    channels[FORECAST].send.assert_not_awaited()


async def test_a_league_with_every_module_off_is_posted_nothing(tmp_path):
    bot = _bot(await _make_db(tmp_path), weather=False, results=False, attendance=False)
    guild, channels = _guild()
    failures = await _announce(bot, guild)
    for channel in channels.values():
        channel.send.assert_not_awaited()
    assert failures == []


@pytest.mark.parametrize(
    "module, channel_id",
    [("weather", FORECAST), ("results", RESULTS), ("attendance", RSVP)],
)
async def test_each_module_posts_only_to_its_own_channel(tmp_path, module, channel_id):
    flags = {"weather": False, "results": False, "attendance": False, module: True}
    bot = _bot(await _make_db(tmp_path), **flags)
    guild, channels = _guild()
    await _announce(bot, guild)
    for cid, channel in channels.items():
        if cid == channel_id:
            channel.send.assert_awaited_once()
        else:
            channel.send.assert_not_awaited()


async def test_weather_and_results_are_silent_notes(tmp_path):
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    await _announce(bot, guild)
    assert channels[FORECAST].send.await_args.kwargs.get("silent") is True
    assert channels[RESULTS].send.await_args.kwargs.get("silent") is True


async def test_the_check_in_channel_carries_the_notification(tmp_path):
    """Not silent, and it mentions the division role as the check-in call does."""
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    await _announce(bot, guild)
    call = channels[RSVP].send.await_args
    assert not call.kwargs.get("silent")
    assert call.args[0].startswith(f"<@&{ROLE_ID}>")
    assert call.kwargs["allowed_mentions"].roles is True
    assert "Round 3 Cancelled: Pro" in call.args[0]
    assert "no check-in to answer" in call.args[0]


@pytest.mark.parametrize("scope", [cns.SCOPE_ROUND, cns.SCOPE_DIVISION, cns.SCOPE_SEASON])
async def test_every_scope_says_something_in_every_enabled_module(tmp_path, scope):
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    assert await _announce(bot, guild, scope) == []
    for channel in channels.values():
        channel.send.assert_awaited_once()


def test_a_round_note_names_the_round_and_its_track():
    note = cns.weather_note(cns.SCOPE_ROUND, "Pro", round_number=3, track_name="Monza")
    assert "Round 3 (Monza)" in note
    assert "No weather forecast" in note
    assert "Round 3 (Mystery)" in cns.results_note(cns.SCOPE_ROUND, "Pro", round_number=3)


# ── Failures are gathered, not raised ──────────────────────────────────────


async def test_a_missing_channel_is_reported_and_the_rest_still_sent(tmp_path):
    bot = _bot(await _make_db(tmp_path, results=False))
    guild, channels = _guild()
    failures = await _announce(bot, guild)
    assert [(f.division_name, f.target) for f in failures] == [("Pro", "results channel")]
    assert failures[0].reason == "no channel is set"
    channels[FORECAST].send.assert_awaited_once()
    channels[RSVP].send.assert_awaited_once()


async def test_a_channel_that_cannot_be_found_is_reported(tmp_path):
    bot = _bot(await _make_db(tmp_path))
    guild, _ = _guild(missing=(FORECAST,))
    failures = await _announce(bot, guild)
    assert [(f.target, f.reason) for f in failures] == [
        ("forecast channel", "the channel could not be found")
    ]


async def test_a_failed_send_does_not_stop_the_others(tmp_path):
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    channels[RSVP].send = AsyncMock(side_effect=RuntimeError("forbidden"))
    failures = await _announce(bot, guild)
    assert [f.target for f in failures] == ["check-in channel"]
    assert "forbidden" in failures[0].reason
    channels[FORECAST].send.assert_awaited_once()
    channels[RESULTS].send.assert_awaited_once()


def test_failures_are_named_for_the_reply():
    lines = cns.failure_lines([cns.NoticeFailure("Pro", "results channel", "no channel is set")])
    assert "Not notified" in lines
    assert "**Pro** — results channel: no channel is set" in lines
    assert cns.failure_lines([]) == ""


# ── The calendar ───────────────────────────────────────────────────────────


async def test_a_calendar_never_posted_is_left_alone(tmp_path, monkeypatch):
    from services import calendar_post_service

    post = AsyncMock()
    monkeypatch.setattr(calendar_post_service, "post_division_calendar", post)
    bot = _bot(await _make_db(tmp_path))
    assert await cns.refresh_division_calendar(bot, MagicMock(), _division()) is None
    post.assert_not_awaited()


async def test_the_calendar_is_posted_again_with_the_round_cancelled(tmp_path, monkeypatch):
    from services import calendar_post_service

    post = AsyncMock(return_value=NS(message_id=42, problem=None))
    monkeypatch.setattr(calendar_post_service, "post_division_calendar", post)
    monkeypatch.setattr(calendar_post_service, "tracks_by_name", AsyncMock(return_value={}))
    bot = _bot(await _make_db(tmp_path))

    import dataclasses

    @dataclasses.dataclass
    class _Round:
        id: int
        status: str

    bot.season_service.get_division_rounds = AsyncMock(
        return_value=[_Round(1, "NOT_RUN"), _Round(2, "NOT_RUN"), _Round(3, "FINAL")]
    )
    reason = await cns.refresh_division_calendar(
        bot, MagicMock(), _division(message_id=9), also_cancelled=frozenset({2})
    )
    assert reason is None
    rounds = post.await_args.args[3]
    assert [r.status for r in rounds] == ["NOT_RUN", "CANCELLED", "FINAL"]
    # Not commanded: a graphic that cannot be drawn falls back to text (XIV.7).
    assert not post.await_args.kwargs.get("commanded")


async def test_a_calendar_that_could_not_be_posted_is_a_failure(tmp_path, monkeypatch):
    from services import calendar_post_service

    monkeypatch.setattr(
        calendar_post_service,
        "post_division_calendar",
        AsyncMock(return_value=NS(message_id=None, problem="forbidden")),
    )
    monkeypatch.setattr(calendar_post_service, "tracks_by_name", AsyncMock(return_value={}))
    bot = _bot(await _make_db(tmp_path), weather=False, results=False, attendance=False)
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])
    division = _division(message_id=9)
    failures = await cns.announce_cancellation(
        bot, MagicMock(), [division], scope=cns.SCOPE_DIVISION
    )
    assert [(f.target, f.reason) for f in failures] == [("calendar", "forbidden")]


async def test_a_calendar_that_raises_is_a_failure_not_an_error(tmp_path, monkeypatch):
    from services import calendar_post_service

    monkeypatch.setattr(
        calendar_post_service, "post_division_calendar", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(calendar_post_service, "tracks_by_name", AsyncMock(return_value={}))
    bot = _bot(await _make_db(tmp_path), weather=False, results=False, attendance=False)
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])
    failures = await cns.announce_cancellation(
        bot, MagicMock(), [_division(message_id=9)], scope=cns.SCOPE_SEASON
    )
    assert [(f.target, f.reason) for f in failures] == [("calendar", "boom")]
