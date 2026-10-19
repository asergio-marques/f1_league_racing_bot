"""The final attendance sheet a season's completion posts, where its division has no attendance
channel set (#439, slice 6b, fault 5); and the signup window a season's end closes, its Sign Up
button's deletion and its closed notice each a job of their own (#439, slice 6b, C67).

A completion's `final_sheet` job used to complete having posted nothing where the division's
attendance channel was unset, where a sheet a command asked for stops the queue naming the command
that sets the channel. It stops the queue too, while a sheet no longer due, with attendance off,
is still dropped.

The league is `tests.support.season_league`'s `pending_completion_league`: season 3, every round
of Pro and Am final, Pro's rounds 1 and 2 and Am's round 1 with results, completed by admin 77
through `/season complete`. The signup window's tests use `ongoing_league` with signups open:
season 3 ongoing, its window open in the signup channel (620) with the Sign Up button (6201)
standing, cancelled by admin 77 through `/season cancel`.
"""

from __future__ import annotations

import json

import discord
import pytest

from tests.support.change_queue import (
    change_rows,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    DIVISIONS,
    PRO,
    SEASON_CANCEL_KIND,
    SIGNUP_BUTTON,
    SIGNUP_CHANNEL,
    cancel_season,
    complete_season,
    ongoing_league,
    pending_completion_league,
)


PRO_CH = DIVISIONS[PRO][3]


async def _unset_pro_attendance_channel(league) -> None:
    await league.write(
        "UPDATE attendance_division_config SET attendance_channel_id = NULL WHERE division_id = ?",
        PRO,
    )


async def test_the_final_sheet_with_no_channel_stops_the_queue(tmp_path):
    """Attendance is on, and Pro's attendance channel was never set. Admin 77 completes the
    season: the queue stops at Pro's final sheet, its stop line naming the command that sets the
    channel, and the season is not completed while it stands stopped."""
    league = await pending_completion_league(tmp_path, attendance=True)
    await _unset_pro_attendance_channel(league)
    await complete_season(league)
    assert not league.errors, league.errors

    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "final_sheet"
    assert json.loads(job["payload"])["division_id"] == PRO
    assert "/attendance channel attendance" in league.log()
    assert (await league.season())["status"] == "ACTIVE"


async def test_a_sheet_with_attendance_off_is_still_dropped(tmp_path):
    """Attendance is off, and Pro's attendance channel is unset. Admin 77 completes the season:
    no sheet is due, so nothing stops the queue and the season is completed."""
    league = await pending_completion_league(tmp_path, attendance=False)
    await _unset_pro_attendance_channel(league)
    await complete_season(league)
    assert not league.errors, league.errors

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await league.season())["status"] == "COMPLETED"
    assert league.texts(PRO_CH.attendance) == []


# ── The signup window a season's end closes (C67) ──────────────────────────────────

CLOSE_JOBS = (
    "#439: a season's end deletes the Sign Up button and posts the closed notice inside its "
    "close_window job, a refusal named in its outcome, rather than as jobs of their own that stop "
    "the queue"
)

#: The notice a closed window leaves in the signup channel.
CLOSED_NOTICE = "🔒 Signups are now closed."


async def _cancel_with_signups_open(tmp_path):
    league = await ongoing_league(tmp_path, signups_open=True)
    await cancel_season(league)
    assert not league.errors, league.errors
    return league


async def _cancellation_jobs(league) -> dict[str, dict]:
    """The cancellation's jobs by name (each name once in these tests)."""
    [change] = [row for row in await change_rows(league.db_path)
                if row["kind"] == SEASON_CANCEL_KIND]
    return {job["name"]: job for job in await step_rows(league.db_path, change["id"])}


def _closed_notices(league) -> list:
    return [message for message in league.channel(SIGNUP_CHANNEL).messages.values()
            if message.content == CLOSED_NOTICE]


@pytest.mark.xfail(strict=True, reason=CLOSE_JOBS)
async def test_a_season_end_s_close_deletes_the_sign_up_button_as_a_job(tmp_path):
    """The season is ongoing with its signup window open, the Sign Up button standing in the
    signup channel. Admin 77 cancels the season and the queue runs: the button is deleted by a
    `delete_button` job of the cancellation, done, and the season is cancelled."""
    league = await _cancel_with_signups_open(tmp_path)

    await run_queue(league.bot)

    jobs = await _cancellation_jobs(league)
    assert "delete_button" in jobs and jobs["delete_button"]["done_at"]
    assert ("delete", SIGNUP_CHANNEL, SIGNUP_BUTTON) in league.events
    assert SIGNUP_BUTTON not in league.channel(SIGNUP_CHANNEL).messages
    assert (await league.season())["status"] == "CANCELLED"


@pytest.mark.xfail(strict=True, reason=CLOSE_JOBS)
async def test_a_season_end_s_refused_closed_notice_stops_the_queue(tmp_path):
    """The season is ongoing with its signup window open, and Discord refuses every post in the
    signup channel. Admin 77 cancels the season: the queue stops at the `post_closed` job, with no
    closed notice posted (today the refusal is only named in the cancellation's outcome). Once
    the channel takes posts again, Retry posts the notice and the season is cancelled."""
    league = await _cancel_with_signups_open(tmp_path)
    league.channel(SIGNUP_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_closed"
    assert _closed_notices(league) == []

    league.channel(SIGNUP_CHANNEL).send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(_closed_notices(league)) == 1
    assert (await league.season())["status"] == "CANCELLED"


@pytest.mark.xfail(strict=True, reason=CLOSE_JOBS)
async def test_the_closed_notice_s_id_is_saved_with_its_mark(tmp_path):
    """The season is ongoing with its signup window open. Admin 77 cancels the season and the
    queue runs: the closed notice is posted by a `post_closed` job, done, and the notice's id is
    the one the signup configuration keeps (so the next `/signup open` deletes it), with the
    window recorded closed."""
    league = await _cancel_with_signups_open(tmp_path)

    await run_queue(league.bot)

    jobs = await _cancellation_jobs(league)
    assert "post_closed" in jobs and jobs["post_closed"]["done_at"]
    [notice] = _closed_notices(league)
    [config] = await league.rows(
        "SELECT signups_open, signup_closed_message_id FROM signup_module_config WHERE id = 1"
    )
    assert config["signup_closed_message_id"] == notice.id
    assert not config["signups_open"]
