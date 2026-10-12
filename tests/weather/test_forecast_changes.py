"""A weather forecast on the change queue: its draw, its post and the deletion of the phase it
supersedes, each a job of its own (#439, slice 6).

A phase is drawn, recorded and its calculation written to the log channel in one save (weather
spec :165); its forecast is posted by a job after it, which saves the message's id; and the forecast
of the phase it supersedes is deleted by a job after that. A forecast Discord refuses, or whose
forecast channel is unset, stops the queue, and is tried again as text; a deletion Discord refuses
stops it too, the earlier forecast's record kept. A mystery round's phase 1 is its notice, and its
phases 2 and 3 nothing. A forecast whose turn comes after its round's race is dropped (A25).

The league is `ongoing_league`'s with weather on: season 3 being raced, Pro's round 3 (id 213) at
Silverstone Circuit, 90 days ahead, with a short qualifying and a long race; Pro's forecast channel
is 605 and its role 801. Each test asks for a phase through weather's `ask_forecast`, as the timers
do, runs the queue and reads the forecast channel, the log channel and the database. Images are
off, so a forecast is posted as text, unless a test hands the job a picture by patching
`image_weather_post.attach_forecast`, which the post job reaches through that module as it runs.
"""
from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import AsyncMock

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
    AM,
    DIVISIONS,
    PRO,
    ongoing_league,
    posted_forecast,
    round_id,
)

#: The kind of a forecast's change.
KIND = "weather.forecast"
ROUND = round_id(PRO, 3)
FORECAST = DIVISIONS[PRO][3].forecast
TRACK = "Silverstone Circuit"


async def _league(tmp_path: Any, *, mystery: bool = False) -> Any:
    """The league of the module docstring; round 3 a mystery round where *mystery* is set."""
    league = await ongoing_league(tmp_path, weather=True)
    await league.write("UPDATE rounds SET track_name = ? WHERE division_id = ?", TRACK, PRO)
    if mystery:
        await league.write("UPDATE rounds SET format = 'MYSTERY' WHERE id = ?", ROUND)
    for session in ("SHORT_QUALIFYING", "LONG_RACE"):
        await league.write("INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
                           ROUND, session)
    return league


async def _ask(league: Any, phase: int, round_: int = ROUND) -> None:
    """Ask the queue for *phase* of *round_*, as its timer does; the queue is not run."""
    from leaguebot.weather.services.forecast_changes import ask_forecast

    await ask_forecast(league.bot.change_queue, league.db_path, round_, phase)


async def _posted(league: Any, *phases: int) -> None:
    """Each of *phases*, in turn, asked and run through the queue to its end."""
    for phase in phases:
        await _ask(league, phase)
        await run_queue(league.bot)
    assert await stopped_job(league.db_path) is None


async def _jobs(league: Any) -> list[dict[str, Any]]:
    """Every job of every forecast change, in order, each with its payload read back and laid
    over its change's payload, so a job that carries no phase of its own (the opening draw)
    reads as its change's phase."""
    changes = {row["id"]: json.loads(row["payload"] or "{}")
               for row in await change_rows(league.db_path) if row["kind"] == KIND}
    jobs = [job for job in await step_rows(league.db_path) if job["change_id"] in changes]
    for job in jobs:
        job["payload"] = {**changes[job["change_id"]], **json.loads(job["payload"] or "{}")}
    return jobs


async def _run_through(league: Any, name: str, phase: int) -> None:
    """Run the queue a job at a time until the job *name* of *phase* is done."""
    for _ in range(100):
        if any(job["name"] == name and job["payload"].get("phase") == phase and job["done_at"]
               for job in await _jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"{name} of phase {phase} never finished: {await _jobs(league)}")


def _events(league: Any) -> list[tuple[str, int]]:
    """What the forecast channel saw, in order: each send and delete, with its message."""
    return [(kind, mid) for kind, cid, mid in league.events if cid == FORECAST]


def _ids(events: list[tuple[str, int]], *kinds: str) -> list[int]:
    """The messages of *events*, which are *kinds* in that order."""
    assert [kind for kind, _ in events] == list(kinds), events
    return [mid for _, mid in events]


