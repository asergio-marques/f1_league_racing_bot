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
    failures = (await _announce(bot, guild)).failures
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
    assert (await _announce(bot, guild, scope)).failures == []
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
    failures = (await _announce(bot, guild)).failures
    assert [(f.division_name, f.target) for f in failures] == [("Pro", "results channel")]
    assert failures[0].reason == "no channel is set"
    channels[FORECAST].send.assert_awaited_once()
    channels[RSVP].send.assert_awaited_once()


async def test_a_channel_that_cannot_be_found_is_reported(tmp_path):
    bot = _bot(await _make_db(tmp_path))
    guild, _ = _guild(missing=(FORECAST,))
    failures = (await _announce(bot, guild)).failures
    assert [(f.target, f.reason) for f in failures] == [
        ("forecast channel", "the channel could not be found")
    ]


async def test_a_failed_send_does_not_stop_the_others(tmp_path):
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    channels[RSVP].send = AsyncMock(side_effect=RuntimeError("forbidden"))
    failures = (await _announce(bot, guild)).failures
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

    post = AsyncMock(
        return_value=calendar_post_service.CalendarPosting(division_id=DIVISION_ID, message_id=42)
    )
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
        bot, MagicMock(), _division(message_id=9), round_ids=frozenset({2})
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
        AsyncMock(return_value=calendar_post_service.CalendarPosting(
            division_id=DIVISION_ID, problem="forbidden"
        )),
    )
    monkeypatch.setattr(calendar_post_service, "tracks_by_name", AsyncMock(return_value={}))
    bot = _bot(await _make_db(tmp_path), weather=False, results=False, attendance=False)
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])
    division = _division(message_id=9)
    failures = (await cns.announce_cancellation(
        bot, MagicMock(), [division], scope=cns.SCOPE_DIVISION
    )).failures
    assert [(f.target, f.reason) for f in failures] == [("calendar", "forbidden")]


async def test_a_calendar_that_raises_is_a_failure_not_an_error(tmp_path, monkeypatch):
    from services import calendar_post_service

    monkeypatch.setattr(
        calendar_post_service, "post_division_calendar", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(calendar_post_service, "tracks_by_name", AsyncMock(return_value={}))
    bot = _bot(await _make_db(tmp_path), weather=False, results=False, attendance=False)
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])
    failures = (await cns.announce_cancellation(
        bot, MagicMock(), [_division(message_id=9)], scope=cns.SCOPE_SEASON
    )).failures
    assert [(f.target, f.reason) for f in failures] == [("calendar", "boom")]


# ── Nothing escapes: the cancellation is part-done when this runs ──────────


async def test_a_module_state_that_cannot_be_read_is_a_failure_not_an_error(tmp_path):
    """Before the cascade of a season an exception here would leave it half-cancelled."""
    bot = _bot(await _make_db(tmp_path))
    bot.module_service.is_weather_enabled = AsyncMock(side_effect=RuntimeError("db locked"))
    guild, _ = _guild()
    failures = (await _announce(bot, guild, cns.SCOPE_SEASON)).failures
    assert [(f.target, f.reason) for f in failures] == [("the announcement", "db locked")]


async def test_channels_that_cannot_be_read_still_leave_the_calendar_refreshed(
    tmp_path, monkeypatch
):
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    monkeypatch.setattr(cns, "_module_channels", AsyncMock(side_effect=RuntimeError("gone")))
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr(cns, "refresh_division_calendar", refresh)
    failures = (await _announce(bot, guild)).failures
    assert [f.target for f in failures] == ["its module channels"]
    refresh.assert_awaited_once()
    for channel in channels.values():
        channel.send.assert_not_awaited()


