"""A round whose every session is cancelled (#439, slice 6b, C47a).

Where the last paste of a submission enters its session as CANCELLED and every other session was
cancelled too, there are no Provisional Results to carry the cancelled sessions' notes (results
spec :363) and the round skips both review stages (:379). The turn hands
`results.submission.all_cancelled` to the league: it posts each session's "This session was
cancelled." line in the division's results channel, each recorded as that session's results
message, and then asks round 3's own lane for `results.submission.close_channel`, which says
"⏭️" in the submission channel and deletes it. That deletion is the conversation ending itself,
so where Discord refuses it, only the round's lane stops (decision 25).

The league is `ongoing_league`'s, results on: Pro's round 3 awaits results, its submission open in
channel `SUBMISSION_CHANNEL`, its Feature Qualifying already entered as cancelled; the admin (77)
holds the league managers' role and types CANCELLED for the Feature Race, the last session.

**The round ends cancelled** (#439, slice 8, Part E; owner 2026-10-23, "Fold in: round
cancelled"). The change's first job, `end_round`, marks round 3 CANCELLED and not held in one
save, with its `round.status` audit, finishing Pro where it was the last round outstanding and
winding the season down where that was the last division (core :232-234). It cannot be discarded:
without it the round would never finish and the season could never be completed. Its calendar is
reposted struck through where one was posted, and the log channel gets one `ROUND_CANCELLED` line,
naming any job discarded with its fix. Where `end_round` finds a session no longer entered as
cancelled, it writes nothing, and the channel still closes.
"""
from __future__ import annotations

import importlib
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import discord
import pytest

from leaguebot.core.models.round import RoundFormat
from leaguebot.results.services.result_submission_service import get_sessions_for_format
from leaguebot.results.utils import results_formatter
from tests.support.change_queue import (
    MANAGER_ROLE_ID,
    change_rows,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    typed_message,
)
from tests.support.season_league import (
    ADMIN_ID,
    AM,
    CALL_MESSAGES,
    DIVISIONS,
    PRO,
    PRO_ROLE,
    SEASON_ID,
    SUBMISSION_CHANNEL,
    SeasonLeague,
    accept_session,
    complete_season,
    ongoing_league,
    open_submission,
    reply,
    round_id,
)

ALL_CANCELLED_KIND = "results.submission.all_cancelled"
CLOSE_KIND = "results.submission.close_channel"
ROUND_3 = round_id(PRO, 3)
RESULTS = DIVISIONS[PRO][3].results
LANE = f"channel:{SUBMISSION_CHANNEL}"
CALENDAR = DIVISIONS[PRO][3].calendar

#: Why the tests of the round ending cancelled fail until Part E is built.
NOT_HELD_UNBUILT = (
    "#439: a round whose every session is entered as cancelled is left awaiting its results, "
    "and rounds has no not_held column"
)
#: The refusal of a Discard on the round's ending.
CANNOT_DISCARD_END = (
    "⛔ This job can't be discarded: without it round 3 in **Pro** would never finish, and "
    "season 3 could not be completed. Fix what stopped it and press **Retry**."
)
#: How a discarded calendar repost is named beneath the round's line (W1).
CALENDAR_DISCARDED = (
    "**Pro** — calendar: the calendar could not be posted, and a league admin discarded it; run "
    "`/division calendar-sync` while the season is still being raced"
)
#: The head of the line that records the round ending cancelled.
ENDED_LINE = "| ROUND_CANCELLED | Success"
CHECKIN = DIVISIONS[PRO][3].checkin
FORECAST = DIVISIONS[PRO][3].forecast
#: What sets each module's "round cancelled" notice apart, in `/round cancel`'s words (Q-E5).
CHECKIN_NOTICE = "There is no check-in to answer for it, and nobody is charged for it."
FORECAST_NOTE = "No weather forecast will be posted for it."
RESULTS_NOTE = "No results will be posted for it."
#: The heading every one of round 3's notices opens with.
NOTICE_HEADING = "**Round 3 Cancelled: Pro**"

QUALIFYING, RACE = (
    results_formatter.format_session_label(session, is_sprint=False)
    for session in get_sessions_for_format(RoundFormat.NORMAL)[:2]
)

async def _league(tmp_path: Any, **modules: bool) -> SeasonLeague:
    """The league of the module docstring, the race not yet entered; *modules* turn weather or
    attendance on beside results."""
    league = await ongoing_league(tmp_path, **modules)
    league.roles_held[ADMIN_ID] = {MANAGER_ROLE_ID}
    await league.write("UPDATE rounds SET status = 'AWAITING_RESULTS' WHERE id = ?", ROUND_3)
    await open_submission(league, ROUND_3)
    sub = league.channel(SUBMISSION_CHANNEL)
    if not isinstance(getattr(sub, "posted", None), list):
        sub.posted = []
    await accept_session(league, ROUND_3, session="FEATURE_QUALIFYING", status="CANCELLED")
    return league