async def _said(league: Any) -> str:
    """Every log line, sent or still waiting on the old retry queue."""
    from tests.support.change_queue import queued_log_lines

    return "\n".join([league.log(), *await queued_log_lines(league.db_path)])


async def _forecasts(league: Any, round_: int = ROUND) -> dict[int, int]:
    """The forecasts recorded for *round_*: each phase's message id."""
    return {row["phase_number"]: int(row["message_id"]) for row in await league.rows(
        "SELECT phase_number, message_id FROM forecast_messages WHERE round_id = ?", round_)}


async def _draws(league: Any, phase: int, round_: int = ROUND) -> int:
    """How many active draws of *phase* are recorded for *round_*."""
    [row] = await league.rows(
        "SELECT COUNT(*) AS n FROM phase_results WHERE round_id = ? AND phase_number = ? "
        "AND status = 'ACTIVE'", round_, phase)
    return row["n"]


async def _done(league: Any, phase: int, round_: int = ROUND) -> int:
    [row] = await league.rows(f"SELECT phase{phase}_done AS done FROM rounds WHERE id = ?", round_)
    return row["done"]


def _refusing(league: Any) -> None:
    league.channel(FORECAST).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Access")


def _accepting(league: Any) -> None:
    league.channel(FORECAST).send_fails = None


@pytest.mark.parametrize("phase", [1, 2, 3])
async def test_a_phase_s_draw_and_its_calculation_line_are_saved_together_before_its_forecast_is_posted(
    tmp_path, phase,
):
    """The draw's save holds the draw, the phase's flag and its calculation line, and nothing is
    sent until the post job after it (folded defect 6)."""
    league = await _league(tmp_path)
    await _posted(league, *range(1, phase))
    before = len(_events(league))

    await _ask(league, phase)
    await _run_through(league, "draw_forecast", phase)

    assert await _draws(league, phase) == 1
    assert await _done(league, phase) == 1
    assert f"Phase {phase} Calculation Log** | Round #{ROUND}" in await _said(league)
    assert _events(league)[before:] == []

    await run_queue(league.bot)

    [(kind, new)] = [event for event in _events(league)[before:] if event[0] == "send"]
    assert (await _forecasts(league))[phase] == new


@pytest.mark.parametrize("refused", ["picture refused", "text refused"])
async def test_a_forecast_discord_refuses_stops_the_queue_and_is_tried_again_as_text(
    tmp_path, monkeypatch, refused,
):
    """The forecast refused stops the queue at its post, and nothing is put on the old retry
    queue; Retry posts it as text, with no picture drawn, and records its id."""
    from leaguebot.image.services import image_weather_post

    league = await _league(tmp_path)
    drawn = AsyncMock(side_effect=lambda *_a, **_k: discord.File(io.BytesIO(b"png"),
                                                                    filename="forecast.png"))
    if refused == "picture refused":
        monkeypatch.setattr(image_weather_post, "attach_forecast", drawn)
        league.channel(FORECAST).fail_when = lambda _content, kwargs: kwargs.get("file") is not None
    else:
        _refusing(league)

    await _ask(league, 1)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_forecast"
    assert _events(league) == []
    assert await league.rows("SELECT * FROM pending_messages WHERE channel_id = ?", FORECAST) == []

    _accepting(league)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [new] = _ids(_events(league), "send")
    assert league.channel(FORECAST).files == [None]
    assert "Silverstone" in league.texts(FORECAST)[0]
    assert await _forecasts(league) == {1: new}
    if refused == "picture refused":
        assert drawn.await_count == 1


@pytest.mark.parametrize("phase", [2, 3])
async def test_a_phase_posts_before_the_superseded_forecast_is_deleted_by_a_job(tmp_path, phase):
    league = await _league(tmp_path)
    await _posted(league, *range(1, phase))
    earlier = (await _forecasts(league))[phase - 1]
    before = len(_events(league))

    await _ask(league, phase)
    await run_queue(league.bot)

    new, deleted = _ids(_events(league)[before:], "send", "delete")
    assert deleted == earlier
    assert await _forecasts(league) == {phase: new}
    names = [job["name"] for job in await _jobs(league)][-3:]
    assert names == ["draw_forecast", "post_forecast", "delete_forecast"]


