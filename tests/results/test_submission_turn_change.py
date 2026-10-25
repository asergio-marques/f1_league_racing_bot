"""Each results paste a turn on the round's lane (#439, slice 6b, C47).

A league manager's message in a round's open submission channel is taken by
`ResultsCog.on_message`: it is marked ⏳ at once and `results.submission.turn` is asked for it, as
the member's, on the round's own lane (``channel:<submission channel>``). When its turn comes the
change judges the paste against the session the round then expects, saves it or refuses it, posts
"✅ … results saved." and the next session's prompt, and lastly clears the mark. The last paste
hands the penalty review to the league (`results.review.open`) and says it is being opened with its
job number (results spec :382). A message typed while a turn still waits in the channel is turned
away at once with a note and deleted. A message from anyone but a league manager is ignored.

The league is `ongoing_league`'s, results on: Pro's round 3 awaits results, its submission open in
channel `SUBMISSION_CHANNEL`; the admin (77) holds the league managers' role. The division's seats,
as the paste's validation reads them, are Lewis (101) and Max (102) at Ferrari, shorthand FER;
Charles (103) races in Am.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.models.round import RoundFormat
from leaguebot.core.utils.member_names import member_named
from leaguebot.results.services import result_submission_service
from leaguebot.results.services.result_submission_service import (
    DivisionValidationData,
    get_sessions_for_format,
)
from leaguebot.results.utils import results_formatter
from tests.support.change_queue import (
    MANAGER_ROLE_ID,
    MARK,
    change_rows,
    member_interaction,
    queued_log_lines,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    typed_message,
)
from tests.support.review_league import block_queue
from tests.support.season_league import (
    ADMIN_ID,
    DIVISIONS,
    LEWIS,
    PRO,
    SEASON_NUMBER,
    SUBMISSION_CHANNEL,
    SeasonLeague,
    accept_session,
    ongoing_league,
    open_submission,
    round_id,
)

KIND = "results.submission.turn"
REVIEW_OPEN = "results.review.open"
ROUND_3 = round_id(PRO, 3)
RESULTS = DIVISIONS[PRO][3].results
LANE = f"channel:{SUBMISSION_CHANNEL}"


QUALIFYING, RACE = (
    results_formatter.format_session_label(session, is_sprint=False)
    for session in get_sessions_for_format(RoundFormat.NORMAL)[:2]
)
QUALI_PASTE = "1, <@101>, FER, Soft, 1:19.000, N/A\n2, <@102>, FER, Soft, 1:19.500, +0.500"
RACE_PASTE = "1, <@101>, FER, 1:30:00.000, 1:20.000, N/A\n2, <@102>, FER, +5.000, 1:21.000, N/A"
#: Charles races in Am, not Pro.
OUTSIDER_PASTE = "1, <@103>, FER, Soft, 1:19.000, N/A"

_SEATED = DivisionValidationData(
    division_driver_ids={101, 102}, team_of_role={811: 31}, reserve_team_role_id=None,
    driver_team_map={101: 31, 102: 31}, reserve_driver_ids=set(), team_names={31: "Ferrari"},
    team_of_shorthand={"fer": 31},
)


async def _league(tmp_path: Any, monkeypatch: Any) -> SeasonLeague:
    """The league of the module docstring."""
    league = await ongoing_league(tmp_path)
    league.roles_held[ADMIN_ID] = {MANAGER_ROLE_ID}
    await league.write("UPDATE rounds SET status = 'AWAITING_RESULTS' WHERE id = ?", ROUND_3)
    await open_submission(league, ROUND_3)
    sub = league.channel(SUBMISSION_CHANNEL)
    if not isinstance(getattr(sub, "posted", None), list):
        sub.posted = []
    monkeypatch.setattr(
        result_submission_service, "_build_division_validation_data",
        AsyncMock(return_value=_SEATED),
    )
    return league


async def _type(league: SeasonLeague, content: str, *, author: int = ADMIN_ID) -> Any:
    """*author* types *content* in round 3's submission channel, and the results cog hears it.
    Gives the message."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    message = typed_message(
        league.channel(SUBMISSION_CHANNEL), league._member(author), content,
        created_at=league.clock.now,
    )
    await ResultsCog(league.bot).on_message(message)
    return message


async def _sessions(league: SeasonLeague) -> list[tuple[str, str]]:
    rows = await league.rows(
        "SELECT session_type, status FROM session_results WHERE round_id = ? ORDER BY id", ROUND_3
    )
    return [(row["session_type"], row["status"]) for row in rows]