async def _cancel_the_race(league: SeasonLeague) -> None:
    """The admin types CANCELLED for the race in round 3's submission channel, and the results
    cog hears it."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    message = typed_message(
        league.channel(SUBMISSION_CHANNEL), league._member(ADMIN_ID), "CANCELLED",
        created_at=league.clock.now,
    )
    await ResultsCog(league.bot).on_message(message)


async def _recorded(league: SeasonLeague) -> dict[str, int | None]:
    """Each of round 3's sessions, by type, with the results message recorded for it."""
    rows = await league.rows(
        "SELECT session_type, results_message_id FROM session_results WHERE round_id = ?",
        ROUND_3,
    )
    return {row["session_type"]: row["results_message_id"] for row in rows}


async def _round(league: SeasonLeague) -> tuple[str, int]:
    """Round 3's status, and whether it is marked as not held."""
    [row] = await league.rows("SELECT status, not_held FROM rounds WHERE id = ?", ROUND_3)
    return row["status"], row["not_held"]


async def _round_audits(league: SeasonLeague) -> list[tuple[Any, ...]]:
    """Each `round.status` audit of round 3's division: actor, old status, new status."""
    rows = await league.rows(
        "SELECT actor_id, old_value, new_value FROM audit_entries "
        "WHERE change_type = 'round.status' AND division_id = ? ORDER BY id", PRO,
    )
    return [(row["actor_id"], row["old_value"], row["new_value"]) for row in rows]


def _ended_lines(league: SeasonLeague) -> list[str]:
    """The log channel's lines recording a round ending cancelled."""
    return [line for line in league.bot.log_channel.sent if ENDED_LINE in line]


async def _stopped_at(league: SeasonLeague) -> str | None:
    job = await stopped_job(league.db_path)
    return None if job is None else job["name"]


async def _closed(league: SeasonLeague) -> bool:
    [row] = await league.rows(
        "SELECT closed FROM round_submission_channels WHERE round_id = ?", ROUND_3
    )
    return bool(row["closed"])


async def test_every_session_cancelled_posts_each_line_recorded_before_the_channel_closes(
    tmp_path,
):
    """Round 3's qualifying was entered as cancelled; the admin types CANCELLED for the race.
    When the queue runs, `results.submission.all_cancelled` is asked on the league lane, its first
    job `end_round`, which ends round 3 cancelled and marked as not held. Pro's
    results channel carries one "This session was cancelled." line for the qualifying and one for
    the race, each recorded as its session's results message. Only after both are posted does
    `results.submission.close_channel`, on round 3's lane, say "⏭️" in the submission channel and
    delete it, closing the submission row. No penalty review is asked and the queue is not
    stopped. The results channel's "round cancelled" note comes after both lines."""
    league = await _league(tmp_path)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    changes = await change_rows(league.db_path)
    [handed] = [row for row in changes if row["kind"] == ALL_CANCELLED_KIND]
    assert handed["lane"] == "league"
    assert (await step_rows(league.db_path, handed["id"]))[0]["name"] == "end_round"
    assert await _round(league) == ("CANCELLED", 1)
    [closing] = [row for row in changes if row["kind"] == CLOSE_KIND]
    assert closing["lane"] == LANE
    assert not [row for row in changes if row["kind"] == "results.review.open"]

    results = league.channel(RESULTS)
    recorded = await _recorded(league)
    lines = {}
    for session, label in (("FEATURE_QUALIFYING", QUALIFYING), ("FEATURE_RACE", RACE)):
        message_id = recorded[session]
        assert message_id is not None, session
        content = results.messages[message_id].content
        assert "This session was cancelled" in content and label in content
        lines[session] = message_id

    sent = [event for event in league.events if event[0] == "send" and event[1] == RESULTS]
    deleted = [index for index, event in enumerate(league.events)
               if event[0] == "delete_channel" and event[1] == SUBMISSION_CHANNEL]
    assert deleted, "the submission channel was not deleted"
    posted_at = [league.events.index(event) for event in sent if event[2] in lines.values()]
    assert len(posted_at) == 2 and max(posted_at) < deleted[0]
    texts = league.texts(RESULTS)
    [note] = [index for index, text in enumerate(texts) if RESULTS_NOTE in text]
    assert all(index < note for index, text in enumerate(texts)
               if "This session was cancelled" in text)
    assert "⏭️" in "\n".join(league.texts(SUBMISSION_CHANNEL))
    assert await _closed(league)


async def test_a_refused_deletion_of_an_all_cancelled_channel_stops_only_the_round_s_lane(
    tmp_path,
):
    """Discord refuses to delete round 3's submission channel. The admin types CANCELLED for the
    race. When the queue runs, both cancelled lines are posted and recorded in the results
    channel, then round 3's own lane stops at the channel's deletion; the league lane is not
    stopped, and the channel stands."""
    league = await _league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).delete_fails = http_error(
        status=403, text="Missing Permissions"
    )

    await _cancel_the_race(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path, LANE)
    assert job is not None and job["name"] == "delete_channel"
    assert await stopped_job(league.db_path, "league") is None
    assert all(message_id is not None for message_id in (await _recorded(league)).values())
    assert not any(event[0] == "delete_channel" and event[1] == SUBMISSION_CHANNEL
                   for event in league.events)


