"""The results submission wizard: from a round's start time to penalty review.

Issue #208. `run_result_submission_job` was three-quarters untested. Every raced round of every
season passes through it. This file drives the whole flow — the channel, the paste for each
session, the points configuration and the handover — with the real validator and the real
session write, so a paste that would be refused in production is refused here, and one that
would be saved is saved.

**The round's start is what moves it off NOT_RUN, and where results are off it ends there.**
The round's start asks the change queue to open its submission (#439, slice 6b), and the queue's
jobs do so whatever the modules. With results on, the round begins waiting for results; with
results off nobody will ever enter one, so the round is made FINAL there and its division
reconsidered — otherwise it would stay outstanding for ever and the season could never be
completed (#154).

**Each paste is a turn on the round's lane** (#439, slice 6b, C47). A league manager's message
in the submission channel is marked at once and `results.submission.turn` asked for it; when its
turn comes it is judged against the session the round then expects. These cases type each paste
through the results cog and run the queue after it, as `test_submission_turn_change.py` does.

**Sessions are asked for in racing order, one at a time, and each must be accepted before the
next is asked.** A rejected paste is answered with every error and the same session is asked
again: the manager fixes the paste rather than losing the session. The rejected input is logged
with the reason, and so is the accepted one — the log is the only record of what was actually
typed.

**A session can be cancelled.** `CANCELLED` records the session as cancelled and moves on; its
line comes with the round's Provisional Results, not when it is typed (decision 17). If every
session is cancelled there is nothing for stewards to review, so the channel is closed instead
of opening a penalty review.

**A fastest-lap override must name somebody in the paste.** It decides who gets the bonus when
two laps tie, and naming a driver who did not race would award it to them.

**The points configuration is picked without asking where it can be.** One attached
configuration is used and the manager told; several are offered to choose from; none is saved
without one and warned about, rather than refusing a round whose drivers have just raced.

**Anything missing before the channel exists opens nothing.** A cancelled round, or one that
does not exist, is no longer due and its opening is dropped. A division without a results
channel, or one whose results channel has since been deleted, stops the queue naming
`/results channel results`, as a bot's change lacking its channel does (#439, slice 6b).

**The last paste asks the change queue to open the penalty review** (#439, slice 2):
`results.review.open` is asked rather than the review opened in place, and, since a paste
answers no interaction, the submission channel is told that the review is being opened, with its
job number and, where the league's queue is stopped, the job it is stopped at (results :382).
"""
from __future__ import annotations

import itertools
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.models.round import RoundFormat
from leaguebot.results.services import result_submission_service
from leaguebot.results.services.result_submission_service import (
    DivisionValidationData,
    get_sessions_for_format,
)
from leaguebot.results.utils import results_formatter
from tests.support.change_queue import (
    ADMIN_ROLE_ID,
    MANAGER_ROLE_ID,
    change_rows,
    http_error,
    member_interaction,
    queued_log_lines,
    run_queue,
    step_rows,
    stopped_job,
    typed_message,
)
from tests.support.review_league import block_queue, channel
from tests.support.season_league import (
    ADMIN_ID,
    AM,
    CONFIG,
    DIVISIONS,
    PRO,
    PRO_ROLE,
    SEASON_ID,
    SUBMISSION_CHANNEL,
    SeasonLeague,
    ongoing_league,
    open_submission,
    round_id,
)

REVIEW_OPEN = "results.review.open"


# ---------------------------------------------------------------------------
# The round's start, on the change queue (#439, slice 6b, C45)
# ---------------------------------------------------------------------------
#
# The round's start asks the queue for `results.submission.open`, as the bot, and the queue
# opens the submission: the round moved off NOT_RUN, the channel created in the interaction
# channel's category and recorded, the opening and the first prompt posted. These cases run on
# `ongoing_league`'s league (Pro's round 3 NOT_RUN, its results channel 606, the league
# managers' role 900 and the admins' 901), its server creating each text channel asked of it.

ROUND_3 = round_id(PRO, 3)
RESULTS_606 = DIVISIONS[PRO][3].results

_created_ids = itertools.count(7700)


async def _opening_league(tmp_path: Any, **options: Any) -> SeasonLeague:
    """`ongoing_league`'s league, its server creating text channels: the ids of the channels
    created are kept in `league.created`; while `league.create_fails` is set, creating one
    raises it."""
    league = await ongoing_league(tmp_path, **options)
    league.created = []
    league.create_fails = None

    async def _create(name: str, **kwargs: Any) -> Any:
        if league.create_fails is not None:
            raise league.create_fails
        made = league._recording(channel(next(_created_ids), league.events))
        made.name = name
        made.category = kwargs.get("category")
        made.guild = league.guild
        league.channels[made.id] = made
        league.created.append(made.id)
        return made

    league.guild.create_text_channel = AsyncMock(side_effect=_create)
    return league