async def _turns(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


def _said(league: SeasonLeague) -> str:
    return "\n".join(league.texts(SUBMISSION_CHANNEL))


async def test_a_paste_is_marked_at_once_and_saved_with_the_next_prompt_when_its_turn_comes(
    tmp_path, monkeypatch,
):
    """The admin pastes round 3's qualifying. At once the paste is marked ⏳ and nothing is saved
    or posted. When the queue runs, the qualifying is saved, the channel says "✅ **Qualifying**
    results saved." and then asks for the race, and the mark is cleared."""
    league = await _league(tmp_path, monkeypatch)

    paste = await _type(league, QUALI_PASTE)
    assert MARK in paste.marks
    assert await _sessions(league) == []
    assert league.texts(SUBMISSION_CHANNEL) == []

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _sessions(league) == [("FEATURE_QUALIFYING", "ACTIVE")]
    saved, prompt = league.texts(SUBMISSION_CHANNEL)
    assert f"✅ **{QUALIFYING}** results saved." in saved
    assert f"📋 Submit **{RACE}** results" in prompt
    assert MARK not in paste.marks


async def _refused_lines(league: SeasonLeague) -> list[str]:
    """Every `RESULT_SUBMISSION_REJECTED` line written to the log channel, queued or posted, its
    mentions read without the backticks the log channel posts them in."""
    lines = [*await queued_log_lines(league.db_path), *league.bot.log_channel.sent]
    return [line.replace("`<@", "<@").replace(">`", ">") for line in lines
            if "RESULT_SUBMISSION_REJECTED" in line]


def _names_the_paste(line: str, *, reason: str, content: str,
                     session: str = "FEATURE_QUALIFYING") -> None:
    """*line* names season 3, Pro, round 3 and *session*, gives *reason* on a `reason:` line,
    and quotes the paste *content* as typed."""
    assert f"season: {SEASON_NUMBER}, division: 'Pro'" in line, line
    assert f"round: 3, session: {session}" in line, line
    assert "reason:" in line and reason in line, line
    assert content in line, line


async def test_a_paste_while_its_turn_waits_is_turned_away_with_a_note(tmp_path, monkeypatch):
    """The admin pastes the qualifying, then the race before the queue has reached the first.
    The second is deleted at once and a note in the channel says the earlier message is still
    waiting its turn; the refusal is logged, one ⛔ line naming the admin, season 3, round 3 and
    the qualifying still waited on (results :257; README :1781); only one turn is asked, and only
    the qualifying is saved when it runs."""
    league = await _league(tmp_path, monkeypatch)

    await _type(league, QUALI_PASTE)
    second = await _type(league, RACE_PASTE)

    assert second.gone
    assert "still waiting its turn" in _said(league)
    logged = "\n".join(await queued_log_lines(league.db_path)) + league.log()
    assert logged.count("⛔") == 1, logged
    line = logged[logged.index("⛔"):]
    assert f"<@{ADMIN_ID}>" in line and "Round 3" in line and QUALIFYING in line, line
    assert f"season {SEASON_NUMBER}" in line.lower(), line
    assert len(await _turns(league)) == 1
    await run_queue(league.bot)
    assert await _sessions(league) == [("FEATURE_QUALIFYING", "ACTIVE")]


async def test_a_paste_that_fails_validation_is_refused_by_its_job_with_a_line(
    tmp_path, monkeypatch,
):
    """The admin pastes a qualifying naming Charles, who races in Am. When its turn comes nothing
    is saved, the channel is told the driver is not registered in this division, the qualifying
    is asked for again, and the rejected input is logged (results spec :257)."""
    league = await _league(tmp_path, monkeypatch)

    await _type(league, OUTSIDER_PASTE)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _sessions(league) == []
    said = _said(league)
    assert "not registered in this division" in said
    assert f"📋 Submit **{QUALIFYING}** results" in said
    assert "<@103>" in "\n".join(await queued_log_lines(league.db_path)) + league.log()


async def test_a_paste_judged_against_the_session_expected_when_it_runs(tmp_path, monkeypatch):
    """The admin pastes a race while the qualifying is still expected; before the queue reaches
    it, the qualifying is saved. When the paste's turn runs it is judged as the race, the session
    the round then expects, and saved as the race."""
    league = await _league(tmp_path, monkeypatch)

    await _type(league, RACE_PASTE)
    await accept_session(league, ROUND_3, session="FEATURE_QUALIFYING")
    await run_queue(league.bot)

    assert ("FEATURE_RACE", "ACTIVE") in await _sessions(league)


async def test_cancelled_typed_is_confirmed_in_the_submission_channel_only(tmp_path, monkeypatch):
    """The admin types CANCELLED for the qualifying. When its turn comes the qualifying is saved
    as cancelled and the submission channel says "✅ **Qualifying** marked as CANCELLED."; nothing
    is posted in Pro's results channel (decision 17: the line comes with the round's tables)."""
    league = await _league(tmp_path, monkeypatch)

    await _type(league, "CANCELLED")
    await run_queue(league.bot)

    assert await _sessions(league) == [("FEATURE_QUALIFYING", "CANCELLED")]
    assert f"✅ **{QUALIFYING}** marked as CANCELLED." in _said(league)
    assert league.texts(RESULTS) == []


async def test_the_last_paste_asks_the_review_and_says_its_job_number(tmp_path, monkeypatch):
    """The qualifying is saved; the admin pastes the race, the last session. When its turn comes
    the race is saved, `results.review.open` is asked for round 3, and the channel says "⏳ The
    penalty review is being opened (job #N)", N being that change's first job."""
    league = await _league(tmp_path, monkeypatch)
    await accept_session(league, ROUND_3, session="FEATURE_QUALIFYING")

    await _type(league, RACE_PASTE)
    await run_queue(league.bot)

    assert ("FEATURE_RACE", "ACTIVE") in await _sessions(league)
    [review] = [row for row in await change_rows(league.db_path) if row["kind"] == REVIEW_OPEN]
    first = min(step["id"] for step in await step_rows(league.db_path, review["id"]))
    assert f"⏳ The penalty review is being opened (job #{first})" in _said(league)


async def test_every_session_cancelled_closes_the_channel_as_a_job(tmp_path, monkeypatch):
    """The qualifying was entered as cancelled; the admin types CANCELLED for the race too. When
    the queue runs, the channel says "⏭️", round 3's submission row is closed and its channel
    deleted by a job, no penalty review is asked, and round 3 ends cancelled, marked as not
    held."""
    league = await _league(tmp_path, monkeypatch)
    await accept_session(league, ROUND_3, session="FEATURE_QUALIFYING", status="CANCELLED")

    await _type(league, "CANCELLED")
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert "⏭️" in _said(league)
    [row] = await league.rows(
        "SELECT closed FROM round_submission_channels WHERE round_id = ?", ROUND_3
    )
    assert row["closed"] == 1
    assert any(event[0] == "delete_channel" and event[1] == SUBMISSION_CHANNEL
               for event in league.events)
    assert not [row for row in await change_rows(league.db_path) if row["kind"] == REVIEW_OPEN]
    [ended] = await league.rows("SELECT status, not_held FROM rounds WHERE id = ?", ROUND_3)
    assert (ended["status"], ended["not_held"]) == ("CANCELLED", 1)


async def test_a_refused_prompt_stops_the_queue_with_the_session_saved(tmp_path, monkeypatch):
    """Discord refuses the race's prompt in the submission channel. The qualifying's turn stops
    at `post_prompt`, on the round's lane, with the qualifying already saved."""
    league = await _league(tmp_path, monkeypatch)
    league.channel(SUBMISSION_CHANNEL).fail_when = (
        lambda content, _kwargs: "📋 Submit" in str(content)
    )

    await _type(league, QUALI_PASTE)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path, LANE)
    assert job is not None and job["name"] == "post_prompt"
    assert await _sessions(league) == [("FEATURE_QUALIFYING", "ACTIVE")]
    assert await stopped_job(league.db_path, "league") is None