# ---------------------------------------------------------------------------
# The round ends cancelled (#439, slice 8, Part E)
# ---------------------------------------------------------------------------


async def test_a_round_whose_every_session_is_cancelled_ends_cancelled_with_its_audit(tmp_path):
    """Round 3 awaits its results, its qualifying entered as cancelled; the admin types CANCELLED
    for the race. When the queue runs, round 3 is CANCELLED and marked as not held, and one
    `round.status` audit records the admin moving it from AWAITING_RESULTS to CANCELLED
    (core :232-234; constitution V). Rounds 1, 2 and 4 keep their statuses."""
    league = await _league(tmp_path)
    before = await league.rows(
        "SELECT id, status FROM rounds WHERE division_id = ? AND id != ? ORDER BY id", PRO, ROUND_3
    )

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _round(league) == ("CANCELLED", 1)
    assert await _round_audits(league) == [(ADMIN_ID, "AWAITING_RESULTS", "CANCELLED")]
    after = await league.rows(
        "SELECT id, status FROM rounds WHERE division_id = ? AND id != ? ORDER BY id", PRO, ROUND_3
    )
    assert [(row["id"], row["status"]) for row in after] == [
        (row["id"], row["status"]) for row in before
    ]


async def test_a_resubmission_entering_every_session_cancelled_ends_the_round_cancelled(
    tmp_path, monkeypatch,
):
    """Round 3 of Pro is in its penalty review, awaiting its report verdicts. Alex, a league
    manager, presses Resubmit and types CANCELLED for its qualifying and then for its race. When
    the queue runs, `results.submission.all_cancelled` reads the drivers' names and then runs
    `end_round` (the round holding standings to withdraw), round 3 is
    CANCELLED and marked as not held, and one `round.status` audit records it moving from
    AWAITING_REPORT_VERDICTS to CANCELLED; the review is not asked again (core :232-234)."""
    from leaguebot.core.db.database import get_connection
    from tests.results.test_resubmission_changes import (
        REVIEW_OPEN,
        ROUND_ID as REVIEWED_ROUND,
        _changes,
        _press_resubmit,
        _reviewing,
        _type,
    )

    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    asked_before = len(await _changes(league, REVIEW_OPEN))

    await _type(league, "CANCELLED")
    await _type(league, "CANCELLED")

    assert await stopped_job(league.db_path) is None
    [handed] = await _changes(league, ALL_CANCELLED_KIND)
    names = [row["name"] for row in await step_rows(league.db_path, handed["id"])]
    assert names[:2] == ["names", "end_round"]
    async with get_connection(league.db_path) as db:
        [row] = await (await db.execute(
            "SELECT status, not_held, division_id FROM rounds WHERE id = ?", (REVIEWED_ROUND,)
        )).fetchall()
        audits = await (await db.execute(
            "SELECT old_value, new_value FROM audit_entries "
            "WHERE change_type = 'round.status' AND division_id = ? ORDER BY id",
            (row["division_id"],),
        )).fetchall()
    assert (row["status"], row["not_held"]) == ("CANCELLED", 1)
    assert [(audit["old_value"], audit["new_value"]) for audit in audits] == [
        ("AWAITING_REPORT_VERDICTS", "CANCELLED")
    ]
    assert len(await _changes(league, REVIEW_OPEN)) == asked_before


async def test_the_last_round_ending_cancelled_finishes_the_division_and_winds_the_season_down(
    tmp_path,
):
    """Every other round of the season is done: Pro's rounds 1 and 2 are final and its round 4
    was called off, and Am is finished. The admin types CANCELLED for round 3's race, its
    qualifying already entered as cancelled. When the queue runs, round 3 ends cancelled, Pro
    finishes, the season moves on to pending completion and its wind-down is asked. A
    `/season complete` then is asked of the queue, not refused on round 3's account (core
    :232-234)."""
    league = await _league(tmp_path)
    await league.write("UPDATE rounds SET status = 'FINAL' WHERE id IN (?, ?)",
                       round_id(PRO, 1), round_id(PRO, 2))
    await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", round_id(PRO, 4))
    await league.write("UPDATE rounds SET status = 'FINAL' WHERE division_id = ?", AM)
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", AM)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _round(league) == ("CANCELLED", 1)
    [division] = await league.rows("SELECT status FROM divisions WHERE id = ?", PRO)
    assert division["status"] == "FINISHED"
    [season] = await league.rows("SELECT stage FROM seasons WHERE id = ?", SEASON_ID)
    assert season["stage"] == "PENDING_COMPLETION"
    kinds = [row["kind"] for row in await change_rows(league.db_path)]
    assert "season.wind_down" in kinds

    await complete_season(league)

    assert "season.complete" in [row["kind"] for row in await change_rows(league.db_path)]


