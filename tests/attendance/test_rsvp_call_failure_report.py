"""A check-in call that does not post must be visible to the league's staff (FR-062).

The hazard this closes predates the image module. A call that fails to post opens no attendance
rows; the penalty pass iterates those rows and finds none; and the attendance sheet then draws
every cell of that round empty — which means **zero points**. A round nobody was asked to check in
for is recorded as flawless attendance for everyone, and nothing anywhere says otherwise.

The call is now a job of a change on the queue (#439, slice 6b): a call that does not post stops
the queue, and the stop notice in the log channel is what staff act on, saying what is left for the
round; Retry posts it, and a Discard's line names the command that posts it by hand. The line
`call_failure_text` forms carries a note of its own for a round whose deadline passed with no
call standing (#429).

The league is `ongoing_league`'s with attendance on: Pro (role 801), its check-in channel 603,
round 4 moved to three days off with no call posted for it. The call is asked of the queue as its
timer asks it, and the queue run.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from leaguebot.attendance.services.attendance_service import AttendanceService
from leaguebot.attendance.services.rsvp_service import call_failure_text
from tests.support.change_queue import (
    attach_queue,
    discard_job,
    http_error,
    run_queue,
    stopped_job,
)
from tests.support.season_league import DIVISIONS, PRO, SeasonLeague, ongoing_league, round_id

CHECK_IN = DIVISIONS[PRO][3].checkin
ROUND_4 = round_id(PRO, 4)

#: What the stop notice says is left where no call stands and no answers are kept.
NOTHING_LEFT = "no attendance rows are open for this round until this call is posted"
#: What the stop notice says the round comes to meanwhile (attendance spec :120: "the round will
#: count nothing against anyone until the call is posted").
COUNTS_NOTHING = "will count nothing against anyone"


async def _league(tmp_path: Any, *, images: bool = False) -> SeasonLeague:
    league = await ongoing_league(tmp_path, attendance=True, images=images)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    when = league.clock.now + timedelta(days=3)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_4)
    return league


def _refuse(league: SeasonLeague, error: BaseException | None = None) -> None:
    league.channel(CHECK_IN).send_fails = error or http_error(
        status=503, text="Service Unavailable")


async def _ask_and_run(league: SeasonLeague) -> None:
    from leaguebot.attendance.services.check_in_changes import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_call(league.bot.change_queue, league.db_path, ROUND_4,
                            origin=ChangeOrigin.BOT)
    await run_queue(league.bot)


async def _nothing_opened(league: SeasonLeague) -> bool:
    return (
        await league.rows("SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?", ROUND_4) == []
        and await league.rows(
            "SELECT 1 FROM driver_round_attendance WHERE round_id = ?", ROUND_4) == []
    )


async def test_a_refused_call_s_stop_notice_names_the_division_the_round_and_the_refusal(tmp_path):
    """Discord answers 503: the queue stops at the call, and its notice names Pro, round 4 and
    what Discord said."""
    league = await _league(tmp_path)
    _refuse(league)

    await _ask_and_run(league)

    assert await stopped_job(league.db_path) is not None
    notice = league.log()
    assert "Pro" in notice
    assert "round 4" in notice.lower()
    assert "Service Unavailable" in notice


async def test_the_report_says_the_round_opened_no_attendance_rows(tmp_path):
    """The consequence is what staff need to know, not merely that a send failed: the stop notice
    says no attendance rows are open for the round until the call is posted. FR-062 holds with
    images off, as here."""
    league = await _league(tmp_path)
    _refuse(league)

    await _ask_and_run(league)

    assert await stopped_job(league.db_path) is not None
    assert NOTHING_LEFT in league.log()
    assert COUNTS_NOTHING in league.log()
    assert await _nothing_opened(league)


async def test_a_failure_to_report_never_masks_the_original_failure(tmp_path):
    """The log channel refuses the stop notice as well: the call is still stopped at, with
    nothing recorded, for the queue to try again."""
    league = await _league(tmp_path)
    _refuse(league)
    league.bot.log_channel.fails = http_error(status=503, text="Service Unavailable")

    await _ask_and_run(league)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "post_call"
    assert await _nothing_opened(league)


async def test_both_failure_paths_of_the_notice_report_the_call(tmp_path):
    """A refused send stops the queue (the tests above); so does a check-in channel gone from the
    server, its notice saying what is left."""
    league = await _league(tmp_path)
    league.remove_channel(CHECK_IN)

    await _ask_and_run(league)

    assert await stopped_job(league.db_path) is not None
    assert NOTHING_LEFT in league.log()
    assert COUNTS_NOTHING in league.log()
    assert await _nothing_opened(league)


async def test_the_report_is_not_guarded_by_any_conditional_in_its_call_sites(tmp_path, monkeypatch):
    """Images on and the check-in graphic drawn: a refused call stops the queue with its notice
    as it does without one."""
    from leaguebot.image.services import image_rsvp_post

    league = await _league(tmp_path, images=True)
    picture = tmp_path / "checkin.png"

    async def _attach(*_args: Any, **_kwargs: Any) -> discord.File:
        picture.write_bytes(b"png")
        return discord.File(str(picture), filename="checkin.png")

    monkeypatch.setattr(image_rsvp_post, "try_attach", AsyncMock(side_effect=_attach))
    _refuse(league)

    await _ask_and_run(league)

    assert await stopped_job(league.db_path) is not None
    assert NOTHING_LEFT in league.log()
    assert COUNTS_NOTHING in league.log()


@pytest.mark.parametrize(
    "refusal",
    [None, http_error(discord.Forbidden, status=403, text="Missing Permissions")],
    ids=["service-unavailable", "forbidden"],
)
async def test_the_note_names_the_command_that_posts_the_call_again(tmp_path, refusal):
    """The advice must name a command that exists (#123), filled in for the division and round so
    a manager can copy it out of the log channel: a league admin discards the refused call, no
    call stands and the queue is no longer stopped, and the line written for the discard says to
    post it with `/attendance post-check-in division: Pro round: 4`, whatever Discord refused
    with."""
    league = await _league(tmp_path)
    _refuse(league, refusal)
    await _ask_and_run(league)
    told_before = league.log()

    await discard_job(league.bot)

    assert "/attendance post-check-in division: Pro round: 4" in league.log()[len(told_before):]
    assert await stopped_job(league.db_path) is None
    assert await league.rows(
        "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?", ROUND_4) == []


def test_the_command_the_note_names_is_a_real_one():
    """Pinned against the cog itself, so a rename breaks this rather than a league's recovery."""

    from leaguebot.attendance.cogs.attendance_cog import AttendanceCog

    assert "post-check-in" in {c.name for c in AttendanceCog.attendance.commands}


def test_a_report_can_carry_a_note_of_its_own():
    """A round whose deadline passed with no call standing (#429) cannot have its call posted by
    hand either — the command refuses once the deadline has gone — so its line must not advise
    it. The line takes the note it is given in place of the usual advice."""
    content = call_failure_text(
        division_id=7,
        division_name="Division 1",
        season_number=4,
        round_number=11,
        reason="whatever",
        note="nothing can be done about it",
    )

    assert "  note: nothing can be done about it" in content
    assert "/attendance post-check-in" not in content
