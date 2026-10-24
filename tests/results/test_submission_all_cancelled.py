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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
async def test_the_round_ending_cancelled_is_struck_through_on_its_calendar_reposted(tmp_path):
    """Pro's calendar was posted. Round 3 ends cancelled when the admin types CANCELLED for its
    race: Pro's calendar is posted again, once, with round 3 shown as cancelled (core :427)."""
    league = await _league(tmp_path)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    calendar = league.texts(CALENDAR)
    assert len(calendar) == 1 and calendar[0].count("— Cancelled") == 1


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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
    pytest.param(True, True, id="every module on",
                 marks=pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)),
    pytest.param(False, False, id="weather and attendance off",
                 marks=pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)),
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_HELD_UNBUILT)
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