async def test_a_calendar_that_fell_back_to_text_is_named(tmp_path, monkeypatch):
    """Posted, but not as the league asked: the admin should know the picture failed."""
    from services import calendar_post_service

    monkeypatch.setattr(
        calendar_post_service,
        "post_division_calendar",
        AsyncMock(return_value=calendar_post_service.CalendarPosting(
            division_id=DIVISION_ID, message_id=42, problem="template invalid"
        )),
    )
    monkeypatch.setattr(calendar_post_service, "tracks_by_name", AsyncMock(return_value={}))
    bot = _bot(await _make_db(tmp_path), weather=False, results=False, attendance=False)
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])
    failures = (await cns.announce_cancellation(
        bot, MagicMock(), [_division(message_id=9)], scope=cns.SCOPE_ROUND, round_number=1
    )).failures
    assert [f.target for f in failures] == ["calendar"]
    assert "posted as text" in failures[0].reason
    assert "template invalid" in failures[0].reason


# ── The reply fits one message; the log carries everything ─────────────────


def test_a_long_list_of_failures_is_summed_up_in_the_reply():
    failures = [
        cns.NoticeFailure(f"Division {n}", "results channel", "x" * 500) for n in range(30)
    ]
    lines = cns.failure_lines(failures)
    assert len(lines) < 2000
    assert "and 22 more" in lines
    assert lines.count("  • ") == cns.MAX_LINES + 1


def test_the_log_names_every_failure_in_full():
    failures = [cns.NoticeFailure(f"Division {n}", "results channel", "gone") for n in range(30)]
    logged = cns.failure_log_lines(failures)
    assert logged.count("not notified: ") == 30
    assert cns.failure_log_lines([]) == ""


# ── The check-in call of a round called off comes down; its answers stay ───

ROUND_ID = 31
CALL_MSG, LAST_MSG, DIST_MSG = 9001, 9002, 9003