async def test_a_superseded_forecast_discord_will_not_delete_stops_the_queue_keeping_its_record(
    tmp_path,
):
    """Phase 1's forecast refuses its deletion when phase 2 supersedes it: the queue stops at the
    delete, phase 1's record kept beside phase 2's; Retry deletes it and drops its record (folded
    defect 7)."""
    league = await _league(tmp_path)
    await _posted(league, 1)
    earlier = (await _forecasts(league))[1]
    message = league.channel(FORECAST).messages[earlier]
    own = message.delete
    message.delete = AsyncMock(side_effect=http_error(discord.Forbidden, status=403,
                                                      text="Missing Permissions"))

    await _ask(league, 2)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "delete_forecast"
    assert earlier in league.channel(FORECAST).messages
    recorded = await _forecasts(league)
    assert recorded[1] == earlier and 2 in recorded

    message.delete = own
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert earlier not in league.channel(FORECAST).messages
    assert list(await _forecasts(league)) == [2]


async def test_a_stop_after_the_draw_posts_the_forecast_when_the_bot_starts(tmp_path):
    """The bot stops once phase 1 is drawn: started again, the queue posts the forecast, and the
    phase is not drawn twice."""
    league = await _league(tmp_path)
    await _ask(league, 1)
    await _run_through(league, "draw_forecast", 1)
    assert _events(league) == []

    await league.restart()
    await run_queue(league.bot)

    [new] = _ids(_events(league), "send")
    assert await _forecasts(league) == {1: new}
    assert await _draws(league, 1) == 1


async def test_a_forecast_whose_id_could_not_be_saved_removes_the_copy_before_it_posts_again(
    tmp_path,
):
    """The forecast is sent and recording it fails: the queue stops at the post, keeping the copy
    it sent; Retry deletes that copy first, posts once more and records the new id."""
    league = await _league(tmp_path)
    await league.write(
        "CREATE TRIGGER refuse_forecast_row BEFORE INSERT ON forecast_messages "
        "BEGIN SELECT RAISE(ABORT, 'the disk is full'); END"
    )
    await _ask(league, 1)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_forecast"
    [copy] = _ids(_events(league), "send")

    await league.write("DROP TRIGGER refuse_forecast_row")
    await retry_job(league.bot)

    events = _events(league)
    assert events[:2] == [("send", copy), ("delete", copy)]
    [new] = _ids(events[2:], "send")
    assert list(league.channel(FORECAST).messages) == [new]
    assert await _forecasts(league) == {1: new}


async def test_the_mystery_notice_s_flag_is_saved_with_its_record_and_a_refused_notice_stops_the_queue(
    tmp_path,
):
    """Round 3 is a mystery round, and its phase 1 asked: the notice's record sets `phase1_done`;
    Discord refusing the notice stops the queue, and Retry posts it, mentioning no role and
    deleting nothing (folded defect 5)."""
    league = await _league(tmp_path, mystery=True)
    _refusing(league)

    await _ask(league, 1)
    await run_queue(league.bot)

    assert await _done(league, 1) == 1
    assert await _draws(league, 1) == 0
    assert (await stopped_job(league.db_path))["name"] == "post_forecast"

    _accepting(league)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [new] = _ids(_events(league), "send")
    assert "<@&" not in league.texts(FORECAST)[0]
    assert await _forecasts(league) == {0: new}


async def test_a_phase_whose_earlier_phase_is_undone_draws_and_posts_the_earlier_first(tmp_path):
    """Phase 3 asked while phase 2 is undone: phase 2 is drawn, posted and supersedes phase 1,
    and only then is phase 3 drawn, posted and supersedes phase 2."""
    league = await _league(tmp_path)
    await _posted(league, 1)
    first = (await _forecasts(league))[1]
    before = len(_events(league))

    await _ask(league, 3)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    second, gone_first, third, gone_second = _ids(_events(league)[before:], "send", "delete",
                                                  "send", "delete")
    assert (gone_first, gone_second) == (first, second)
    assert await _forecasts(league) == {3: third}
    names = [job["name"] for job in await _jobs(league) if job["done_at"]][-7:]
    assert names == ["draw_forecast", "draw_forecast", "post_forecast", "delete_forecast",
                     "draw_forecast", "post_forecast", "delete_forecast"]