async def _open(league: SeasonLeague, round_: int = ROUND_3) -> None:
    """Round *round_*'s start asks the queue to open its submission, as its timer does, and the
    queue is run."""
    from leaguebot.core.models.change import ChangeOrigin

    await league.bot.change_queue.ask(
        "results.submission.open", {"round_id": round_}, what="opening a results submission",
        origin=ChangeOrigin.BOT,
    )
    await run_queue(league.bot)


async def _status_of(league: SeasonLeague, round_: int = ROUND_3) -> str:
    [row] = await league.rows("SELECT status FROM rounds WHERE id = ?", round_)
    return row["status"]


async def _submissions(league: SeasonLeague) -> list[dict[str, Any]]:
    return await league.rows("SELECT round_id, channel_id FROM round_submission_channels")


def _opening(league: SeasonLeague) -> str:
    [made] = league.created
    return league.texts(made)[0]


async def test_the_round_starts_waiting_for_results(tmp_path):
    """Round 3 starts with results on: once the queue opens its submission, round 3 awaits
    results."""
    league = await _opening_league(tmp_path)

    await _open(league)

    assert await _status_of(league) == "AWAITING_RESULTS"


async def test_with_results_off_the_round_ends_at_its_start(tmp_path):
    """#154: nobody will ever enter a result, so the round would otherwise stay outstanding
    for ever and the season could never be completed. Pro's rounds 1–3 are final and round 4,
    its last, starts with results off: the queue makes round 4 FINAL, creates no channel, and
    Pro is finished."""
    league = await _opening_league(tmp_path, results=False)
    await league.write(
        "UPDATE rounds SET status = 'FINAL' WHERE division_id = ? AND round_number < 4", PRO,
    )

    await _open(league, round_id(PRO, 4))

    assert await _status_of(league, round_id(PRO, 4)) == "FINAL"
    [division] = await league.rows("SELECT status FROM divisions WHERE id = ?", PRO)
    assert division["status"] == "FINISHED"
    assert league.created == []


async def test_a_round_already_moved_on_is_not_moved_back(tmp_path):
    """The move is guarded to NOT_RUN, so an opening asked late for round 2, already awaiting its
    report verdicts, cannot reset it."""
    league = await _opening_league(tmp_path)

    await _open(league, round_id(PRO, 2))

    assert await _status_of(league, round_id(PRO, 2)) == "AWAITING_REPORT_VERDICTS"


async def test_a_cancelled_round_opens_nothing(tmp_path):
    """Round 3 is cancelled when its start asks for the submission: the opening is dropped as no
    longer due, the queue does not stop, no channel is created, and round 3 stays CANCELLED."""
    league = await _opening_league(tmp_path)
    await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", ROUND_3)

    await _open(league)

    assert await stopped_job(league.db_path) is None
    assert league.created == []
    assert await _status_of(league) == "CANCELLED"


async def test_a_round_that_does_not_exist_opens_nothing(tmp_path):
    """An opening asked for a round id the league has none of (9999) is dropped: the queue does
    not stop and no channel is created."""
    league = await _opening_league(tmp_path)

    await _open(league, 9999)

    assert await stopped_job(league.db_path) is None
    assert league.created == []
    assert await _submissions(league) == []


# ---------------------------------------------------------------------------
# What stops it before a channel exists
# ---------------------------------------------------------------------------


async def test_a_guild_the_bot_is_not_in_opens_nothing(tmp_path):
    """The bot is no longer in the league's server when round 3 starts: no channel is created
    and no submission recorded."""
    league = await _opening_league(tmp_path)
    league.bot.get_guild = MagicMock(return_value=None)

    await _open(league)

    assert league.created == []
    assert await _submissions(league) == []


async def test_a_division_without_a_results_channel_opens_nothing(tmp_path):
    """Pro has no results channel set when round 3 starts: the queue stops, its notice naming
    `/results channel results`, and no channel is created."""
    league = await _opening_league(tmp_path)
    await league.write(
        "UPDATE division_results_config SET results_channel_id = NULL WHERE division_id = ?", PRO,
    )

    await _open(league)

    assert await stopped_job(league.db_path) is not None
    assert "/results channel results" in league.log()
    assert league.created == []


