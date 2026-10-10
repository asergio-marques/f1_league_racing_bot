"""What each module says when a round, a division or a season is called off (#175).

Before this, a cancellation posted one notice, to the forecast channel, whether or not the
weather module was on — so a league without weather heard nothing, and a league with it
switched off heard from a module that should have produced nothing. These tests pin each
module to its own channel and its own enabled state, and the calendar to being refreshed.

A round's, a division's and a season's cancellation are on the change queue (#439): each
module's notice is a job of its own (`post_module_notice`), as is each round's call taken down
(`take_down_call`). The job is planned only while its module is on, so the notice itself does
not look.
"""
from __future__ import annotations

import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services import cancellation_notice_service as cns

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


# ── Each module in its own channel ─────────────────────────────────────────


@pytest.mark.parametrize(
    "module, channel_id",
    [("weather", FORECAST), ("results", RESULTS), ("attendance", RSVP)],
)
async def test_each_module_posts_only_to_its_own_channel(tmp_path, module, channel_id):
    """Pro's notice of round 3's cancellation, posted for *module* alone, reaches that module's
    channel and no other."""
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    await _post_notice(bot, guild, module)
    for cid, channel in channels.items():
        if cid == channel_id:
            channel.send.assert_awaited_once()
        else:
            channel.send.assert_not_awaited()


async def test_weather_and_results_are_silent_notes(tmp_path):
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    await _post_notice(bot, guild, "weather")
    await _post_notice(bot, guild, "results")
    assert channels[FORECAST].send.await_args.kwargs.get("silent") is True
    assert channels[RESULTS].send.await_args.kwargs.get("silent") is True


async def test_the_check_in_channel_carries_the_notification(tmp_path):
    """Not silent, and it mentions the division role as the check-in call does."""
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    await _post_notice(bot, guild, "attendance")
    call = channels[RSVP].send.await_args
    assert not call.kwargs.get("silent")
    assert call.args[0].startswith(f"<@&{ROLE_ID}>")
    assert call.kwargs["allowed_mentions"].roles is True
    assert "Round 3 Cancelled: Pro" in call.args[0]
    assert "no check-in to answer" in call.args[0]


@pytest.mark.parametrize("scope", [cns.SCOPE_ROUND, cns.SCOPE_DIVISION, cns.SCOPE_SEASON])
async def test_every_scope_says_something_in_every_enabled_module(tmp_path, scope):
    """A round's, a division's and a season's cancellation each post a notice in every
    module's channel, none of them answering that no channel is set."""
    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    for module in ("weather", "results", "attendance"):
        assert await _post_notice(bot, guild, module, scope) is None
    for channel in channels.values():
        channel.send.assert_awaited_once()


def test_a_round_note_names_the_round_and_its_track():
    note = cns.weather_note(cns.SCOPE_ROUND, "Pro", round_number=3, track_name="Monza")
    assert "Round 3 (Monza)" in note
    assert "No weather forecast" in note
    assert "Round 3 (Mystery)" in cns.results_note(cns.SCOPE_ROUND, "Pro", round_number=3)


# ── What could not be notified is named ────────────────────────────────────


def test_failures_are_named_for_the_reply():
    lines = cns.failure_lines([cns.NoticeFailure("Pro", "results channel", "no channel is set")])
    assert "Not notified" in lines
    assert "**Pro** — results channel: no channel is set" in lines
    assert cns.failure_lines([]) == ""


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
    from leaguebot.attendance.services.attendance_service import AttendanceService

    bot.attendance_service = AttendanceService(bot.db_path)
    return bot


async def _rows(db_path, table):
    async with get_connection(db_path) as db:
        return await (await db.execute(f"SELECT * FROM {table}")).fetchall()  # noqa: S608


# ── The check-in is audited in the log before the call comes down ──────────