@pytest.mark.parametrize("why", ["closed by a cancellation", "held by an amendment"])
async def test_a_paste_in_a_channel_held_by_an_amendment_or_closed_by_a_cancellation_is_refused_when_it_runs(
    tmp_path, monkeypatch, why,
):
    """The admin pastes the qualifying; before its turn comes, round 3's submission is closed by
    a cancellation, or round 2 of Pro is opened for amendment. When the turn runs nothing is
    saved and no prompt is posted, and one `RESULT_SUBMISSION_REJECTED` line is logged naming
    season 3, Pro, round 3 and the qualifying, the reason the channel was given, and the paste
    (results :261)."""
    league = await _league(tmp_path, monkeypatch)

    await _type(league, QUALI_PASTE)
    if why == "closed by a cancellation":
        await league.write(
            "UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?", ROUND_3
        )
    else:
        await league.write(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (?, 7700, '[\"FEATURE_RACE\"]', ?)",
            round_id(PRO, 2), league.clock.now.isoformat(),
        )
    await run_queue(league.bot)

    assert await _sessions(league) == []
    assert "📋 Submit" not in _said(league)
    [line] = await _refused_lines(league)
    _names_the_paste(
        line, content=QUALI_PASTE,
        reason=("Round 3's results submission has been closed" if why == "closed by a cancellation"
                else "Round 2 of this division is being amended"),
    )