async def test_a_results_channel_since_deleted_opens_nothing(tmp_path):
    """Pro's results channel (606) is set but has been deleted from the server when round 3
    starts: the queue stops, its notice naming `/results channel results`, and no channel is
    created."""
    league = await _opening_league(tmp_path)
    league.remove_channel(RESULTS_606)

    await _open(league)

    assert await stopped_job(league.db_path) is not None
    assert "/results channel results" in league.log()
    assert league.created == []


async def test_a_channel_discord_refuses_to_create_stops_the_job(tmp_path):
    """Discord refuses to create round 3's channel (Missing Permissions): the queue stops at the
    channel's creation, no submission is recorded, and no penalty review is asked."""
    league = await _opening_league(tmp_path)
    league.create_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")

    await _open(league)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "create_channel"
    assert await _submissions(league) == []
    assert [row for row in await change_rows(league.db_path) if row["kind"] == REVIEW_OPEN] == []


async def test_the_channel_is_opened_to_both_league_roles(tmp_path):
    """The channel round 3's opening creates admits the league managers' role (900) and the
    league admins' role (901)."""
    league = await _opening_league(tmp_path)

    await _open(league)

    overwrites = league.guild.create_text_channel.await_args.kwargs["overwrites"]
    admitted = {getattr(target, "id", None) for target in overwrites}
    assert {MANAGER_ROLE_ID, ADMIN_ROLE_ID} <= admitted


# ---------------------------------------------------------------------------
# The opening message
# ---------------------------------------------------------------------------


async def test_the_opening_message_names_the_round_and_its_sessions(tmp_path):
    """The opening the queue posts in round 3's new channel names Round 3, Pro, and its
    sessions, Qualifying and Race."""
    league = await _opening_league(tmp_path)

    await _open(league)

    opening = _opening(league)
    assert "Round 3" in opening
    assert "Pro" in opening
    assert "Qualifying" in opening and "Race" in opening


@pytest.mark.parametrize(
    ("fmt", "label"),
    [("NORMAL", "Normal"), ("SPRINT", "Sprint"), ("MYSTERY", "Mystery"), ("ENDURANCE", "Endurance")],
)
async def test_the_opening_message_names_the_format_as_a_league_reads_it(tmp_path, fmt, label):
    """Issue #360. The format was interpolated as the enum member, which Python 3.12 prints as
    ``RoundFormat.NORMAL`` — the code name, not a word a league manager uses. Round 3 is of each
    format in turn; its opening names the format as a league reads it."""
    league = await _opening_league(tmp_path)
    await league.write("UPDATE rounds SET format = ? WHERE id = ?", fmt, ROUND_3)

    await _open(league)

    opening = _opening(league)
    assert f"(Pro) - {label}. Sessions:" in opening
    assert "RoundFormat" not in opening
    assert fmt not in opening


async def test_the_opening_message_pings_the_interaction_role_and_not_the_division(tmp_path):
    """Issue #136. The channel is closed to the division's role, so a ping for it reached
    nobody and the league managers who enter the results were never told. Round 3's opening
    mentions the league managers' role (900), not Pro's role (801) nor the admins' (901)."""
    league = await _opening_league(tmp_path)

    await _open(league)

    opening = _opening(league)
    assert f"<@&{MANAGER_ROLE_ID}>" in opening
    assert f"<@&{PRO_ROLE}>" not in opening
    assert f"<@&{ADMIN_ROLE_ID}>" not in opening


async def test_the_opening_message_pings_nobody_when_the_interaction_role_is_gone(tmp_path):
    """The league managers' role (900) has been deleted from the server: round 3's opening
    mentions no role at all."""
    league = await _opening_league(tmp_path)
    league.roles_gone.add(MANAGER_ROLE_ID)

    await _open(league)

    assert "<@&" not in _opening(league)


# ---------------------------------------------------------------------------
# Collecting the sessions, each paste a turn on the round's lane (#439, slice 6b, C47)
# ---------------------------------------------------------------------------
#
# These cases run on `ongoing_league`'s league, results on: Pro's round 3 awaits results, its
# submission open in `SUBMISSION_CHANNEL`; the admin (77) holds the league managers' role. The
# division's seats, as the paste's validation reads them, are Lewis (101) and Max (102) at
# Ferrari, shorthand FER. The season scores on "Standard", which gives the race 25 and 18 points
# and the qualifying's pole 3. Each paste is typed through the results cog and the queue run
# after it.


