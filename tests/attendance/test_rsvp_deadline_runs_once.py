"""A round's check-in deadline runs once for each call (#429), now as a change on the queue
(#439, slice 6b, C30).

Two runs of one deadline used to meet: the scheduler's late run and the restart's catch-up, or two
presses of `/test-mode advance`, and the division was sent two announcements. On the queue the
deadline is a change keyed to its round, so a second ask while the first is in hand is the same
change, and the queue runs one change at a time on the league's lane.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from leaguebot.attendance.services.attendance_service import AttendanceService

NO_DEADLINE_CHANGE = (
    "#439: the check-in deadline is run by `run_rsvp_deadline` at once, off the change queue"
)


# A deadline that has run is marked so on its call (`deadline_run_at`), apart from the message it
# posts (#439, slice 6b): a discarded announcement leaves the placements made and the deadline
# run, and it must not be run again, at a restart or otherwise. These use `ongoing_league` with
# attendance on, its queue rebuilt on a real `AttendanceService`: Pro's round 3 is an hour off,
# its two-hour deadline passed, its call standing in Pro's check-in channel (603) as message 7001
# with its last notice 7002. The deadline is asked of the queue as its timer asks it, and the
# queue run.


async def _league_past_its_deadline(tmp_path, *, marked: bool, announced: bool):
    from tests.support.change_queue import attach_queue
    from tests.support.season_league import CALL_MESSAGES, DIVISIONS, PRO, ongoing_league, round_id

    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    now = league.clock.now
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       (now + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
                       round_id(PRO, 3))
    if not announced:
        del league.channel(DIVISIONS[PRO][3].checkin).messages[CALL_MESSAGES[2]]
        await league.write("UPDATE rsvp_embed_messages SET distribution_msg_id = NULL "
                           "WHERE round_id = ?", round_id(PRO, 3))
    if marked:
        await league.write("UPDATE rsvp_embed_messages SET deadline_run_at = ? WHERE round_id = ?",
                           (now - timedelta(minutes=30)).isoformat(), round_id(PRO, 3))
    return league


async def _ask_deadline(league) -> None:
    from leaguebot.attendance.services.check_in_changes import ask_check_in_deadline
    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.change_queue import run_queue
    from tests.support.season_league import PRO, round_id

    await ask_check_in_deadline(league.bot.change_queue, league.db_path, round_id(PRO, 3),
                                origin=ChangeOrigin.BOT)
    await run_queue(league.bot)


def _check_in_events(league) -> list[tuple[str, int]]:
    from tests.support.season_league import DIVISIONS, PRO

    return [(kind, mid) for kind, cid, mid in league.events
            if cid == DIVISIONS[PRO][3].checkin]


async def _call(league) -> dict:
    from tests.support.season_league import PRO, round_id

    [row] = await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?",
                              round_id(PRO, 3))
    return row


async def test_a_deadline_whose_mark_is_set_does_nothing(tmp_path):
    """It has run, and its announcement stands. The call is not locked again and nothing is
    posted."""
    from tests.support.change_queue import stopped_job

    league = await _league_past_its_deadline(tmp_path, marked=True, announced=True)

    await _ask_deadline(league)

    assert _check_in_events(league) == []
    assert await stopped_job(league.db_path) is None


async def test_a_deadline_marked_run_is_not_run_again_whatever_became_of_its_announcement(
    tmp_path,
):
    """Its announcement was discarded, so no message is recorded on the call; the mark still
    says the deadline ran, and the placements it made stand without a second announcement."""
    from tests.support.change_queue import stopped_job

    league = await _league_past_its_deadline(tmp_path, marked=True, announced=False)

    await _ask_deadline(league)

    assert _check_in_events(league) == []
    assert await stopped_job(league.db_path) is None
    assert (await _call(league))["distribution_msg_id"] is None


async def test_a_deadline_whose_message_never_posted_is_run_again(tmp_path):
    """No mark and no message: the deadline has not run, so it locks the call, posts its
    announcement, and records both the message and the mark."""
    from tests.support.change_queue import stopped_job
    from tests.support.season_league import CALL_MESSAGES

    league = await _league_past_its_deadline(tmp_path, marked=False, announced=False)

    await _ask_deadline(league)

    assert await stopped_job(league.db_path) is None
    events = _check_in_events(league)
    assert ("edit", CALL_MESSAGES[0]) in events
    sent = [mid for kind, mid in events if kind == "send"]
    assert len(sent) == 1
    call = await _call(league)
    assert call["distribution_msg_id"] == str(sent[0])
    assert call["deadline_run_at"] is not None


# ---------------------------------------------------------------------------
# Twice asked, and past the deadline with no call standing (owner, 2026-10-09: "Past the
# deadline, nothing counts"). Pro's round 3 holds Lewis's acceptance and Max's NO_RSVP; where no
# call stands, its record is taken off and the answers are those kept from an earlier call.
# ---------------------------------------------------------------------------


async def _ask_only(league) -> None:
    """Ask the queue for round 3's deadline as its timer does; the queue is not run."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_deadline
    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.season_league import PRO, round_id

    await ask_check_in_deadline(league.bot.change_queue, league.db_path, round_id(PRO, 3),
                                origin=ChangeOrigin.BOT)