async def _with_call(db_path, *, round_id=ROUND_ID) -> None:
    """A round of the division whose check-in call is posted, with two answers recorded."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at)"
            " VALUES (?, ?, 3, 'NORMAL', 'Monza', '2026-10-01T18:00:00')",
            (round_id, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id,"
            " posted_at, last_notice_msg_id, distribution_msg_id)"
            " VALUES (?, ?, ?, ?, '2026-09-26T18:00:00', ?, ?)",
            (round_id, DIVISION_ID, str(CALL_MSG), str(RSVP), str(LAST_MSG), str(DIST_MSG)),
        )
        for profile_id, user_id, status in ((1, "101", "ACCEPTED"), (2, "102", "DECLINED")):
            await db.execute(
                "INSERT OR IGNORE INTO driver_profiles (id, discord_user_id, current_state)"
                " VALUES (?, ?, 'ASSIGNED')",
                (profile_id, user_id),
            )
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id,"
                " rsvp_status) VALUES (?, ?, ?, ?)",
                (round_id, DIVISION_ID, profile_id, status),
            )
        await db.commit()


def _call_channel(bot, *, refuse=False):
    """The check-in channel as `withdraw_rsvp_call` reaches it, recording what it deletes."""
    import discord

    deleted: list[int] = []

    async def _fetch(message_id):
        message = MagicMock()

        async def _delete():
            if refuse:
                raise discord.Forbidden(MagicMock(status=403), "nope")
            deleted.append(message_id)

        message.delete = _delete
        return message

    channel = MagicMock()
    channel.fetch_message = _fetch
    bot.get_channel = MagicMock(return_value=channel)
    return deleted


def _with_attendance(bot):
    from services.attendance_service import AttendanceService

    bot.attendance_service = AttendanceService(bot.db_path)
    return bot


async def _rows(db_path, table):
    async with get_connection(db_path) as db:
        return await (await db.execute(f"SELECT * FROM {table}")).fetchall()  # noqa: S608


async def test_the_call_its_notice_and_its_distribution_come_down(tmp_path):
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    deleted = _call_channel(bot)
    guild, _ = _guild()

    report = await cns.announce_cancellation(
        bot, guild, [_division()], scope=cns.SCOPE_ROUND, round_number=3,
        round_ids=frozenset({ROUND_ID}),
    )

    assert report.failures == []
    assert sorted(deleted) == [CALL_MSG, LAST_MSG, DIST_MSG]
    assert await _rows(db_path, "rsvp_embed_messages") == []


async def test_the_answers_are_kept(tmp_path):
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    _call_channel(bot)
    guild, _ = _guild()

    await cns.announce_cancellation(
        bot, guild, [_division()], scope=cns.SCOPE_ROUND, round_ids=frozenset({ROUND_ID})
    )

    rows = await _rows(db_path, "driver_round_attendance")
    assert sorted((r["driver_profile_id"], r["rsvp_status"]) for r in rows) == [
        (1, "ACCEPTED"), (2, "DECLINED"),
    ]


async def test_the_call_comes_down_even_where_the_notice_could_not_be_posted(tmp_path):
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    deleted = _call_channel(bot)
    guild, _ = _guild(missing=(RSVP,))

    report = await cns.announce_cancellation(
        bot, guild, [_division()], scope=cns.SCOPE_ROUND, round_ids=frozenset({ROUND_ID})
    )

    assert [f.target for f in report.failures] == ["check-in channel"]
    assert CALL_MSG in deleted


async def test_with_attendance_off_the_call_is_left_alone(tmp_path):
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path, attendance=False))
    deleted = _call_channel(bot)
    guild, _ = _guild()

    await cns.announce_cancellation(
        bot, guild, [_division()], scope=cns.SCOPE_ROUND, round_ids=frozenset({ROUND_ID})
    )

    assert deleted == []
    assert len(await _rows(db_path, "rsvp_embed_messages")) == 1


async def test_a_round_this_cancellation_does_not_call_off_keeps_its_call(tmp_path):
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    deleted = _call_channel(bot)
    guild, _ = _guild()

    await cns.announce_cancellation(
        bot, guild, [_division()], scope=cns.SCOPE_DIVISION, round_ids=frozenset({999})
    )

    assert deleted == []
    assert len(await _rows(db_path, "rsvp_embed_messages")) == 1


async def test_a_call_that_cannot_be_taken_down_is_a_failure_not_an_error(
    tmp_path, monkeypatch
):
    from services import rsvp_service

    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    monkeypatch.setattr(
        rsvp_service, "withdraw_rsvp_call", AsyncMock(side_effect=RuntimeError("db locked"))
    )
    guild, channels = _guild()

    report = await cns.announce_cancellation(
        bot, guild, [_division()], scope=cns.SCOPE_ROUND, round_ids=frozenset({ROUND_ID})
    )

    assert [(f.target, f.reason) for f in report.failures] == [
        ("check-in call", "could not be taken down (db locked)")
    ]
    channels[FORECAST].send.assert_awaited_once()


# ── The check-in is audited in the log before the call comes down ──────────


async def _distribute(db_path) -> None:
    """Two reserves, one sent to a team and one left standing by."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name) VALUES (51, ?, 'Ferrari')",
            (DIVISION_ID,),
        )
        for profile_id, user_id, team, standby in ((3, "103", 51, 0), (4, "104", None, 1)):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
                "is_test_driver, test_display_name) VALUES (?, ?, 'ASSIGNED', 1, ?)",
                (profile_id, user_id, f"Reserve {profile_id}"),
            )
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id,"
                " rsvp_status, assigned_team_id, is_standby) VALUES (?, ?, ?, 'ACCEPTED', ?, ?)",
                (ROUND_ID, DIVISION_ID, profile_id, team, standby),
            )
        await db.commit()


async def _audit(tmp_path, *, distribute=False, attendance=True, round_ids=(ROUND_ID,)):
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    if distribute:
        await _distribute(db_path)
    bot = _with_attendance(_bot(db_path, attendance=attendance))
    _call_channel(bot)
    guild, _ = _guild()
    return await cns.announce_cancellation(
        bot, guild, [_division()], scope=cns.SCOPE_ROUND, round_ids=frozenset(round_ids)
    )