QUALIFYING, RACE = (
    results_formatter.format_session_label(session, is_sprint=False)
    for session in get_sessions_for_format(RoundFormat.NORMAL)[:2]
)
QUALI_PASTE = "1, <@101>, FER, Soft, 1:19.000, N/A\n2, <@102>, FER, Soft, 1:19.500, +0.500"
RACE_PASTE = "1, <@101>, FER, 1:30:00.000, 1:20.000, N/A\n2, <@102>, FER, +5.000, 1:21.000, N/A"
HEAVY = "Heavy"
#: The channel of an amendment standing open while round 3's results are entered.
AMEND_CHANNEL = 8200

_SEATED = DivisionValidationData(
    division_driver_ids={101, 102}, team_of_role={811: 31}, reserve_team_role_id=None,
    driver_team_map={101: 31, 102: 31}, reserve_driver_ids=set(), team_names={31: "Ferrari"},
    team_of_shorthand={"fer": 31},
)


async def _paste_league(tmp_path: Any, monkeypatch: Any, *, fmt: str = "NORMAL") -> SeasonLeague:
    """The league of the section's comment, round 3 of format *fmt*."""
    league = await ongoing_league(tmp_path)
    league.roles_held[ADMIN_ID] = {MANAGER_ROLE_ID}
    await league.write(
        "UPDATE rounds SET status = 'AWAITING_RESULTS', format = ? WHERE id = ?", fmt, ROUND_3
    )
    for session, position, points in (("FEATURE_RACE", 1, 25), ("FEATURE_RACE", 2, 18),
                                      ("FEATURE_QUALIFYING", 1, 3)):
        await league.write(
            "INSERT INTO season_points_entries (season_id, config_name, session_type, position, "
            "points) VALUES (?, ?, ?, ?, ?)", SEASON_ID, CONFIG, session, position, points,
        )
    await open_submission(league, ROUND_3)
    sub = league.channel(SUBMISSION_CHANNEL)
    if not isinstance(getattr(sub, "posted", None), list):
        sub.posted = []
    monkeypatch.setattr(
        result_submission_service, "_build_division_validation_data",
        AsyncMock(return_value=_SEATED),
    )
    return league


async def _paste(league: SeasonLeague, *contents: str) -> None:
    """The admin types each of *contents* in round 3's submission channel in turn, the results
    cog hearing it and the queue run after each."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    for content in contents:
        message = typed_message(
            league.channel(SUBMISSION_CHANNEL), league._member(ADMIN_ID), content,
            created_at=league.clock.now,
        )
        await ResultsCog(league.bot).on_message(message)
        await run_queue(league.bot)


async def _entered(league: SeasonLeague) -> list[tuple[Any, ...]]:
    """Round 3's sessions entered: type, status, configuration and who submitted it."""
    rows = await league.rows(
        "SELECT session_type, status, config_name, submitted_by FROM session_results "
        "WHERE round_id = ? ORDER BY id", ROUND_3,
    )
    return [(r["session_type"], r["status"], r["config_name"], r["submitted_by"]) for r in rows]


def _sub_said(league: SeasonLeague) -> str:
    return "\n".join(league.texts(SUBMISSION_CHANNEL))


async def _logs(league: SeasonLeague) -> str:
    """Every line written to the log channel, queued or posted."""
    return "\n".join(await queued_log_lines(league.db_path)) + "\n" + league.log()


async def _review_asks(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == REVIEW_OPEN]


async def _paste_turns(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path)
            if row["kind"] == "results.submission.turn"]


async def _two_configs(league: SeasonLeague) -> None:
    """The season has a second points configuration, "Heavy", attached beside "Standard"."""
    await league.write("INSERT INTO points_config_store (config_name) VALUES (?)", HEAVY)
    await league.write(
        "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)", SEASON_ID, HEAVY,
    )


async def _choose(league: SeasonLeague, name: str) -> None:
    """The admin presses *name* on the newest configuration picker in the submission channel,
    and the queue is run."""
    pickers = [message for message in league.channel(SUBMISSION_CHANNEL).messages.values()
               if message.view is not None
               and "Select the points configuration" in str(message.content)]
    picker = max(pickers, key=lambda message: message.id)
    [button] = [item for item in picker.view.children if getattr(item, "label", None) == name]
    press = member_interaction(league.bot, user=league._member(ADMIN_ID),
                               created_at=league.clock.now)
    press.guild = league.guild
    press.channel_id = SUBMISSION_CHANNEL
    press.message = picker
    answered = {"done": False}

    async def _answer(*_args: Any, **_kwargs: Any) -> None:
        answered["done"] = True

    for method in ("send_message", "defer", "edit_message"):
        setattr(press.response, method, AsyncMock(side_effect=_answer))
    press.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    press.followup.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await button.callback(press)
    await run_queue(league.bot)