async def test_the_round_ending_cancelled_is_struck_through_on_its_calendar_reposted(tmp_path):
    """Pro's calendar was posted. Round 3 ends cancelled when the admin types CANCELLED for its
    race: Pro's calendar is posted again, once, with round 3 shown as cancelled (core :427)."""
    league = await _league(tmp_path)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    calendar = league.texts(CALENDAR)
    assert len(calendar) == 1 and calendar[0].count("— Cancelled") == 1


async def test_no_calendar_is_posted_where_none_stood(tmp_path):
    """Pro's calendar was never posted. Round 3 ends cancelled when the admin types CANCELLED for
    its race, and no calendar is posted (core, Announcing a cancellation)."""
    league = await _league(tmp_path)
    await league.write("UPDATE divisions SET calendar_message_id = NULL WHERE id = ?", PRO)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _round(league) == ("CANCELLED", 1)
    assert league.texts(CALENDAR) == []


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
async def test_a_discarded_calendar_is_named_with_division_calendar_sync(tmp_path):
    """Discord refuses Pro's calendar repost after round 3 ends cancelled, and a league admin
    discards it. The round stays cancelled, and the log channel's `ROUND_CANCELLED` line names
    the calendar as not notified, with `/division calendar-sync` to put it right (core :427;
    W1)."""
    league = await _league(tmp_path)
    league.channel(CALENDAR).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Access")

    await _cancel_the_race(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "post_calendar"
    await discard_job(league.bot)

    assert await _round(league) == ("CANCELLED", 1)
    [line] = _ended_lines(league)
    assert f"\n  not notified: {CALENDAR_DISCARDED}" in line


async def test_the_round_s_ending_cannot_be_discarded(tmp_path):
    """The round's ending stops on a fault of the database. A league admin's Discard is refused,
    saying round 3 would never finish and season 3 could not be completed; the queue stays
    stopped at `end_round`, round 3 still awaits its results, and nothing is recorded as
    discarded. Once the fault is lifted, Retry ends the round cancelled (core :137)."""
    league = await _league(tmp_path)

    with patch("leaguebot.core.services.season_service.refresh_division_status_on",
               new=AsyncMock(side_effect=RuntimeError("disk I/O error"))):
        await _cancel_the_race(league)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "end_round"
        discarding = await discard_job(league.bot)

        assert CANNOT_DISCARD_END in reply(discarding)
        assert await _stopped_at(league) == "end_round"
        assert await league.rows(
            "SELECT * FROM audit_entries WHERE change_type = 'CHANGE_JOB_DISCARDED'"
        ) == []
        assert await _round(league) == ("AWAITING_RESULTS", 0)

    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _round(league) == ("CANCELLED", 1)


async def test_the_round_s_ending_is_written_once_to_the_log_channel(tmp_path):
    """Round 3 ends cancelled when the admin types CANCELLED for its race. The log channel gets
    one line, naming the admin, `ROUND_CANCELLED | Success`, the division, the round and the
    reason, "every session was entered as not held" (core :138)."""
    league = await _league(tmp_path)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    [line] = _ended_lines(league)
    assert f"<@{ADMIN_ID}>" in line.split(ENDED_LINE)[0]
    assert "\n  division: Pro\n  round: 3\n  reason: every session was entered as not held" in line


async def test_an_ending_that_finds_a_session_not_cancelled_writes_nothing_and_still_closes_the_channel(
    tmp_path,
):
    """The admin types CANCELLED for round 3's race and the turn hands the round's ending to the
    league. Before it runs, round 3's qualifying is found entered with results after all. When
    the ending runs, round 3 keeps awaiting its results, unmarked; no `round.status` audit is
    written and no `ROUND_CANCELLED` line; round 3's submission channel is still closed."""
    league = await _league(tmp_path)

    await _cancel_the_race(league)
    for _ in range(40):
        if any(row["kind"] == ALL_CANCELLED_KIND for row in await change_rows(league.db_path)):
            break
        await run_queue(league.bot, steps=1)
    else:
        raise AssertionError("the round's ending was never asked")
    await league.write(
        "UPDATE session_results SET status = 'ACTIVE' WHERE round_id = ? AND session_type = ?",
        ROUND_3, "FEATURE_QUALIFYING",
    )
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _round(league) == ("AWAITING_RESULTS", 0)
    assert await _round_audits(league) == []
    assert _ended_lines(league) == []
    assert await _closed(league)


async def test_a_first_pass_ending_cancelled_plans_no_names_and_no_standings_jobs(tmp_path):
    """A first submission of round 3 ends cancelled, so the round holds no standings. Its change
    opens with `end_round`, and plans no reading of names, no standings post and no deletion of
    one (results :383)."""
    league = await _league(tmp_path)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    [handed] = [row for row in await change_rows(league.db_path)
                if row["kind"] == ALL_CANCELLED_KIND]
    names = [row["name"] for row in await step_rows(league.db_path, handed["id"])]
    assert names[0] == "end_round"
    assert not {"names", "post_standings", "delete_message"} & set(names)


# ---------------------------------------------------------------------------
# Its notices, and its call and forecast left until the day after (Part E, E3)
# ---------------------------------------------------------------------------


def _sends(league: SeasonLeague, channel_id: int, phrase: str) -> list[Any]:
    """Each send to channel *channel_id* whose content carries *phrase*."""
    from tests.support.season_league import _content

    return [call for call in league.channel(channel_id).send.call_args_list
            if phrase in _content(call)]


@pytest.mark.parametrize("weather, attendance", [
    pytest.param(True, True, id="every module on"),
    pytest.param(False, False, id="weather and attendance off"),
])
async def test_a_round_ending_cancelled_posts_the_round_cancelled_notices_in_check_in_forecast_and_results(
    tmp_path, weather, attendance,
):
    """Round 3 ends cancelled when the admin types CANCELLED for its race. Each module that is on
    posts its "round cancelled" notice once, in `/round cancel`'s words: Pro's check-in channel a
    notice opening with Pro's role and pinging it, Pro's forecast and results channels a silent
    note. A module that is off posts nothing (owner, check 1, "Post the notices"; Q-E5)."""
    league = await _league(tmp_path, weather=weather, attendance=attendance)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [results] = _sends(league, RESULTS, RESULTS_NOTE)
    assert NOTICE_HEADING in str(results.args[0]) and results.kwargs.get("silent") is True
    forecasts = _sends(league, FORECAST, FORECAST_NOTE)
    checkins = _sends(league, CHECKIN, CHECKIN_NOTICE)
    if not weather:
        assert forecasts == [] and checkins == []
        return
    [forecast] = forecasts
    assert NOTICE_HEADING in str(forecast.args[0]) and forecast.kwargs.get("silent") is True
    [checkin] = checkins
    assert str(checkin.args[0]).startswith(f"<@&{PRO_ROLE}>")
    assert NOTICE_HEADING in str(checkin.args[0])
    assert not checkin.kwargs.get("silent")
    assert checkin.kwargs["allowed_mentions"].roles is True


async def test_a_round_ending_cancelled_leaves_its_call_and_forecast_until_the_day_after(
    tmp_path,
):
    """Weather and attendance are on, and round 3's check-in call, last notice and announcement
    stand in Pro's check-in channel. Round 3 ends cancelled when the admin types CANCELLED for its
    race: none of the three is deleted, the call's record stays, nothing is deleted in Pro's
    forecast channel, and round 3's timed work, its clean-ups a day after the start among it, is
    not removed (owner, check 1, "Day after, as usual"; attendance :75; weather :143)."""
    league = await _league(tmp_path, weather=True, attendance=True)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _round(league) == ("CANCELLED", 1)
    assert all(message_id in league.channel(CHECKIN).messages for message_id in CALL_MESSAGES)
    assert not [event for event in league.events
                if event[0] == "delete" and event[1] in (CHECKIN, FORECAST)]
    assert len(await league.rows(
        "SELECT * FROM rsvp_embed_messages WHERE round_id = ?", ROUND_3
    )) == 1
    assert ROUND_3 not in league.unarmed


async def test_a_discarded_notice_is_named_in_the_round_s_log_line(tmp_path):
    """Weather is on, and Discord refuses Pro's forecast note after round 3 ends cancelled. The
    queue stops at the note; a league admin discards it. The results note is still posted, and
    the round's one `ROUND_CANCELLED` line names the forecast channel as not notified, the notice
    discarded (core, Announcing a cancellation :495)."""
    league = await _league(tmp_path, weather=True)
    league.channel(FORECAST).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Access")

    await _cancel_the_race(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "notify_forecast"
    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(_sends(league, RESULTS, RESULTS_NOTE)) == 1
    [line] = _ended_lines(league)
    assert ("\n  not notified: **Pro** — forecast channel: the notice could not be posted, and a "
            "league admin discarded it") in line


# ---------------------------------------------------------------------------
# Its check-in record, beneath its line (Part E, E3b; owner, check 2, "Write the record")
# ---------------------------------------------------------------------------


#: The heading of round 3's check-in record in the log channel.
RECORD_HEADING = "\n  check-in, Pro, Round 3 (Silverstone):"


async def test_a_round_ending_cancelled_writes_its_check_in_record_beneath_its_line(tmp_path):
    """Attendance is on. Round 3's check-in recorded Lewis as accepted and Max as giving no
    answer, and the reserves were distributed, Lewis sent to Scuderia Ferrari. Round 3 ends
    cancelled when the admin types CANCELLED for its race: beneath its `ROUND_CANCELLED` line,
    after the reason, the log channel shows the round's check-in, every driver grouped by their
    answer and Lewis to Scuderia Ferrari, as `/round cancel` writes it (attendance :130-131)."""
    from tests.support.season_league import FERRARI, LEWIS, profile_id

    league = await _league(tmp_path, attendance=True)
    await league.write(
        "UPDATE driver_round_attendance SET assigned_team_id = ? "
        "WHERE round_id = ? AND driver_profile_id = ?", FERRARI, ROUND_3, profile_id(LEWIS),
    )

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [line] = _ended_lines(league)
    record = (
        RECORD_HEADING
        + "\n    accepted: `<@101>`"
        + "\n    tentative: none"
        + "\n    declined: none"
        + "\n    no answer: `<@102>`"
        + "\n    reserves: `<@101>` to Scuderia Ferrari"
    )
    assert record in line
    assert line.index("reason: every session was entered as not held") < line.index(record)


#: How a discarded read of round 3's check-in is named beneath the round's line (owner, check 2).
CHECK_IN_DISCARDED = "the check-in record of round 3 in **Pro** was not written"


def _check_in_read_failing() -> ExitStack:
    """Core's `checkin_record` raises, as a fault of the database would: in its own module, and
    in each module that holds its own reference to it."""
    stack = ExitStack()
    failing = AsyncMock(side_effect=RuntimeError("disk I/O error"))
    for module_name in ("leaguebot.core.services.cancellation_notice_service",
                        "leaguebot.core.services.cancellation_changes",
                        "leaguebot.results.services.submission_changes"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(module, "checkin_record"):
            stack.enter_context(patch.object(module, "checkin_record", failing))
    return stack


async def test_a_discarded_check_in_record_is_named_and_the_round_still_ends_cancelled(tmp_path):
    """Attendance is on and round 3's check-in call was posted. Round 3 ends cancelled when the
    admin types CANCELLED for its race, but reading its check-in fails on a fault of the
    database, and the queue stops at `read_check_in`. A league admin discards it. Round 3 is
    CANCELLED, the queue runs on with nothing left stopped, and the round's one
    `ROUND_CANCELLED` line carries no check-in record and names the record, under what was not
    done, as not written (attendance :130; owner, check 2, "Write the record")."""
    league = await _league(tmp_path, attendance=True)

    with _check_in_read_failing():
        await _cancel_the_race(league)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "read_check_in"
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _round(league) == ("CANCELLED", 1)
    [line] = _ended_lines(league)
    assert f"\n  not done: {CHECK_IN_DISCARDED}" in line
    assert RECORD_HEADING not in line


async def test_no_check_in_record_is_written_where_no_call_was_posted(tmp_path):
    """Attendance is on, but round 3's check-in call was never posted, so it recorded nobody.
    Round 3 ends cancelled when the admin types CANCELLED for its race: its `ROUND_CANCELLED`
    line is written with no check-in record beneath it (attendance :130)."""
    league = await _league(tmp_path, attendance=True)
    await league.write("DELETE FROM driver_round_attendance WHERE round_id = ?", ROUND_3)
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", ROUND_3)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [line] = _ended_lines(league)
    assert "check-in, Pro" not in line


async def test_no_check_in_record_is_written_while_attendance_is_off(tmp_path):
    """Attendance is off, though round 3's check-in recorded Lewis and Max. Round 3 ends
    cancelled when the admin types CANCELLED for its race: its `ROUND_CANCELLED` line is written
    with no check-in record beneath it (attendance :131)."""
    league = await _league(tmp_path)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [line] = _ended_lines(league)
    assert "check-in, Pro" not in line


# ---------------------------------------------------------------------------
# Its standings, withdrawn after a resubmission (Part E, E5; owner, check 1, "Fold all three in")
# ---------------------------------------------------------------------------

#: Round 4 of the reviewed division, final, and round 2 before it, in the resubmission's league.
LATER_ROUND, EARLIER_ROUND = 22, 20
#: Round 4's standings post, standing in the standings channel before the resubmission: below
#: 9000, where the channel double's ids for the messages it is sent begin, so no message sent in
#: an earlier test can share it.
LATER_STANDINGS = 8802


async def _resubmitting(tmp_path: Any, monkeypatch: Any) -> Any:
    """`test_resubmission_changes.py`'s league: Pro (division 11) of season 1, its round 3 at
    Silverstone in its penalty review, Lewis (101) first and Max (102) second in its Feature Race,
    its provisional results table and standings posted. Alex, a league manager, has pressed
    Resubmit, and the queue has run."""
    from tests.results.test_resubmission_changes import _press_resubmit, _reviewing

    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    return league


async def _seed_later_round(league: Any) -> None:
    """Round 4 of Pro at Spa, final, won by Max alone for 25 points; its standings post
    `LATER_STANDINGS` stands, Max first on 43 points (18 + 25) and Lewis second on 25."""
    from leaguebot.core.db.database import get_connection
    from tests.results.test_review_open_change import DIVISION_ID, STANDINGS_CHANNEL
    from tests.support.review_league import LEWIS, MAX, MAX_PROFILE

    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "track_name, status) VALUES (?, ?, 4, '2026-02-08T18:00:00+00:00', 'NORMAL', "
            "'Spa', 'FINAL')", (LATER_ROUND, DIVISION_ID),
        )
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')", (LATER_ROUND, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, outcome, base_time_ms, points_awarded, "
            "driver_profile_id) VALUES (?, ?, 3001, 1, 'CLASSIFIED', 3601000, 25, ?)",
            (session.lastrowid, MAX, MAX_PROFILE),
        )
        for driver, position, points, message_id in (
            (MAX, 1, 43, LATER_STANDINGS), (LEWIS, 2, 25, None),
        ):
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
                "standing_position, total_points, standings_message_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (LATER_ROUND, DIVISION_ID, driver, position, points, message_id),
            )
        await db.commit()
    league.channel(STANDINGS_CHANNEL).seed(LATER_STANDINGS, "round 4 standings")