async def test_a_paste_whose_round_is_cancelled_before_its_turn_is_logged(tmp_path, monkeypatch):
    """The admin pastes round 3's qualifying; before its turn comes, round 3 is cancelled. When
    the turn runs nothing is saved, the channel is told the round has been cancelled, and one
    `RESULT_SUBMISSION_REJECTED` line is logged naming season 3, Pro, round 3 and the qualifying,
    with that reason and the paste (results :261)."""
    league = await _league(tmp_path, monkeypatch)

    await _type(league, QUALI_PASTE)
    await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", ROUND_3)
    await run_queue(league.bot)

    assert await _sessions(league) == []
    assert "Round 3 has been cancelled" in _said(league)
    [line] = await _refused_lines(league)
    _names_the_paste(line, content=QUALI_PASTE, reason="Round 3 has been cancelled")


async def test_a_fastest_lap_override_refused_is_logged(tmp_path, monkeypatch):
    """The qualifying is saved; the admin pastes the race with "FL: <@103>", Charles, who is not
    among its finishers. When its turn comes nothing is saved, the channel is told the override is
    not in the submitted results, and one `RESULT_SUBMISSION_REJECTED` line is logged naming
    season 3, Pro, round 3 and the race, with the reason "FL override <@103> is not in the
    submitted results" and the paste (results :261; owner Q-G3a)."""
    league = await _league(tmp_path, monkeypatch)
    await accept_session(league, ROUND_3, session="FEATURE_QUALIFYING")
    paste = f"FL: <@103>\n{RACE_PASTE}"

    await _type(league, paste)
    await run_queue(league.bot)

    assert ("FEATURE_RACE", "ACTIVE") not in await _sessions(league)
    assert "FL override <@103> is not in the submitted results" in _said(league)
    [line] = await _refused_lines(league)
    _names_the_paste(
        line, content=paste, session="FEATURE_RACE",
        reason="FL override <@103> is not in the submitted results",
    )


async def test_the_validation_refusal_s_line_is_unchanged(tmp_path, monkeypatch):
    """The admin pastes a qualifying naming Charles, who races in Am. The one
    `RESULT_SUBMISSION_REJECTED` line logged when its turn comes reads as before: the admin, then
    season 3 and 'Pro', round 3 and the qualifying, then the paste quoted, with no `reason:` line
    (results :257)."""
    league = await _league(tmp_path, monkeypatch)

    await _type(league, OUTSIDER_PASTE)
    await run_queue(league.bot)

    [line] = await _refused_lines(league)
    assert (
        f"{member_named('Admin', ADMIN_ID)} | RESULT_SUBMISSION_REJECTED | \n"
        f"  season: {SEASON_NUMBER}, division: 'Pro'\n"
        "  round: 3, session: FEATURE_QUALIFYING\n"
        f"  input:\n```\n{OUTSIDER_PASTE}\n```"
    ) in line, line
    assert "reason:" not in line, line


async def test_a_cancellation_saved_between_the_settling_and_the_save_saves_no_session(
    tmp_path, monkeypatch,
):
    """The admin pastes round 3's qualifying. Its turn is judged and settled, finding the round
    open, and its save is next; before the save runs, a cancellation's save records round 3
    cancelled. The session's save then saves nothing and answers in the channel that round 3 has
    been cancelled, so its results can no longer be entered, and no prompt for the race is
    posted."""
    league = await _league(tmp_path, monkeypatch)

    await _type(league, QUALI_PASTE)
    for _ in range(50):
        turns = await _turns(league)
        if turns and any(job["name"] == "settle" and job["done_at"]
                         for job in await step_rows(league.db_path, turns[0]["id"])):
            break
        await run_queue(league.bot, steps=1)
    else:
        raise AssertionError("the turn was never settled")
    await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", ROUND_3)

    await run_queue(league.bot)

    assert await _sessions(league) == []
    assert ("⛔ Round 3 has been cancelled, so its results can no longer be entered. Nothing was "
            "saved.") in _said(league)
    assert "📋 Submit" not in _said(league)
    assert await stopped_job(league.db_path) is None