async def _distribute(db_path) -> None:
    """Two reserves, one sent to a team and one left standing by."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name) VALUES (51, ?, 'Ferrari', 'Ferrari')",
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


async def _audit(tmp_path, *, distribute=False, round_ids=(ROUND_ID,)):
    """Take down the call of each of *round_ids* as the `take_down_call` job does, and give the
    check-in audit each read before its call came down, together."""
    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    if distribute:
        await _distribute(db_path)
    bot = _with_attendance(_bot(db_path))
    _call_channel(bot)
    audits = [
        (await cns.take_down_call(bot, _division(), round_id))["audit"]
        for round_id in round_ids
    ]
    return NS(audit="".join(audits))


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


# ── The notices and take-downs, as jobs on the queue (#439, slices 4b and 5) ──
#
# A round's, a division's or a season's cancellation posts each notice and takes each call down
# as a job of the change queue, which stops on a failure Discord caused; so each raises here.

AUDIT_OF_ROUND_3 = (
    "\n  check-in, Pro, Round 3 (Monza):"
    "\n    accepted: <@101>"
    "\n    tentative: none"
    "\n    declined: <@102>"
    "\n    no answer: none"
)


async def _post_notice(bot, guild, module, scope=cns.SCOPE_ROUND):
    return await cns.post_module_notice(
        bot, guild, _division(), module, scope=scope, round_number=3,
        track_name="Monza",
    )


async def test_a_raising_notice_raises_where_its_channel_is_gone(tmp_path):
    """Pro's check-in channel is set but no longer on the server. Posting attendance's notice
    raises StepFailedOnDiscord, caused by nothing else, so the queue stops until the channel is
    set again and Retry is pressed."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    bot = _bot(await _make_db(tmp_path))
    guild, _ = _guild(missing=(RSVP,))
    with pytest.raises(StepFailedOnDiscord) as caught:
        await _post_notice(bot, guild, "attendance")
    assert caught.value.__cause__ is None


async def test_a_raising_notice_raises_where_discord_refuses(tmp_path):
    """Discord refuses the forecast note in Pro's forecast channel (Forbidden). Posting it raises
    StepFailedOnDiscord, raised from Discord's refusal, which the stop notice names."""
    import discord

    from leaguebot.core.models.change import StepFailedOnDiscord

    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    refusal = discord.Forbidden(MagicMock(status=403), "Missing Access")
    channels[FORECAST].send.side_effect = refusal
    with pytest.raises(StepFailedOnDiscord) as caught:
        await _post_notice(bot, guild, "weather")
    assert caught.value.__cause__ is refusal


async def test_a_raising_notice_lets_a_fault_of_the_bot_s_own_through_unchanged(tmp_path):
    """Sending the results note fails with a ValueError, a fault of the bot's own rather than
    Discord's. It is raised as it is, not turned into StepFailedOnDiscord."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    bot = _bot(await _make_db(tmp_path))
    guild, channels = _guild()
    channels[RESULTS].send.side_effect = ValueError("bad content")
    with pytest.raises(ValueError, match="bad content") as caught:
        await _post_notice(bot, guild, "results")
    assert not isinstance(caught.value, StepFailedOnDiscord)


async def test_a_raising_notice_names_a_channel_never_set(tmp_path):
    """Pro has no results channel set. Posting the results note raises nothing, sends nothing
    and answers "no channel is set", which the cancellation names; a channel posted to answers
    None."""
    bot = _bot(await _make_db(tmp_path, results=False))
    guild, channels = _guild()
    assert await _post_notice(bot, guild, "results") == "no channel is set"
    for channel in channels.values():
        channel.send.assert_not_awaited()
    assert await _post_notice(bot, guild, "weather") is None
    channels[FORECAST].send.assert_awaited_once()


async def test_the_take_down_reads_the_check_in_before_the_call_comes_down(
    tmp_path, monkeypatch
):
    """Round 3 (Monza) has its call standing, Lewis (101) accepted and Max (102) declined. The
    take-down reads that check-in first, then withdraws the call in its raising form: the
    withdrawal here wipes the answers, and the audit it gives still names both drivers."""
    from leaguebot.attendance.services import rsvp_service

    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    calls: list[dict] = []

    async def _withdraw(round_id, division_id, bot_, **kwargs):
        calls.append({"round_id": round_id, "division_id": division_id, **kwargs})
        async with get_connection(db_path) as db:
            await db.execute("DELETE FROM driver_round_attendance")
            await db.commit()
        return True

    monkeypatch.setattr(rsvp_service, "withdraw_rsvp_call", _withdraw)
    result = await cns.take_down_call(bot, _division(), ROUND_ID)

    assert result["audit"] == AUDIT_OF_ROUND_3
    assert result["taken_down"]
    assert [(c["round_id"], c["division_id"], c.get("raise_on_failure")) for c in calls] == [
        (ROUND_ID, DIVISION_ID, True)
    ]


async def test_a_take_down_discord_refuses_raises_keeping_the_audit_and_the_ids(tmp_path):
    """Round 3's call, its last notice and its distribution (9001-9003) stand, and Discord
    refuses to delete any of them. The take-down raises StepFailedOnDiscord carrying the check-in
    audit and the three ids, and the call's record stays for the next try."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    _call_channel(bot, refuse=True)

    with pytest.raises(StepFailedOnDiscord) as caught:
        await cns.take_down_call(bot, _division(), ROUND_ID)

    assert caught.value.result["audit"] == AUDIT_OF_ROUND_3
    assert sorted(caught.value.result["undeleted"]) == [
        str(CALL_MSG), str(LAST_MSG), str(DIST_MSG)
    ]
    assert len(await _rows(db_path, "rsvp_embed_messages")) == 1
