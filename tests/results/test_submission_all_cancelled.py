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
"""
from __future__ import annotations

from typing import Any

from leaguebot.core.models.round import RoundFormat
from leaguebot.results.services.result_submission_service import get_sessions_for_format
from leaguebot.results.utils import results_formatter
from tests.support.change_queue import (
    MANAGER_ROLE_ID,
    change_rows,
    http_error,
    run_queue,
    stopped_job,
    typed_message,
)
from tests.support.season_league import (
    ADMIN_ID,
    DIVISIONS,
    PRO,
    SUBMISSION_CHANNEL,
    SeasonLeague,
    accept_session,
    ongoing_league,
    open_submission,
    round_id,
)

ALL_CANCELLED_KIND = "results.submission.all_cancelled"
CLOSE_KIND = "results.submission.close_channel"
ROUND_3 = round_id(PRO, 3)
RESULTS = DIVISIONS[PRO][3].results
LANE = f"channel:{SUBMISSION_CHANNEL}"

QUALIFYING, RACE = (
    results_formatter.format_session_label(session, is_sprint=False)
    for session in get_sessions_for_format(RoundFormat.NORMAL)[:2]
)

async def _league(tmp_path: Any) -> SeasonLeague:
    """The league of the module docstring, the race not yet entered."""
    league = await ongoing_league(tmp_path)
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


async def _closed(league: SeasonLeague) -> bool:
    [row] = await league.rows(
        "SELECT closed FROM round_submission_channels WHERE round_id = ?", ROUND_3
    )
    return bool(row["closed"])


async def test_every_session_cancelled_posts_each_line_recorded_before_the_channel_closes(
    tmp_path,
):
    """Round 3's qualifying was entered as cancelled; the admin types CANCELLED for the race.
    When the queue runs, `results.submission.all_cancelled` is asked on the league lane and Pro's
    results channel carries one "This session was cancelled." line for the qualifying and one for
    the race, each recorded as its session's results message. Only after both are posted does
    `results.submission.close_channel`, on round 3's lane, say "⏭️" in the submission channel and
    delete it, closing the submission row. No penalty review is asked and the queue is not
    stopped."""
    league = await _league(tmp_path)

    await _cancel_the_race(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    changes = await change_rows(league.db_path)
    [handed] = [row for row in changes if row["kind"] == ALL_CANCELLED_KIND]
    assert handed["lane"] == "league"
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