async def test_a_blocked_phase_writes_its_line_and_nothing_else(tmp_path):
    """Round 3's circuit is one the bot has no row for: the phase's ⚠️ BLOCKED line is written,
    nothing is drawn or posted, and the queue goes on."""
    league = await _league(tmp_path)
    await league.write("UPDATE rounds SET track_name = 'Nowhere Circuit' WHERE id = ?", ROUND)

    await _ask(league, 1)
    await run_queue(league.bot)

    assert f"Phase 1 BLOCKED for round {ROUND} (Nowhere Circuit)" in await _said(league)
    assert await _draws(league, 1) == 0
    assert await _done(league, 1) == 0
    assert _events(league) == []
    assert await stopped_job(league.db_path) is None


async def test_a_phase_asked_twice_is_drawn_once(tmp_path):
    """Phase 1 asked twice before the queue runs, and again once it has: drawn and posted once."""
    league = await _league(tmp_path)

    await _ask(league, 1)
    await _ask(league, 1)
    await run_queue(league.bot)
    await _ask(league, 1)
    await run_queue(league.bot)

    assert await _draws(league, 1) == 1
    assert len(_events(league)) == 1
    assert await stopped_job(league.db_path) is None


async def test_a_forecast_for_a_division_with_no_forecast_channel_is_drawn_and_its_post_stops_the_queue(
    tmp_path,
):
    """Pro has no forecast channel: phase 1 is drawn and its calculation logged, and the post
    stops the queue naming the command that sets one (A11); set it, Retry: the forecast posted."""
    league = await _league(tmp_path)
    await league.write("UPDATE divisions SET forecast_channel_id = NULL WHERE id = ?", PRO)

    await _ask(league, 1)
    await run_queue(league.bot)

    assert await _draws(league, 1) == 1
    assert f"Phase 1 Calculation Log** | Round #{ROUND}" in await _said(league)
    assert (await stopped_job(league.db_path))["name"] == "post_forecast"
    assert ("**Pro** has no forecast channel set: set one with `/weather channel`, then press "
            "Retry.") in await _said(league)

    await league.write("UPDATE divisions SET forecast_channel_id = ? WHERE id = ?", FORECAST, PRO)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    [new] = _ids(_events(league), "send")
    assert await _forecasts(league) == {1: new}


async def test_a_phase_whose_race_passed_while_it_waited_is_dropped_at_its_draw(tmp_path):
    """Phase 1 asked, and its turn comes only after round 3's race: nothing drawn or posted (A25)."""
    league = await _league(tmp_path)
    await _ask(league, 1)
    league.clock.advance(days=91)

    await run_queue(league.bot)

    assert await _draws(league, 1) == 0
    assert await _done(league, 1) == 0
    assert _events(league) == []
    assert await stopped_job(league.db_path) is None


async def test_a_forecast_whose_race_passed_while_its_post_stood_stopped_is_dropped(tmp_path):
    """Phase 2 is drawn and its post refused; the queue stays stopped past round 3's race. Retry:
    nothing posted, phase 1's forecast still standing, and phase 2's draw kept (A25)."""
    league = await _league(tmp_path)
    await _posted(league, 1)
    earlier = (await _forecasts(league))[1]
    before = len(_events(league))
    _refusing(league)

    await _ask(league, 2)
    await run_queue(league.bot)
    assert (await stopped_job(league.db_path))["name"] == "post_forecast"

    league.clock.advance(days=91)
    _accepting(league)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _events(league)[before:] == []
    assert earlier in league.channel(FORECAST).messages
    assert await _forecasts(league) == {1: earlier}
    assert await _draws(league, 2) == 1
    assert await _done(league, 2) == 1


async def test_a_mystery_round_s_phase_1_is_asked_as_its_notice_and_its_later_phases_not_at_all(
    tmp_path,
):
    """Phases 1, 2 and 3 of a mystery round asked: one forecast change, of its notice (phase 0).
    Round 4's phase 2 asked, and the round amended to a mystery round before it runs: dropped,
    nothing drawn or posted."""
    league = await _league(tmp_path, mystery=True)

    for phase in (1, 2, 3):
        await _ask(league, phase)

    asked = [json.loads(row["payload"]) for row in await change_rows(league.db_path)
             if row["kind"] == KIND]
    assert [(each["round_id"], each["phase"]) for each in asked] == [(ROUND, 0)]

    later = round_id(PRO, 4)
    await _ask(league, 2, later)
    await league.write("UPDATE rounds SET format = 'MYSTERY' WHERE id = ?", later)
    await run_queue(league.bot)

    assert await _draws(league, 2, later) == 0
    assert await _forecasts(league, later) == {}
    assert await stopped_job(league.db_path) is None