async def _snapshot_rounds(league: Any, round_id: int) -> tuple[int, int]:
    """How many driver and team standings rows *round_id* holds."""
    from leaguebot.core.db.database import get_connection

    async with get_connection(league.db_path) as db:
        counts = []
        for table in ("driver_standings_snapshots", "team_standings_snapshots"):
            [row] = await (await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE round_id = ?", (round_id,)
            )).fetchall()
            counts.append(row["n"])
    return counts[0], counts[1]


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
async def test_a_resubmission_entering_every_session_cancelled_withdraws_the_round_s_standings(
    tmp_path, monkeypatch,
):
    """Round 3's provisional standings were posted when its review opened. Alex resubmits and
    types CANCELLED for its qualifying and then for its race. When the queue runs, round 3 holds
    no driver or team standings rows any more, and each of its standings posts is deleted, by a
    job of `results.submission.all_cancelled` run after both sessions' "This session was
    cancelled." lines are posted (owner, check 1)."""
    from tests.results.test_resubmission_changes import ROUND_ID as REVIEWED_ROUND, _changes, _type
    from tests.results.test_review_open_change import RESULTS_CHANNEL, STANDINGS_CHANNEL

    league = await _resubmitting(tmp_path, monkeypatch)
    standings = league.sent_to(STANDINGS_CHANNEL)
    assert standings and (await _snapshot_rounds(league, REVIEWED_ROUND))[0]

    await _type(league, "CANCELLED")
    await _type(league, "CANCELLED")

    assert await stopped_job(league.db_path) is None
    assert await _snapshot_rounds(league, REVIEWED_ROUND) == (0, 0)
    assert set(standings) <= set(league.deleted_in(STANDINGS_CHANNEL))
    [handed] = await _changes(league, ALL_CANCELLED_KIND)
    names = [row["name"] for row in await step_rows(league.db_path, handed["id"])]
    assert names.count("delete_message") >= len(standings)
    # A message sent to the results channel and since deleted, the earlier provisional table
    # among them, is no note line.
    standing = league.channel(RESULTS_CHANNEL).messages
    lines = [index for index, event in enumerate(league.events)
             if event[0] == "send" and event[1] == RESULTS_CHANNEL
             and (sent := standing.get(event[2])) is not None
             and "This session was cancelled" in str(sent.content)]
    assert len(lines) == 2
    deleted_at = [league.events.index(("delete", STANDINGS_CHANNEL, mid)) for mid in standings]
    assert min(deleted_at) > max(lines)