async def _open_amendment(league: SeasonLeague, division: int = PRO) -> None:
    """Round 1 of *division* stands open for amendment, in `AMEND_CHANNEL`."""
    await league.write(
        "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
        "VALUES (?, ?, '[\"FEATURE_RACE\"]', ?)",
        round_id(division, 1), AMEND_CHANNEL, league.clock.now.isoformat(),
    )


async def _end_amendment(league: SeasonLeague) -> None:
    await league.write("DELETE FROM round_amend_channels")


async def test_a_normal_round_is_saved_session_by_session(tmp_path, monkeypatch):
    """The admin pastes round 3's qualifying, then its race. Each is saved when its turn comes,
    scored by "Standard" and submitted by the admin."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, QUALI_PASTE, RACE_PASTE)

    assert await _entered(league) == [
        ("FEATURE_QUALIFYING", "ACTIVE", CONFIG, ADMIN_ID),
        ("FEATURE_RACE", "ACTIVE", CONFIG, ADMIN_ID),
    ]


async def test_the_drivers_rows_are_written(tmp_path, monkeypatch):
    """Once both pastes' turns have run, the race holds Lewis first and Max second, and the
    qualifying holds both drivers."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, QUALI_PASTE, RACE_PASTE)

    race = await league.rows(
        "SELECT r.driver_user_id, r.finishing_position FROM race_session_results r "
        "JOIN session_results s ON s.id = r.session_result_id WHERE s.round_id = ? "
        "ORDER BY r.finishing_position", ROUND_3,
    )
    assert [(r["driver_user_id"], r["finishing_position"]) for r in race] == [(101, 1), (102, 2)]
    qualifying = await league.rows(
        "SELECT q.id FROM qualifying_session_results q "
        "JOIN session_results s ON s.id = q.session_result_id WHERE s.round_id = ?", ROUND_3,
    )
    assert len(qualifying) == 2


async def test_a_sprint_round_asks_for_all_four_sessions_in_order(tmp_path, monkeypatch):
    """Round 3 is a sprint round. The admin pastes four sessions in turn: they are saved as the
    sprint qualifying, the sprint race, the feature qualifying and the feature race, and after
    each of the first three the channel asks for the next."""
    league = await _paste_league(tmp_path, monkeypatch, fmt="SPRINT")

    await _paste(league, QUALI_PASTE, RACE_PASTE, QUALI_PASTE, RACE_PASTE)

    assert [s[0] for s in await _entered(league)] == [
        "SPRINT_QUALIFYING", "SPRINT_RACE", "FEATURE_QUALIFYING", "FEATURE_RACE",
    ]
    prompts = [text for text in league.texts(SUBMISSION_CHANNEL) if "Submit **" in text]
    assert len(prompts) == 3


async def test_points_are_applied_for_each_saved_session(tmp_path, monkeypatch):
    """Both sessions are scored as they are saved: in the race Lewis has 25 and Max 18, and in
    the qualifying Lewis's pole has 3."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, QUALI_PASTE, RACE_PASTE)

    race = await league.rows(
        "SELECT r.driver_user_id, r.points_awarded FROM race_session_results r "
        "JOIN session_results s ON s.id = r.session_result_id WHERE s.round_id = ? "
        "ORDER BY r.finishing_position", ROUND_3,
    )
    assert [(r["driver_user_id"], r["points_awarded"]) for r in race] == [(101, 25), (102, 18)]
    pole = await league.rows(
        "SELECT q.points_awarded FROM qualifying_session_results q "
        "JOIN session_results s ON s.id = q.session_result_id "
        "WHERE s.round_id = ? AND q.driver_user_id = 101", ROUND_3,
    )
    assert [r["points_awarded"] for r in pole] == [3]


async def test_the_round_is_handed_to_penalty_review(tmp_path, monkeypatch):
    """The last paste asks for round 3's review to open, naming round 3 and Pro, its first
    results published under "Provisional Results"; the channel stays open for the review."""
    import json

    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, QUALI_PASTE, RACE_PASTE)

    [review] = await _review_asks(league)
    payload = json.loads(review["payload"])
    assert payload["round_id"] == ROUND_3
    assert (payload["round_number"], payload["division_name"]) == (3, "Pro")
    assert payload["label"] == "Provisional Results"
    assert payload["publish"] is True
    assert not payload.get("returning")
    assert ("delete_channel", SUBMISSION_CHANNEL) not in [event[:2] for event in league.events]


@pytest.mark.parametrize(
    "stopped",
    [pytest.param(False, id="queue_clear"), pytest.param(True, id="queue_stopped")],
)
async def test_the_last_paste_says_in_the_channel_that_the_review_is_being_opened(
    tmp_path, monkeypatch, stopped,
):
    """A paste answers no interaction, so the channel is told: the penalty review is being
    opened, with its job number, and, where the league's queue is stopped, the job it is
    stopped at."""
    league = await _paste_league(tmp_path, monkeypatch)
    await _paste(league, QUALI_PASTE)
    blocker = None
    if stopped:
        await block_queue(league, lane="league")
        blocker = await stopped_job(league.db_path, "league")

    await _paste(league, RACE_PASTE)

    [review] = await _review_asks(league)
    first_job = min(step["id"] for step in await step_rows(league.db_path, review["id"]))
    lines = [line for line in _sub_said(league).splitlines()
             if "penalty review" in line.lower() and "being opened" in line.lower()]
    assert len(lines) == 1
    assert f"job #{first_job}" in lines[0]
    if blocker is not None:
        assert f"job #{blocker['id']}" in lines[0]
        assert "stopped" in lines[0]
    else:
        assert "stopped" not in lines[0]


async def test_the_accepted_input_is_logged_verbatim(tmp_path, monkeypatch):
    """The log is the only record of what was actually typed: each accepted paste is logged as
    accepted, the race's text with it."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, QUALI_PASTE, RACE_PASTE)

    logged = await _logs(league)
    assert logged.count("RESULT_SUBMISSION_ACCEPTED") == 2
    assert "1:30:00.000" in logged