async def test_a_session_refused_at_its_save_for_a_cancellation_is_logged(tmp_path, monkeypatch):
    """As above: round 3's qualifying is judged and settled, then a cancellation's save records
    round 3 cancelled before the session's save runs. The save saves nothing, and one
    `RESULT_SUBMISSION_REJECTED` line is logged naming season 3, Pro, round 3 and the qualifying,
    with the reason that round 3 has been cancelled and the paste (results :261)."""
    league = await _league(tmp_path, monkeypatch)

    await _type(league, QUALI_PASTE)
    for _ in range(50):
        turns = await _turns(league)
        if turns and any(job["name"] == "settle" and job["done_at"]
                         for job in await step_rows(league.db_path, turns[0]["id"])):
            break
        await run_queue(league.bot, steps=1)
    else:
        raise AssertionError("the turn was never settled")
    await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", ROUND_3)

    await run_queue(league.bot)

    assert await _sessions(league) == []
    [line] = await _refused_lines(league)
    _names_the_paste(line, content=QUALI_PASTE, reason="Round 3 has been cancelled")


async def test_a_message_by_someone_other_than_a_league_manager_is_ignored(tmp_path, monkeypatch):
    """Lewis, a driver without the league managers' role, types in the submission channel. The
    message is not marked, no turn is asked and nothing is posted."""
    league = await _league(tmp_path, monkeypatch)

    message = await _type(league, QUALI_PASTE, author=LEWIS)

    assert message.marks == set()
    assert await _turns(league) == []
    await run_queue(league.bot)
    assert await _sessions(league) == []
    assert league.texts(SUBMISSION_CHANNEL) == []


async def test_a_paste_typed_while_the_queue_is_stopped_waits_and_later_ones_are_turned_away(
    tmp_path, monkeypatch,
):
    """Round 3's own lane is stopped at another job. The admin's qualifying paste is marked and
    waits, nothing saved; a second paste is turned away with a note, and the refusal is logged,
    one ⛔ line naming the admin, round 3 and the qualifying still waited on (results :257).
    Once the stopped job is retried and goes through, the qualifying is saved and its mark
    cleared."""
    league = await _league(tmp_path, monkeypatch)
    holder = await block_queue(league, lane=LANE)

    first = await _type(league, QUALI_PASTE)
    await run_queue(league.bot)
    assert MARK in first.marks
    assert await _sessions(league) == []
    second = await _type(league, RACE_PASTE)
    assert second.gone
    logged = "\n".join(await queued_log_lines(league.db_path)) + league.log()
    turned_away = [line for line in logged.splitlines() if "⛔" in line]
    assert len(turned_away) == 1, logged
    line = logged[logged.index(turned_away[0]):]
    assert f"<@{ADMIN_ID}>" in line and "Round 3" in line and QUALIFYING in line, line

    holder["fail"] = False
    await retry_job(league.bot, lane=LANE)

    assert await _sessions(league) == [("FEATURE_QUALIFYING", "ACTIVE")]
    assert MARK not in first.marks


async def test_a_paste_is_taken_while_the_league_is_stopped(tmp_path, monkeypatch):
    """The league lane is stopped at a job of its own. The admin pastes the qualifying: on
    round 3's lane it is saved and the race asked for all the same, and the league stays
    stopped."""
    league = await _league(tmp_path, monkeypatch)
    await block_queue(league, lane="league")

    paste = await _type(league, QUALI_PASTE)
    await run_queue(league.bot)

    assert await _sessions(league) == [("FEATURE_QUALIFYING", "ACTIVE")]
    assert f"📋 Submit **{RACE}** results" in _said(league)
    assert MARK not in paste.marks
    assert await stopped_job(league.db_path, "league") is not None


async def test_the_last_paste_s_mark_stays_until_its_review_prompt_appears(tmp_path, monkeypatch):
    """The qualifying is saved and the league lane is stopped. The admin pastes the race: it is
    saved, but its ⏳ stays, since the review it handed to the league has not yet posted its
    prompt. Once the league's stopped job goes through, the review opens and the mark is
    cleared."""
    league = await _league(tmp_path, monkeypatch)
    await accept_session(league, ROUND_3, session="FEATURE_QUALIFYING")
    holder = await block_queue(league, lane="league")

    paste = await _type(league, RACE_PASTE)
    await run_queue(league.bot)

    assert ("FEATURE_RACE", "ACTIVE") in await _sessions(league)
    assert MARK in paste.marks

    holder["fail"] = False
    await retry_job(league.bot, lane="league")
    assert MARK not in paste.marks