async def test_the_earlier_provisional_tables_come_down_after_each_session_s_note_stands(
    tmp_path, monkeypatch,
):
    """Round 3's provisional Feature Race table was posted in Pro's results channel when its
    review opened. Alex resubmits and types CANCELLED for both sessions. When the queue runs, the
    race's "This session was cancelled." line is posted and recorded as the race's results
    message, and only then is the earlier table deleted: no table of the first submission is
    left standing beside the notes (owner, check 1; results :412)."""
    from tests.results.test_resubmission_changes import ROUND_ID as REVIEWED_ROUND, _rows, _type
    from tests.results.test_review_open_change import RESULTS_CHANNEL

    league = await _resubmitting(tmp_path, monkeypatch)
    earlier = league.sent_to(RESULTS_CHANNEL)
    assert earlier

    await _type(league, "CANCELLED")
    await _type(league, "CANCELLED")

    assert await stopped_job(league.db_path) is None
    [race] = await _rows(
        league, "SELECT results_message_id FROM session_results "
        "WHERE round_id = ? AND session_type = 'FEATURE_RACE'", REVIEWED_ROUND,
    )
    note = race["results_message_id"]
    assert note not in earlier
    assert "This session was cancelled" in str(league.channel(RESULTS_CHANNEL).messages[note].content)
    for message_id in earlier:
        assert message_id not in league.channel(RESULTS_CHANNEL).messages
        assert (league.events.index(("delete", RESULTS_CHANNEL, message_id))
                > league.events.index(("send", RESULTS_CHANNEL, note)))


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
async def test_a_later_round_s_standings_are_recomputed_without_the_round_and_posted_again(
    tmp_path, monkeypatch,
):
    """Round 4 of Pro, after round 3, is final, Max having won it for 25 points; its standings
    were posted with Max on 43 points, counting his 18 from round 3. Alex resubmits round 3 and
    types CANCELLED for both its sessions. When the queue runs, round 4's standings are worked
    out again without round 3, Max on 25 points, and posted again by the one standings job the
    change plans: round 4's standings post, the one standing or its replacement, shows Max on 25
    points (owner, check 1)."""
    from tests.results.test_resubmission_changes import _changes, _rows, _type
    from tests.results.test_review_open_change import STANDINGS_CHANNEL
    from tests.support.review_league import MAX

    league = await _resubmitting(tmp_path, monkeypatch)
    await _seed_later_round(league)

    await _type(league, "CANCELLED")
    await _type(league, "CANCELLED")

    assert await stopped_job(league.db_path) is None
    [row] = await _rows(
        league, "SELECT total_points, standings_message_id FROM driver_standings_snapshots "
        "WHERE round_id = ? AND driver_user_id = ?", LATER_ROUND, MAX,
    )
    assert row["total_points"] == 25
    [handed] = await _changes(league, ALL_CANCELLED_KIND)
    names = [step["name"] for step in await step_rows(league.db_path, handed["id"])]
    assert names.count("post_standings") == 1
    # The standings job edits the post in place where the new text fits in one message, and
    # replaces it otherwise: either way the post round 4's snapshot names shows Max on 25.
    post = league.channel(STANDINGS_CHANNEL).messages[row["standings_message_id"]]
    assert f"<@{MAX}> — **25 pts**" in str(post.content), post.content