# ---------------------------------------------------------------------------
# A paste refused
# ---------------------------------------------------------------------------


async def test_a_rejected_paste_is_answered_and_the_session_asked_again(tmp_path, monkeypatch):
    """The admin pastes a qualifying whose driver is not a mention, then a good qualifying and
    the race. The first is answered with every error and the qualifying asked again; the
    manager fixes the paste rather than losing the session, and both sessions are saved."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, "1, not-a-mention, FER, Soft, 1:19.000, N/A", QUALI_PASTE, RACE_PASTE)

    said = _sub_said(league)
    assert "Validation failed" in said
    assert "Driver must be a Discord member mention" in said
    assert len(await _paste_turns(league)) == 3
    assert [s[0] for s in await _entered(league)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]


async def test_a_rejected_paste_is_logged_with_its_input(tmp_path, monkeypatch):
    """The admin types "garbage". Its turn refuses it and logs it as rejected, with what was
    typed (results spec :257)."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, "garbage")

    logged = await _logs(league)
    assert "RESULT_SUBMISSION_REJECTED" in logged
    assert "garbage" in logged


async def test_a_driver_outside_the_division_is_refused(tmp_path, monkeypatch):
    """The admin pastes a qualifying naming <@999>, who does not race in Pro, then a good
    qualifying and the race. The first is refused, and the next two saved."""
    league = await _paste_league(tmp_path, monkeypatch)
    outsider = "1, <@999>, FER, Soft, 1:19.000, N/A\n2, <@102>, FER, Soft, 1:19.500, +0.500"

    await _paste(league, outsider, QUALI_PASTE, RACE_PASTE)

    assert "Validation failed" in _sub_said(league)
    assert [s[0] for s in await _entered(league)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]


async def test_a_fastest_lap_override_naming_a_non_finisher_is_refused(tmp_path, monkeypatch):
    """It would award the bonus to somebody who did not race. The admin pastes the qualifying,
    then a race whose fastest-lap override names <@999>, then the race again without it. The
    override is refused, saying <@999> is not in the submitted results, and the race is saved
    from the third paste."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, QUALI_PASTE, "FL: <@999>\n" + RACE_PASTE, RACE_PASTE)

    assert "FL override <@999> is not in the submitted results" in _sub_said(league)
    assert [s[0] for s in await _entered(league)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]


async def test_a_valid_fastest_lap_override_is_saved(tmp_path, monkeypatch):
    """The admin pastes the race with a fastest-lap override naming Max: the race is saved with
    Max as its override."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, QUALI_PASTE, "FL: <@102>\n" + RACE_PASTE)

    rows = await league.rows(
        "SELECT fl_driver_override FROM session_results WHERE round_id = ? "
        "AND session_type = 'FEATURE_RACE'", ROUND_3,
    )
    assert [r["fl_driver_override"] for r in rows] == [102]


# ---------------------------------------------------------------------------
# Cancelled sessions
# ---------------------------------------------------------------------------