async def test_a_message_typed_while_the_league_holds_the_last_paste_s_review_is_turned_away(
    tmp_path, monkeypatch,
):
    """The qualifying is saved and the league lane is stopped. The admin pastes the race, which
    hands the review to the league, and then types again: the message is deleted at once with
    the note, and the refusal is logged, one ⛔ line naming the admin and round 3. It is not
    marked and left to be ignored when it runs."""
    league = await _league(tmp_path, monkeypatch)
    await accept_session(league, ROUND_3, session="FEATURE_QUALIFYING")
    await block_queue(league, lane="league")
    await _type(league, RACE_PASTE)
    await run_queue(league.bot)

    again = await _type(league, "one more thing")

    assert again.gone
    assert MARK not in again.marks
    assert "still waiting its turn" in _said(league)
    logged = "\n".join(await queued_log_lines(league.db_path)) + league.log()
    line = logged[logged.index("⛔"):]
    assert logged.count("⛔") == 1 and f"<@{ADMIN_ID}>" in line and "Round 3" in line, logged
    assert len([row for row in await _turns(league)]) == 1


async def test_a_message_typed_while_the_league_holds_a_round_of_cancelled_sessions_is_turned_away(
    tmp_path, monkeypatch,
):
    """Every session is entered as cancelled and the league lane is stopped, so the cancelled
    lines are not yet posted. A message typed in the channel meanwhile is turned away with the
    note and the refusal is logged."""
    league = await _league(tmp_path, monkeypatch)
    await accept_session(league, ROUND_3, session="FEATURE_QUALIFYING", status="CANCELLED")
    await block_queue(league, lane="league")
    await _type(league, "CANCELLED")
    await run_queue(league.bot)

    again = await _type(league, "one more thing")

    assert again.gone
    assert "still waiting its turn" in _said(league)
    logged = "\n".join(await queued_log_lines(league.db_path)) + league.log()
    assert logged.count("⛔") == 1 and "Round 3" in logged, logged


async def test_a_paste_s_turn_writes_only_its_round_s_rows(tmp_path, monkeypatch):
    """The admin pastes the qualifying. Its turn runs on round 3's lane, and writes the round's
    session results alone: the rounds, the standings and the season are as they were."""
    league = await _league(tmp_path, monkeypatch)
    reads = (
        "SELECT * FROM rounds ORDER BY id",
        "SELECT * FROM driver_standings_snapshots ORDER BY id",
        "SELECT * FROM seasons ORDER BY id",
    )
    before = [await league.rows(sql) for sql in reads]

    await _type(league, QUALI_PASTE)
    await run_queue(league.bot)

    [turn] = await _turns(league)
    assert turn["lane"] == LANE
    assert await _sessions(league) == [("FEATURE_QUALIFYING", "ACTIVE")]
    assert [await league.rows(sql) for sql in reads] == before


# ---------------------------------------------------------------------------
# C48 — the configuration choice a turn of its own
# ---------------------------------------------------------------------------

CONFIG_KIND = "results.submission.config"
#: A second points configuration attached to the season, so that the session's is chosen.
HEAVY = "Heavy"



async def _two_configs(league: SeasonLeague) -> None:
    """The season has a second points configuration, "Heavy", attached beside "Standard"."""
    await league.write("INSERT INTO points_config_store (config_name) VALUES (?)", HEAVY)
    [season] = await league.rows("SELECT id FROM seasons")
    await league.write(
        "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
        season["id"], HEAVY,
    )


def _picker(league: SeasonLeague) -> Any:
    """The message standing in the submission channel that offers the configurations."""
    [picker] = [message for message in league.channel(SUBMISSION_CHANNEL).messages.values()
                if message.view is not None
                and "Select the points configuration" in str(message.content)]
    return picker


def _choice(league: SeasonLeague, picker: Any, *, by: int = ADMIN_ID) -> Any:
    """A press of a button on *picker* by *by*, the admin unless another is given: its
    response, edits and follow-ups recorded; the acknowledgement, where it is a follow-up, is a
    message whose edits are recorded."""
    press = member_interaction(
        league.bot, user=league._member(by), created_at=league.clock.now,
    )
    press.guild = league.guild
    press.channel_id = SUBMISSION_CHANNEL
    press.message = picker
    answered = {"done": False}

    async def _answer(*_args: Any, **_kwargs: Any) -> None:
        answered["done"] = True

    for name in ("send_message", "defer", "edit_message"):
        setattr(press.response, name, AsyncMock(side_effect=_answer))
    press.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    press.acknowledged = MagicMock()
    press.acknowledged.edit = AsyncMock()
    press.followup.send = AsyncMock(return_value=press.acknowledged)
    return press


def _button(picker: Any, label: str) -> Any:
    """The button labelled *label* on *picker*."""
    [button] = [item for item in picker.view.children if getattr(item, "label", None) == label]
    return button


async def _press(league: SeasonLeague, label: str = HEAVY, *, by: int = ADMIN_ID) -> Any:
    """*by*, the admin unless another is given, presses *label*, "Heavy" unless another is given,
    on the configuration picker; gives the press."""
    picker = _picker(league)
    press = _choice(league, picker, by=by)
    await _button(picker, label).callback(press)
    return press


