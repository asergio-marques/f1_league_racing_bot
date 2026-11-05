"""What core keeps of a round's, a division's or a season's cancellation (#175, #283).

Each module words, finds its channel for and posts its own notice of a cancellation, and its tests
pin that (`tests/<module>/test_cancellation_notice.py`). Core keeps its own: the failures a
cancellation names in its reply and its log line, and the check-in record a round called off writes
in the log.

A round's, a division's and a season's cancellation are on the change queue (#439): each module's
notice is a job of its own, as is each round's call taken down (`take_down_call`). The notice is
planned whatever the modules, and drops itself as it runs where its module is off.

The take-down is attendance's own job (`check_in_changes.take_down_call`, #283), which deletes the
call's messages through attendance's own delete steps (`check_in_deletes`); it stays one job,
reading the check-in audit, core's `checkin_record`, before it deletes. The tests hand it delete
steps of their own.
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


def _division(message_id=None):
    return NS(
        id=DIVISION_ID, name="Pro", mention_role_id=ROLE_ID, calendar_message_id=message_id
    )


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


def _check_in_deletes(*, refuse=None, wipe_db=None):
    """Attendance's `check_in_deletes` as its delete steps answer it: called with the round
    and the division, it gives True where a call stood. With *refuse*, a Discord refusal, it
    raises `StepFailedOnDiscord` naming the three messages as undeleted, raised from *refuse*.
    With *wipe_db*, it clears that database's answers first, as delete steps that took them would.
    Its `calls` list each `(round_id, division_id)` it was asked."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    calls: list[tuple[int, int]] = []

    async def deletes(round_id, division_id):
        calls.append((round_id, division_id))
        if wipe_db is not None:
            async with get_connection(wipe_db) as db:
                await db.execute("DELETE FROM driver_round_attendance")
                await db.commit()
        if refuse is not None:
            raise StepFailedOnDiscord(
                "Discord refused to delete the check-in call",
                result={"undeleted": [str(CALL_MSG), str(LAST_MSG), str(DIST_MSG)]},
            ) from refuse
        return True

    deletes.calls = calls
    return deletes


def _with_attendance(bot):
    from leaguebot.attendance.services.attendance_service import AttendanceService

    bot.attendance_service = AttendanceService(bot.db_path)
    return bot


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
    """Take down the call of each of *round_ids* as attendance's `take_down_call` job does, and
    give the check-in audit each read before its call came down, together."""
    from leaguebot.attendance.services.check_in_changes import take_down_call

    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    if distribute:
        await _distribute(db_path)
    bot = _with_attendance(_bot(db_path))
    deletes = _check_in_deletes()
    audits = [
        (await take_down_call(bot, _division(), round_id, check_in_deletes=deletes))["audit"]
        for round_id in round_ids
    ]
    return NS(audit="".join(audits))


async def test_the_audit_groups_the_drivers_by_their_answer(tmp_path):
    """Before a distribution, the audit says nothing of reserves."""
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


async def test_a_round_whose_call_never_went_out_is_not_audited(tmp_path):
    report = await _audit(tmp_path, round_ids=(999,))
    assert report.audit == ""


# ── The take-downs, as jobs on the queue (#439, slices 4b and 5) ──
#
# A round's, a division's or a season's cancellation takes each call down as a job of the change
# queue, which stops on a failure Discord caused; so each raises here.

AUDIT_OF_ROUND_3 = (
    "\n  check-in, Pro, Round 3 (Monza):"
    "\n    accepted: <@101>"
    "\n    tentative: none"
    "\n    declined: <@102>"
    "\n    no answer: none"
)


async def test_the_take_down_reads_the_check_in_before_the_call_comes_down(tmp_path):
    """Round 3 (Monza) has its call standing, Lewis (101) accepted and Max (102) declined.
    Attendance's take-down reads that check-in first, then asks its delete steps
    (`check_in_deletes`) once for round 3 of the division: the steps here wipe the answers, and
    the audit it gives still names both drivers."""
    from leaguebot.attendance.services.check_in_changes import take_down_call

    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    deletes = _check_in_deletes(wipe_db=db_path)

    result = await take_down_call(bot, _division(), ROUND_ID, check_in_deletes=deletes)

    assert result["audit"] == AUDIT_OF_ROUND_3
    assert result["taken_down"]
    assert deletes.calls == [(ROUND_ID, DIVISION_ID)]


async def test_a_take_down_discord_refuses_raises_keeping_the_audit_and_the_ids(tmp_path):
    """Round 3's call, its last notice and its distribution (9001-9003) stand, and attendance's
    delete steps report Discord refusing all three. Attendance's take-down raises
    StepFailedOnDiscord carrying the check-in audit and the three ids, raised from Discord's
    refusal."""
    import discord

    from leaguebot.attendance.services.check_in_changes import take_down_call

    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _make_db(tmp_path)
    await _with_call(db_path)
    bot = _with_attendance(_bot(db_path))
    refusal = discord.Forbidden(MagicMock(status=403), "nope")
    deletes = _check_in_deletes(refuse=refusal)

    with pytest.raises(StepFailedOnDiscord) as caught:
        await take_down_call(bot, _division(), ROUND_ID, check_in_deletes=deletes)

    assert caught.value.result["audit"] == AUDIT_OF_ROUND_3
    assert sorted(caught.value.result["undeleted"]) == [
        str(CALL_MSG), str(LAST_MSG), str(DIST_MSG)
    ]
    assert caught.value.__cause__ is refusal