async def test_a_session_can_be_cancelled(tmp_path, monkeypatch):
    """The admin types "cancelled" for the qualifying, then pastes the race. The qualifying is
    entered as cancelled, the channel says it is marked as CANCELLED, and the review is asked
    once."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, "cancelled", RACE_PASTE)

    assert (await _entered(league))[0][:2] == ("FEATURE_QUALIFYING", "CANCELLED")
    assert "marked as CANCELLED" in _sub_said(league)
    assert len(await _review_asks(league)) == 1




async def test_no_results_channel_line_is_posted_when_cancelled_is_typed(tmp_path, monkeypatch):
    """The manager types CANCELLED for the qualifying. When its turn comes the submission
    channel confirms the qualifying is marked as cancelled; nothing about it is posted in the
    results channel then — its line comes with the round's Provisional Results."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, "CANCELLED")

    assert "marked as CANCELLED" in _sub_said(league)
    assert "was cancelled" not in "\n".join(league.texts(RESULTS_606))


async def test_every_session_cancelled_closes_the_channel_instead_of_reviewing(
    tmp_path, monkeypatch,
):
    """There is nothing for stewards to review. The admin types CANCELLED for both sessions:
    no review is asked, the channel says no penalty review is required, round 3's
    submission is closed, and round 3 ends cancelled, marked as not held."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, "CANCELLED", "CANCELLED")

    assert await _review_asks(league) == []
    assert "⏭️" in _sub_said(league)
    [row] = await league.rows(
        "SELECT closed FROM round_submission_channels WHERE round_id = ?", ROUND_3
    )
    assert row["closed"] == 1
    [ended] = await league.rows("SELECT status, not_held FROM rounds WHERE id = ?", ROUND_3)
    assert (ended["status"], ended["not_held"]) == ("CANCELLED", 1)


# ---------------------------------------------------------------------------
# Choosing the points configuration
# ---------------------------------------------------------------------------


async def test_a_single_configuration_is_chosen_without_asking(tmp_path, monkeypatch):
    """Only "Standard" is attached: the qualifying's turn uses it and says so."""
    league = await _paste_league(tmp_path, monkeypatch)

    await _paste(league, QUALI_PASTE, RACE_PASTE)

    assert f"Auto-selected config **{CONFIG}**" in _sub_said(league)


async def test_several_configurations_are_offered_to_choose_from(tmp_path, monkeypatch):
    """"Standard" and "Heavy" are attached. Each paste's turn offers them; the admin presses
    "Heavy" each time, and both sessions are saved scored by it."""
    league = await _paste_league(tmp_path, monkeypatch)
    await _two_configs(league)

    await _paste(league, QUALI_PASTE)
    assert "Select the points configuration" in _sub_said(league)
    await _choose(league, HEAVY)
    await _paste(league, RACE_PASTE)
    await _choose(league, HEAVY)

    assert {s[2] for s in await _entered(league)} == {HEAVY}
    assert len(await _entered(league)) == 2


async def test_no_configuration_saves_without_one_and_warns(tmp_path, monkeypatch):
    """Refusing a round whose drivers have just raced would be the worse outcome. No
    configuration is attached to the season: both sessions are saved without one, the channel
    warns of it, and the race carries no points."""
    league = await _paste_league(tmp_path, monkeypatch)
    await league.write("DELETE FROM season_points_links")

    await _paste(league, QUALI_PASTE, RACE_PASTE)

    assert "No points configuration attached" in _sub_said(league)
    assert {s[2] for s in await _entered(league)} == {None}
    race = await league.rows(
        "SELECT r.points_awarded FROM race_session_results r "
        "JOIN session_results s ON s.id = r.session_result_id WHERE s.round_id = ?", ROUND_3,
    )
    assert {r["points_awarded"] for r in race} == {0}


# ---------------------------------------------------------------------------
# Nothing is entered while another round of the division is being amended (#345)
# ---------------------------------------------------------------------------
#
# Decided 2026-09-21. An amendment's first stage writes its corrections and recalculates the
# championship, publishing nothing; a first pass posts standings from the same database, so it
# would publish them. While one is open, a paste — `CANCELLED` included — is refused when its
# turn runs and the session asked again, the channel staying open.


async def test_a_paste_is_refused_while_another_round_is_amended(tmp_path, monkeypatch):
    """Round 1 of Pro stands open for amendment. The admin pastes the qualifying: its turn
    saves nothing and says round 1 is being amended, to paste the qualifying again then. Once
    the amendment has ended, the qualifying and race are pasted and saved, the review asked
    once, and only the two accepted pastes logged as accepted."""
    league = await _paste_league(tmp_path, monkeypatch)
    await _open_amendment(league)

    await _paste(league, QUALI_PASTE)

    assert await _entered(league) == []
    said = _sub_said(league)
    assert f"Round 1 of this division is being amended in <#{AMEND_CHANNEL}>" in said
    assert "Paste **" in said and "again then." in said

    await _end_amendment(league)
    await _paste(league, QUALI_PASTE, RACE_PASTE)

    assert [s[0] for s in await _entered(league)] == ["FEATURE_QUALIFYING", "FEATURE_RACE"]
    assert len(await _review_asks(league)) == 1
    assert (await _logs(league)).count("RESULT_SUBMISSION_ACCEPTED") == 2