def _private_texts(press: Any) -> list[str]:
    """Every message the press was answered with privately, in order."""
    calls = [*press.response.send_message.await_args_list, *press.followup.send.await_args_list]
    return [str(call.args[0] if call.args else call.kwargs.get("content", ""))
            for call in calls if call.kwargs.get("ephemeral")]


async def _configs(league: SeasonLeague) -> list[tuple[str, str, str | None]]:
    rows = await league.rows(
        "SELECT session_type, status, config_name FROM session_results WHERE round_id = ? "
        "ORDER BY id", ROUND_3,
    )
    return [(row["session_type"], row["status"], row["config_name"]) for row in rows]


async def test_a_configuration_choice_saves_the_session_and_moves_on(tmp_path, monkeypatch):
    """The season has two points configurations attached. The admin pastes round 3's
    qualifying; its turn offers the configurations in the submission channel and saves nothing
    yet. The admin presses "Heavy": `results.submission.config` is asked on round 3's lane, and
    when it runs the qualifying is saved scored by "Heavy" and the race is asked for."""
    league = await _league(tmp_path, monkeypatch)
    await _two_configs(league)

    await _type(league, QUALI_PASTE)
    await run_queue(league.bot)
    assert await _configs(league) == []
    assert "📋 Submit" not in _said(league)

    await _press(league)
    [choice] = [row for row in await change_rows(league.db_path) if row["kind"] == CONFIG_KIND]
    assert choice["lane"] == LANE
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _configs(league) == [("FEATURE_QUALIFYING", "ACTIVE", HEAVY)]
    assert f"📋 Submit **{RACE}** results" in _said(league)


#: A second league manager, who presses the picker the admin's paste posted.
PRESSER = 78


async def test_a_paste_held_once_its_configuration_is_chosen_is_logged_naming_the_presser(
    tmp_path, monkeypatch,
):
    """The season has two points configurations attached. The admin pastes round 3's
    qualifying and its turn offers the configurations; round 2 of Pro is then opened for
    amendment, and a second league manager (78) presses "Heavy". When the choice runs nothing is
    saved, and one `RESULT_SUBMISSION_REJECTED` line is logged naming the presser, not the admin,
    with season 3, Pro, round 3 and the qualifying, the amendment as the reason, and the paste
    (results :261)."""
    league = await _league(tmp_path, monkeypatch)
    league.roles_held[PRESSER] = {MANAGER_ROLE_ID}
    await _two_configs(league)
    await _type(league, QUALI_PASTE)
    await run_queue(league.bot)
    await league.write(
        "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
        "VALUES (?, 7700, '[\"FEATURE_RACE\"]', ?)",
        round_id(PRO, 2), league.clock.now.isoformat(),
    )

    await _press(league, by=PRESSER)
    await run_queue(league.bot)

    assert await _configs(league) == []
    [line] = await _refused_lines(league)
    assert f"<@{PRESSER}>" in line.split(" | ")[0], line
    assert f"<@{ADMIN_ID}>" not in line, line
    _names_the_paste(line, content=QUALI_PASTE,
                     reason="Round 2 of this division is being amended")


async def test_the_picker_is_edited_by_the_press_s_own_response(tmp_path, monkeypatch):
    """The admin presses "Heavy" on the configuration picker. The picker is changed to
    "🔧 Config selected: **Heavy**", its buttons taken off, by the press's own response (a reply,
    not a job); the bot makes no edit of the picker message of its own."""
    league = await _league(tmp_path, monkeypatch)
    await _two_configs(league)
    await _type(league, QUALI_PASTE)
    await run_queue(league.bot)
    picker = _picker(league)

    press = await _press(league)
    await run_queue(league.bot)

    [edit] = press.response.edit_message.await_args_list
    assert f"Config selected: **{HEAVY}**" in str(edit.kwargs.get("content"))
    assert "view" in edit.kwargs and edit.kwargs["view"] is None
    assert ("edit", SUBMISSION_CHANNEL, picker.id) not in league.events