async def test_the_next_round_s_movement_is_measured_against_the_round_before(
    tmp_path, monkeypatch,
):
    """Pro's round 2 is final and holds standings, and so do round 3, in its review, and round 4,
    final. Round 4's movement is measured against round 3. Alex resubmits round 3 and types
    CANCELLED for both its sessions. When the queue runs, round 4's movement is measured against
    round 2 instead, round 3 holding no standings any more (owner, check 1)."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.standings_service import reference_round_id
    from tests.results.test_resubmission_changes import ROUND_ID as REVIEWED_ROUND, _type
    from tests.results.test_review_open_change import DIVISION_ID
    from tests.support.review_league import LEWIS

    league = await _resubmitting(tmp_path, monkeypatch)
    await _seed_later_round(league)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "track_name, status) VALUES (?, ?, 2, '2026-01-25T18:00:00+00:00', 'NORMAL', "
            "'Monza', 'FINAL')", (EARLIER_ROUND, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
            "standing_position, total_points) VALUES (?, ?, ?, 1, 0)",
            (EARLIER_ROUND, DIVISION_ID, LEWIS),
        )
        await db.commit()
    assert await reference_round_id(league.db_path, DIVISION_ID, LATER_ROUND) == REVIEWED_ROUND

    await _type(league, "CANCELLED")
    await _type(league, "CANCELLED")

    assert await stopped_job(league.db_path) is None
    assert await reference_round_id(league.db_path, DIVISION_ID, LATER_ROUND) == EARLIER_ROUND


async def test_a_discarded_standings_deletion_is_named_for_deletion_by_hand(
    tmp_path, monkeypatch,
):
    """Round 3's provisional standings post stands, and Discord refuses to delete it. Alex
    resubmits round 3 and types CANCELLED for both its sessions. The queue stops at the post's
    deletion; a league admin discards it. Round 3 still ends cancelled, the post still stands,
    and the round's `ROUND_CANCELLED` line names it as an earlier message that could not be
    deleted, to delete by hand."""
    from tests.results.test_resubmission_changes import ROUND_ID as REVIEWED_ROUND, _rows, _type
    from tests.results.test_review_open_change import STANDINGS_CHANNEL

    league = await _resubmitting(tmp_path, monkeypatch)
    [standing] = league.sent_to(STANDINGS_CHANNEL)
    league.channel(STANDINGS_CHANNEL).messages[standing].delete = AsyncMock(
        side_effect=http_error(status=403, text="Missing Permissions")
    )

    await _type(league, "CANCELLED")
    await _type(league, "CANCELLED")
    assert await _stopped_at(league) == "delete_message"
    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [row] = await _rows(league, "SELECT status FROM rounds WHERE id = ?", REVIEWED_ROUND)
    assert row["status"] == "CANCELLED"
    assert standing in league.channel(STANDINGS_CHANNEL).messages
    [line] = _ended_lines(league)
    assert "could not be deleted" in line and "delete it by hand" in line