async def test_cancelling_a_session_is_refused_while_another_round_is_amended(
    tmp_path, monkeypatch,
):
    """`CANCELLED` is entered too, so it is held as well. Round 1 of Pro stands open for
    amendment; the admin types CANCELLED: nothing is entered and the channel says to type
    CANCELLED for the qualifying again then. Once the amendment has ended, CANCELLED is entered."""
    league = await _paste_league(tmp_path, monkeypatch)
    await _open_amendment(league)

    await _paste(league, "CANCELLED")

    assert await _entered(league) == []
    assert "Type `CANCELLED` for **" in _sub_said(league)

    await _end_amendment(league)
    await _paste(league, "CANCELLED")

    assert (await _entered(league))[0][:2] == ("FEATURE_QUALIFYING", "CANCELLED")


async def test_an_amendment_opened_while_the_configuration_is_chosen_still_holds(
    tmp_path, monkeypatch,
):
    """The hold is read when the session is written, not when the paste arrives. "Standard"
    and "Heavy" are attached; the admin pastes the qualifying and is offered the choice. Round 1
    of Pro is then opened for amendment, and the admin presses "Heavy": the choice's turn saves
    nothing and says round 1 is being amended."""
    league = await _paste_league(tmp_path, monkeypatch)
    await _two_configs(league)
    await _paste(league, QUALI_PASTE)

    await _open_amendment(league)
    await _choose(league, HEAVY)

    assert "is being amended" in _sub_said(league)
    assert await _entered(league) == []


async def test_an_amendment_in_another_division_holds_nothing(tmp_path, monkeypatch):
    """Round 1 of Am stands open for amendment. The admin pastes Pro's qualifying and race:
    both are saved, and nothing is said of an amendment."""
    league = await _paste_league(tmp_path, monkeypatch)
    await _open_amendment(league, AM)

    await _paste(league, QUALI_PASTE, RACE_PASTE)

    assert len(await _entered(league)) == 2
    assert "is being amended" not in _sub_said(league)


# ---------------------------------------------------------------------------
# A submission closed by a cancellation (#439 slice 4b, amendment A, 2.9)
# ---------------------------------------------------------------------------

_CLOSED_BY = {
    "the round cancelled": (
        "CANCELLED",
        "⛔ Round 3 has been cancelled, so its results can no longer be entered. Nothing was saved.",
    ),
    "the submission closed, the round not cancelled": (
        "AWAITING_RESULTS",
        "⛔ Round 3's results submission has been closed, so its results can no longer be "
        "entered. Nothing was saved.",
    ),
}


@pytest.mark.parametrize("closed_by", sorted(_CLOSED_BY))
@pytest.mark.parametrize("paste", [
    pytest.param(QUALI_PASTE, id="results"),
    pytest.param("CANCELLED", id="CANCELLED"),
])
async def test_a_paste_into_a_submission_closed_by_a_cancellation_is_refused_and_nothing_is_saved(
    tmp_path, monkeypatch, closed_by, paste,
):
    """Round 3's submission channel is open and asks for the qualifying results. The admin
    pastes the qualifying results, or types `CANCELLED`; before its turn comes, a cancellation
    closes the submission: the round is cancelled, or (closed by `/season cancel` before its
    cascade, or by results being turned off) the round is not yet cancelled. When the turn runs
    the channel is answered once, saying the round is cancelled or its submission closed and
    that nothing was saved: no session is saved, nothing is said in the results channel, and no
    review is asked."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    status, refusal = _CLOSED_BY[closed_by]
    league = await _paste_league(tmp_path, monkeypatch)
    message = typed_message(league.channel(SUBMISSION_CHANNEL), league._member(ADMIN_ID), paste,
                            created_at=league.clock.now)
    await ResultsCog(league.bot).on_message(message)
    await league.write(
        "UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?", ROUND_3
    )
    await league.write("UPDATE rounds SET status = ? WHERE id = ?", status, ROUND_3)

    await run_queue(league.bot)

    assert _sub_said(league).count(refusal) == 1
    assert await _entered(league) == []
    assert league.texts(RESULTS_606) == []
    assert await _review_asks(league) == []