async def test_a_configuration_choice_is_acknowledged_and_updated_and_kept_while_the_queue_is_stopped(
    tmp_path, monkeypatch,
):
    """Round 3's own lane is stopped at another job when the admin presses "Heavy". The press is
    answered privately that the choice is under way, naming the stopped job; nothing is saved.
    Once the stopped job is retried and goes through, the qualifying is saved scored by "Heavy"
    and the private answer is updated with the outcome."""
    league = await _league(tmp_path, monkeypatch)
    await _two_configs(league)
    await _type(league, QUALI_PASTE)
    await run_queue(league.bot)
    holder = await block_queue(league, lane=LANE)

    press = await _press(league)
    await run_queue(league.bot)

    [acknowledged] = _private_texts(press)
    assert acknowledged.startswith("⏳") and "stopped at job #" in acknowledged
    assert await _configs(league) == []

    holder["fail"] = False
    await retry_job(league.bot, lane=LANE)

    assert await _configs(league) == [("FEATURE_QUALIFYING", "ACTIVE", HEAVY)]
    assert press.acknowledged.edit.await_args_list or press.edit_original_response.await_args_list


#: The season's other points configuration, attached beside "Heavy".
STANDARD = "Standard"


def _presser_refusals(lines: list[str]) -> list[str]:
    """The refusal lines among *lines* naming the second league manager, whose mention the log
    channel writes in code."""
    return [line for line in lines if line.startswith("⛔") and f"`<@{PRESSER}>`" in line]


@pytest.mark.xfail(
    strict=True,
    reason="a second press edits the picker to name its own choice before the queue refuses it",
)
@pytest.mark.parametrize("ran_first", [False, True], ids=["waiting", "ran"])
async def test_a_second_press_of_the_picker_is_refused_privately_and_the_first_choice_stands(
    tmp_path, monkeypatch, ran_first,
):
    """The season has two points configurations attached. The admin pastes round 3's qualifying
    and its turn offers the configurations; the admin presses "Heavy". Then — while the choice
    waits its turn, or once it has run — a second league manager (78) presses "Standard" on the
    same picker. His press does not touch the message: he is told, seen by him alone, that the
    configuration has already been chosen, naming Heavy, and one refusal line names him. One
    choice is asked, and the qualifying is saved scored by Heavy."""
    league = await _league(tmp_path, monkeypatch)
    league.roles_held[PRESSER] = {MANAGER_ROLE_ID}
    await _two_configs(league)
    await _type(league, QUALI_PASTE)
    await run_queue(league.bot)

    await _press(league, HEAVY)
    if ran_first:
        await run_queue(league.bot)
    second = await _press(league, STANDARD, by=PRESSER)
    await run_queue(league.bot)

    second.response.edit_message.assert_not_awaited()
    [told] = _private_texts(second)
    assert told.startswith("⚠️"), told
    assert "already been chosen" in told and HEAVY in told, told
    assert len([row for row in await change_rows(league.db_path)
                if row["kind"] == CONFIG_KIND]) == 1
    assert await _configs(league) == [("FEATURE_QUALIFYING", "ACTIVE", HEAVY)]
    lines = [*await queued_log_lines(league.db_path), *league.bot.log_channel.sent]
    assert len(_presser_refusals(lines)) == 1, lines


@pytest.mark.xfail(
    strict=True,
    reason="a press made while the first press's edit is in flight edits the picker and is asked",
)
async def test_a_press_made_while_the_first_press_s_edit_is_in_flight_is_refused(
    tmp_path, monkeypatch,
):
    """The season has two points configurations attached, and the admin's qualifying paste has
    posted the picker. The admin presses "Heavy", and while Discord has yet to answer that press's
    edit of the message, a second league manager (78) presses "Standard". His press does not
    touch the message and is refused, seen by him alone; one choice is asked, and the qualifying
    is saved scored by Heavy."""
    league = await _league(tmp_path, monkeypatch)
    league.roles_held[PRESSER] = {MANAGER_ROLE_ID}
    await _two_configs(league)
    await _type(league, QUALI_PASTE)
    await run_queue(league.bot)
    picker = _picker(league)
    first = _choice(league, picker)
    begun, answered = asyncio.Event(), asyncio.Event()

    async def _edit_in_flight(*_args: Any, **_kwargs: Any) -> None:
        begun.set()
        await answered.wait()

    first.response.edit_message = AsyncMock(side_effect=_edit_in_flight)
    pressing = asyncio.create_task(_button(picker, HEAVY).callback(first))
    await asyncio.wait_for(begun.wait(), timeout=5)

    second = await _press(league, STANDARD, by=PRESSER)
    answered.set()
    await pressing
    await run_queue(league.bot)

    second.response.edit_message.assert_not_awaited()
    [told] = _private_texts(second)
    assert told.startswith("⚠️") and "already been chosen" in told, told
    assert len([row for row in await change_rows(league.db_path)
                if row["kind"] == CONFIG_KIND]) == 1
    assert await _configs(league) == [("FEATURE_QUALIFYING", "ACTIVE", HEAVY)]