async def test_the_audit_groups_the_drivers_by_their_answer(tmp_path):
    report = await _audit(tmp_path)
    assert report.audit == (
        "\n  check-in, Pro, Round 3 (Monza):"
        "\n    accepted: <@101>"
        "\n    tentative: none"
        "\n    declined: <@102>"
        "\n    no answer: none"
    )


async def test_the_audit_names_the_reserves_once_they_are_distributed(tmp_path):
    report = await _audit(tmp_path, distribute=True)
    assert "\n    accepted: <@101>, <@103> (Reserve 3), <@104> (Reserve 4)" in report.audit
    assert report.audit.endswith(
        "\n    reserves: <@103> (Reserve 3) to Ferrari, <@104> (Reserve 4) on standby"
    )


async def test_the_audit_says_nothing_of_reserves_before_a_distribution(tmp_path):
    report = await _audit(tmp_path)
    assert "reserves" not in report.audit


async def test_a_round_whose_call_never_went_out_is_not_audited(tmp_path):
    report = await _audit(tmp_path, round_ids=(999,))
    assert report.audit == ""


async def test_with_attendance_off_nothing_is_audited(tmp_path):
    report = await _audit(tmp_path, attendance=False)
    assert report.audit == ""


async def test_the_audit_is_read_before_the_call_comes_down(tmp_path, monkeypatch):
    """Nothing the withdrawal does touches the answers, but the order is what the log reads
    as: the check-in as it stood, then the call gone."""
    order: list[str] = []
    original = cns._checkin_audit

    async def _audited(*args):
        order.append("audit")
        return await original(*args)

    async def _withdrawn(*args):
        order.append("withdraw")

    monkeypatch.setattr(cns, "_checkin_audit", _audited)
    monkeypatch.setattr(cns, "_withdraw_call", _withdrawn)
    await _audit(tmp_path)
    assert order == ["audit", "withdraw"]


async def test_an_audit_that_cannot_be_read_still_lets_the_call_come_down(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cns, "_checkin_audit", AsyncMock(side_effect=RuntimeError("locked")))
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    deleted = _call_channel(bot)
    guild, _ = _guild()

    report = await cns.announce_cancellation(
        bot, guild, [_division()], scope=cns.SCOPE_ROUND, round_ids=frozenset({ROUND_ID})
    )

    assert [f.target for f in report.failures] == ["check-in audit"]
    assert CALL_MSG in deleted


async def test_a_call_discord_would_not_delete_is_named_for_removal_by_hand(tmp_path):
    """The row goes regardless, so nothing would take the call down later; the admin must
    be told it is still standing."""
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    _call_channel(bot, refuse=True)
    guild, _ = _guild()

    report = await cns.announce_cancellation(
        bot, guild, [_division()], scope=cns.SCOPE_ROUND, round_ids=frozenset({ROUND_ID})
    )

    assert [f.target for f in report.failures] == ["check-in call"]
    reason = report.failures[0].reason
    assert "3 message(s) could not be deleted" in reason
    assert str(CALL_MSG) in reason


async def test_only_the_division_s_own_rounds_are_withdrawn_and_audited(tmp_path):
    """A season names every round it calls off, across every division; each division is told
    of its own alone."""
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (77, ?, 'Am', 2, 556)",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at)"
            " VALUES (78, 77, 3, 'NORMAL', '2026-10-01T18:00:00')",
        )
        await db.commit()
    bot = _with_attendance(_bot(db_path))
    withdrawn: list[tuple[int, int]] = []

    async def _record(round_id, division_id, bot_, **kwargs):
        withdrawn.append((round_id, division_id))

    from services import rsvp_service

    bot.get_channel = MagicMock(return_value=None)
    guild, _ = _guild()
    import unittest.mock as _mock

    with _mock.patch.object(rsvp_service, "withdraw_rsvp_call", _record):
        report = await cns.announce_cancellation(
            bot, guild, [_division()], scope=cns.SCOPE_SEASON,
            round_ids=frozenset({ROUND_ID, 78}),
        )

    assert withdrawn == [(ROUND_ID, DIVISION_ID)]
    assert "Round 3 (Monza)" in report.audit