# ---------------------------------------------------------------------------
# The deletion of forecasts a day after the round, and their clearing, as jobs
# ---------------------------------------------------------------------------

#: The kinds of the clean-up a day after a round and of the clearing of every forecast.
CLEAN_UP_KIND = "weather.forecast.clean_up"
FLUSH_KIND = "weather.forecast.flush"
#: Am's forecast channel, and its round 3.
AM_FORECAST = DIVISIONS[AM][3].forecast
AM_ROUND = round_id(AM, 3)


async def _deletes_of(league: Any, kind: str) -> list[dict[str, Any]]:
    """The `delete_forecast` jobs of every change of *kind*, in order, payloads read back."""
    ids = [row["id"] for row in await change_rows(league.db_path) if row["kind"] == kind]
    jobs = [job for job in await step_rows(league.db_path)
            if job["change_id"] in ids and job["name"] == "delete_forecast"]
    for job in jobs:
        job["payload"] = json.loads(job["payload"] or "{}")
    return jobs


async def _clean_up(league: Any, round_: int = ROUND) -> None:
    """Ask the queue for *round_*'s clean-up, as its timer does a day after the race."""
    from leaguebot.weather.services.forecast_changes import ask_forecast_clean_up

    await ask_forecast_clean_up(league.bot.change_queue, league.db_path, round_)


async def test_a_clean_up_deletes_the_phase_3_forecast_by_a_job_and_a_refusal_stops_the_queue(
    tmp_path,
):
    """Pro's round 3 has its Phase 3 forecast (9303) standing, which Discord refuses to delete:
    the clean-up stops the queue at its `delete_forecast` job, the forecast and its record kept.
    Discord accepting, Retry deletes it and drops its record."""
    league = await _league(tmp_path)
    await posted_forecast(league, ROUND, 3, 9303)
    league.undeletable.add(9303)

    await _clean_up(league)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "delete_forecast"
    assert 9303 in league.channel(FORECAST).messages
    assert await _forecasts(league) == {3: 9303}

    league.undeletable.discard(9303)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert ("delete", 9303) in _events(league)
    assert await _forecasts(league) == {}
    [job] = await _deletes_of(league, CLEAN_UP_KIND)
    assert job["payload"]["phase"] == 3