async def _without_a_call(tmp_path):
    """The league past round 3's deadline, its call taken off the record, the answers kept."""
    from tests.support.season_league import PRO, round_id

    league = await _league_past_its_deadline(tmp_path, marked=False, announced=False)
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", round_id(PRO, 3))
    return league


async def _round_state(league) -> tuple[int, int]:
    """How many answers round 3 holds, and whether its check-in is marked over."""
    from tests.support.season_league import PRO, round_id

    [answers] = await league.rows(
        "SELECT COUNT(*) AS n FROM driver_round_attendance WHERE round_id = ?", round_id(PRO, 3)
    )
    [cleared] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?",
                                  round_id(PRO, 3))
    return answers["n"], cleared["checkin_cleared"]


def _sent(league) -> list[int]:
    return [mid for kind, mid in _check_in_events(league) if kind == "send"]


async def test_two_deadline_runs_at_once_post_one_announcement(tmp_path):
    """The scheduler's late run and the restart's catch-up both ask round 3's deadline before
    the queue reaches it: the queue holds one change for it, and one announcement is posted and
    recorded."""
    from tests.support.change_queue import change_rows, run_queue

    league = await _league_past_its_deadline(tmp_path, marked=False, announced=False)

    await _ask_only(league)
    await _ask_only(league)
    await run_queue(league.bot)

    changes = [row for row in await change_rows(league.db_path)
               if row["kind"] == "attendance.check_in.deadline"]
    assert len(changes) == 1
    [posted] = _sent(league)
    assert (await _call(league))["distribution_msg_id"] == str(posted)


async def test_a_deadline_with_no_call_standing_posts_nothing(tmp_path):
    """A deadline closes the call standing, and with none there is nothing to close: it posts no
    announcement, which no call would record and nothing would take down a day after the round,
    and the queue does not stop."""
    from tests.support.change_queue import stopped_job

    league = await _without_a_call(tmp_path)

    await _ask_deadline(league)

    assert _sent(league) == []
    assert await stopped_job(league.db_path) is None


async def test_a_deadline_with_no_call_standing_clears_the_answers_kept_and_says_so(tmp_path):
    """No call stands for round 3, its earlier call having been taken down, but the answers to it
    are kept. The deadline is asked and run, the bot running throughout: the answers are
    cleared, the check-in marked over, and the log channel says, once, that the round counts
    nothing against anyone, as a restart past the deadline would. Nothing is posted in the
    check-in channel."""
    league = await _without_a_call(tmp_path)

    await _ask_deadline(league)

    assert await _round_state(league) == (0, 1)
    assert league.log().count("nothing against anyone") == 1
    assert _sent(league) == []


async def test_a_deadline_with_a_call_standing_keeps_its_answers(tmp_path):
    """With round 3's call standing, the deadline runs as it always has: the answers given to it
    are kept, its check-in is not marked over, and nothing says the round counts nothing."""
    league = await _league_past_its_deadline(tmp_path, marked=False, announced=False)

    await _ask_deadline(league)

    assert await _round_state(league) == (2, 0)
    assert "nothing against anyone" not in league.log()


@pytest.mark.xfail(strict=True, reason=NO_DEADLINE_CHANGE)
async def test_a_deadline_and_a_give_up_closing_one_check_in_report_it_once(tmp_path):
    """No call stands for round 3 and its answers are kept. The restart asks its call, past the
    deadline, so the call is to be given up, and the deadline is asked too; the queue runs them
    one after the other. The answers are cleared and the check-in marked over, and the log
    channel says once that the round counts nothing against anyone: the round is reported
    once."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.change_queue import run_queue
    from tests.support.season_league import PRO, round_id

    league = await _without_a_call(tmp_path)

    await ask_check_in_call(league.bot.change_queue, league.db_path, round_id(PRO, 3),
                            origin=ChangeOrigin.BOT)
    await _ask_only(league)
    await run_queue(league.bot)

    assert await _round_state(league) == (0, 1)
    assert league.log().count("nothing against anyone") == 1


async def test_a_call_waiting_to_post_when_the_deadline_closes_the_check_in_posts_nothing(
    tmp_path,
):
    """No call stands for round 3 and its answers are kept; the deadline closes its check-in,
    clearing them. A post of the round's call asked meanwhile (a repost after a take-down, a late
    post) then runs: the check-in is over, so nothing is posted, no call is recorded and no
    attendance rows are opened (owner, 2026-10-09: "Fold it in")."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.change_queue import run_queue
    from tests.support.season_league import PRO, round_id

    league = await _without_a_call(tmp_path)

    await _ask_only(league)
    await ask_check_in_call(league.bot.change_queue, league.db_path, round_id(PRO, 3),
                            origin=ChangeOrigin.BOT)
    await run_queue(league.bot)

    assert _sent(league) == []
    assert await _round_state(league) == (0, 1)
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?",
                             round_id(PRO, 3)) == []