@pytest.mark.parametrize("standing", [
    pytest.param((3,), id="phase 3 posted"),
    pytest.param((2,), id="phase 3 never posted"),
    pytest.param((1,), id="phase 2 never posted"),
    pytest.param((1, 2), id="phases 1 and 2 standing"),
])
async def test_a_clean_up_deletes_whichever_phase_is_still_standing(tmp_path, standing):
    """A day after round 3's race, Pro's forecasts still recorded are the ones standing: Phase 3
    in the normal case; Phase 2 where Phase 3 never posted (discarded, or dropped as the race was
    run), Phase 1 where Phase 2 never did either; Phases 1 and 2 both where Phase 3 never posted
    and Phase 2's deletion of Phase 1 was discarded. The clean-up deletes each by a
    `delete_forecast` job of its own, and no record of any is left."""
    league = await _league(tmp_path)
    messages = [9300 + phase for phase in standing]
    for phase, message in zip(standing, messages):
        await posted_forecast(league, ROUND, phase, message)

    await _clean_up(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    for message in messages:
        assert ("delete", message) in _events(league)
        assert message not in league.channel(FORECAST).messages
    assert await _forecasts(league) == {}
    jobs = await _deletes_of(league, CLEAN_UP_KIND)
    assert sorted(job["payload"]["phase"] for job in jobs) == list(standing)


async def test_a_clean_up_leaves_a_mystery_round_s_notice_standing(tmp_path):
    """Round 3 is a mystery round, whose notice (phase 0) was posted in place of its forecasts:
    a day after its race the clean-up deletes nothing, and the notice and its record stand."""
    league = await _league(tmp_path, mystery=True)
    await _posted(league, 1)
    [notice] = _ids(_events(league), "send")

    await _clean_up(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert ("delete", notice) not in _events(league)
    assert notice in league.channel(FORECAST).messages
    assert await _forecasts(league) == {0: notice}
    assert await _deletes_of(league, CLEAN_UP_KIND) == []


async def test_a_flush_deletes_every_forecast_each_by_a_job_of_its_own(tmp_path):
    """Test mode switched off with three forecasts standing (Pro's Phase 1 and 2 of round 3,
    9301 and 9302, and Am's Phase 1 of round 3, 9401): the flush deletes each by a
    `delete_forecast` job of its own, and no forecast record is left."""
    league = await _league(tmp_path)
    await posted_forecast(league, ROUND, 1, 9301)
    await posted_forecast(league, ROUND, 2, 9302)
    await posted_forecast(league, AM_ROUND, 1, 9401)

    from leaguebot.weather.services.forecast_changes import ask_forecast_flush

    await ask_forecast_flush(league.bot.change_queue)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    deleted = {mid for kind, cid, mid in league.events
               if kind == "delete" and cid in (FORECAST, AM_FORECAST)}
    assert deleted == {9301, 9302, 9401}
    assert len(await _deletes_of(league, FLUSH_KIND)) == 3
    assert await league.rows("SELECT * FROM forecast_messages") == []


@pytest.mark.parametrize("gone", ["message gone", "row already gone"])
async def test_a_forecast_gone_by_hand_completes_its_delete_and_drops_its_row(tmp_path, gone):
    """Pro's round 3 Phase 3 forecast (9303) is gone before its clean-up's delete runs: deleted
    by hand from the channel, or its record dropped after the clean-up planned the delete. The
    delete job completes without stopping the queue, and no record of it is left."""
    league = await _league(tmp_path)
    await posted_forecast(league, ROUND, 3, 9303)

    await _clean_up(league)
    if gone == "message gone":
        del league.channel(FORECAST).messages[9303]
    else:
        await run_queue(league.bot, steps=1)
        await league.write("DELETE FROM forecast_messages WHERE round_id = ?", ROUND)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [job] = await _deletes_of(league, CLEAN_UP_KIND)
    assert job["done_at"]
    assert await _forecasts(league) == {}
    assert ("delete", 9303) not in _events(league)


# ---------------------------------------------------------------------------
# The weather timers ask the change queue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "timer, mystery, expected",
    [
        ("phase 1", False, (KIND, 1)),
        ("phase 2", False, (KIND, 2)),
        ("phase 3", False, (KIND, 3)),
        ("mystery notice", True, (KIND, 0)),
        ("phase 1 of a mystery round", True, (KIND, 0)),
        ("clean-up", False, (CLEAN_UP_KIND, None)),
    ],
)
async def test_each_weather_timer_asks_the_queue(tmp_path, timer, mystery, expected):
    """Each callback `timer_callbacks` gives the scheduler, fired for Pro's round 3, puts one
    change on the queue it was handed, and runs nothing: phases 1 to 3 their forecast, the
    mystery notice's timer and a mystery round's phase 1 the notice (phase 0), and the clean-up,
    a day after the race with the Phase 3 forecast (9303) standing, its deletion."""
    from leaguebot.weather.services.forecast_changes import timer_callbacks

    league = await _league(tmp_path, mystery=mystery)
    if timer == "clean-up":
        await posted_forecast(league, ROUND, 3, 9303)
    callbacks = timer_callbacks(league.bot.change_queue, league.db_path)

    fire = {
        "phase 1": callbacks.phases[1],
        "phase 2": callbacks.phases[2],
        "phase 3": callbacks.phases[3],
        "mystery notice": callbacks.mystery_notice,
        "phase 1 of a mystery round": callbacks.phases[1],
        "clean-up": callbacks.clean_up,
    }[timer]
    await fire(ROUND)

    [row] = await change_rows(league.db_path)
    payload = json.loads(row["payload"])
    kind, phase = expected
    assert (row["kind"], payload["round_id"]) == (kind, ROUND)
    if phase is not None:
        assert payload["phase"] == phase
    assert all(not job["done_at"] for job in await step_rows(league.db_path))
