"""The weather deadlines on the change queue (#439, slice 7).

`/weather config phase-1-deadline`, `phase-2-deadline` and `phase-3-deadline` each ask one
settings change: acknowledged at once, checked at the ask and again as it runs, and saving the
deadline, its `WEATHER_CONFIG_SET` audit entry and its log line in one save (core specification,
"The record of what changed"; weather specification :27-39). Each test drives the real command
past its tier guard, on a database built by the migrations, with the real queue and "now" pinned,
and runs the queue.
"""
from __future__ import annotations

from typing import Any

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.core.utils.interaction_errors import failure_reply
from tests.support.change_queue import (
    MEMBER_ID,
    acknowledgement,
    change_rows,
    run_queue,
    stopped_job,
)
from tests.support.settings_league import (
    audit_rows,
    fault_in,
    interaction,
    last_said,
    log_lines,
    settings_league,
    switch,
)
from tests.support.undecorate import undecorate

#: The member as a saved log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
#: The defaults a league starts from: Phase 1 5 days, Phase 2 2 days, Phase 3 2 hours.
DEFAULTS = {"phase_1_days": 5, "phase_2_days": 2, "phase_3_hours": 2}
CONFIRMED = "❌ Phase deadline configuration cannot be changed once a season's placements are confirmed."
MODULE_OFF = "❌ The Weather module is not enabled."

#: Each command: its method, its argument, its column, its phase, its unit.
PHASES = {
    1: ("phase_1_deadline", "days", "phase_1_days", "day(s)"),
    2: ("phase_2_deadline", "days", "phase_2_days", "day(s)"),
    3: ("phase_3_deadline", "hours", "phase_3_hours", "hour(s)"),
}


async def _ask(league: Any, phase: int, value: int) -> Any:
    """Run `/weather config phase-<phase>-deadline <value>` as Admin; the queue is not run."""
    from leaguebot.weather.cogs.weather_cog import WeatherCog

    method, argument, _column, _unit = PHASES[phase]
    asked = interaction(league.bot, f"weather config phase-{phase}-deadline")
    await undecorate(getattr(WeatherCog, method))(WeatherCog(league.bot), asked, **{argument: value})
    return asked


async def _deadlines(db_path: str) -> dict[str, int]:
    """The three deadlines the league holds, the defaults where it has saved none."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase_1_days, phase_2_days, phase_3_hours FROM weather_pipeline_config"
        )
        row = await cursor.fetchone()
    return dict(row) if row is not None else dict(DEFAULTS)


async def _confirm_a_season(db_path: str) -> None:
    """A season whose placements are confirmed: active, under way."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE', 'ONGOING')"
        )
        await db.commit()


def _refused(reply: str, phase: int) -> str:
    return f"⛔ `/weather config phase-{phase}-deadline` refused for {NAMED} — {reply.removeprefix('❌ ')}"


@pytest.mark.parametrize(
    ("phase", "value", "details"),
    [
        (1, 6, ("Phase 1: 6 day(s)", "Phase 2: 2 day(s)", "Phase 3: 2 hour(s)")),
        (2, 3, ("Phase 1: 5 day(s)", "Phase 2: 3 day(s)", "Phase 3: 2 hour(s)")),
        (3, 12, ("Phase 1: 5 day(s)", "Phase 2: 2 day(s)", "Phase 3: 12 hour(s)")),
    ],
)
async def test_each_deadline_saves_its_value_audit_entry_and_line_together(
    tmp_path, phase, value, details
):
    """Each deadline, once its change has run, holds its new value, with one
    `WEATHER_CONFIG_SET` entry naming the member and the column's old and new values, and the
    Success line naming the command and all three deadlines beneath."""
    league = await settings_league(tmp_path)
    column = PHASES[phase][2]

    await _ask(league, phase, value)
    await run_queue(league.bot)

    assert (await _deadlines(league.db_path))[column] == value
    audits = await audit_rows(league.db_path)
    assert [(a["change_type"], a["actor_id"], a["old_value"], a["new_value"]) for a in audits] == [
        ("WEATHER_CONFIG_SET", MEMBER_ID, {column: DEFAULTS[column]}, {column: value})
    ]
    assert await log_lines(league) == [
        f"{NAMED} | /weather config phase-{phase}-deadline | Success"
        + "".join(f"\n  {detail}" for detail in details)
    ]


async def test_a_deadline_is_acknowledged_then_updated_with_all_three_deadlines(tmp_path):
    """The member is answered at once that the Phase 1 deadline is being set to 6 day(s), and
    that answer is updated, once the change has run, with today's success naming the other two."""
    league = await settings_league(tmp_path)

    asked = await _ask(league, 1, 6)
    assert acknowledgement(asked).startswith("⏳ Setting the Phase 1 deadline to 6 day(s). ")
    await run_queue(league.bot)

    assert last_said(asked) == (
        "✅ Phase 1 deadline set to **6 day(s)** before round. (Phase 2: 2d, Phase 3: 2h)"
    )


async def test_a_deadline_asked_before_placements_are_confirmed_and_run_after_is_refused(tmp_path):
    """A deadline asked while no season is confirmed, whose season's placements are confirmed
    before it runs, is refused when it runs in today's words, the deadline left as it was."""
    league = await settings_league(tmp_path)

    asked = await _ask(league, 1, 6)
    await _confirm_a_season(league.db_path)
    await run_queue(league.bot)

    assert last_said(asked) == CONFIRMED
    assert await log_lines(league) == [_refused(CONFIRMED, 1)]
    assert (await _deadlines(league.db_path))["phase_1_days"] == 5
    assert await audit_rows(league.db_path) == []


async def test_a_deadline_out_of_order_with_one_set_ahead_of_it_is_refused_when_it_runs(tmp_path):
    """Phase 2 asked to 4 days, then Phase 1 asked to 4 days (in order with the Phase 2 of 2 days
    held when asked): the first runs, and the second, now equal to Phase 2, is refused when it
    runs with the ordering rule's words, Phase 1 left at 5 days."""
    league = await settings_league(tmp_path)

    await _ask(league, 2, 4)
    second = await _ask(league, 1, 4)
    await run_queue(league.bot)

    reply = "❌ Phase 1 deadline (4d = 96h) must be strictly greater than Phase 2 deadline (4d = 96h)."
    assert last_said(second) == reply
    assert await _deadlines(league.db_path) == {
        "phase_1_days": 5, "phase_2_days": 4, "phase_3_hours": 2,
    }
    assert (await log_lines(league))[-1] == _refused(reply, 1)


async def test_a_deadline_asked_while_the_module_is_turned_off_ahead_of_it_is_refused(tmp_path):
    """A deadline asked while weather is on, the module turned off before it runs, is refused
    when it runs as the module refusal, nothing saved."""
    league = await settings_league(tmp_path)

    asked = await _ask(league, 3, 12)
    await switch(league.bot, ("weather",), on=False)
    await run_queue(league.bot)

    assert last_said(asked) == MODULE_OFF
    assert await log_lines(league) == [_refused(MODULE_OFF, 3)]
    assert (await _deadlines(league.db_path))["phase_3_hours"] == 2
    assert await audit_rows(league.db_path) == []


@pytest.mark.parametrize(
    ("weather_on", "confirmed", "value", "reply"),
    [
        (False, True, 0, MODULE_OFF),
        (True, True, 0, CONFIRMED),
        (True, False, 0, "❌ Phase 1 deadline must be at least 1 day."),
    ],
    ids=["module-before-season", "season-before-range", "range"],
)
async def test_the_refusals_keep_today_s_order(tmp_path, weather_on, confirmed, value, reply):
    """`/weather config phase-1-deadline 0` is refused at once, nothing queued, by the first
    rule it breaks in today's order: the module off, then placements confirmed, then below one;
    the member's reply is the refusal alone and one "⛔" line records it."""
    league = await settings_league(tmp_path)
    if not weather_on:
        await switch(league.bot, ("weather",), on=False)
    if confirmed:
        await _confirm_a_season(league.db_path)

    asked = await _ask(league, 1, value)

    assert last_said(asked) == reply
    assert await change_rows(league.db_path) == []
    assert await log_lines(league) == [_refused(reply, 1)]


async def test_a_fault_in_a_deadline_s_save_saves_nothing_and_the_lane_goes_on(tmp_path):
    """The audit write of a Phase 1 deadline's save faults: nothing is saved, the member is told
    so and to run it again, one failure line is recorded, and a Phase 2 deadline asked behind it
    still runs."""
    league = await settings_league(tmp_path)

    asked = await _ask(league, 1, 6)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot, steps=1)
    behind = await _ask(league, 2, 3)
    await run_queue(league.bot)

    assert last_said(asked) == failure_reply(
        "`/weather config phase-1-deadline`",
        "Nothing was saved. Run `/weather config phase-1-deadline` again to retry.",
    )
    assert await _deadlines(league.db_path) == {
        "phase_1_days": 5, "phase_2_days": 3, "phase_3_hours": 2,
    }
    lines = await log_lines(league)
    assert [line for line in lines if line.startswith("❌ ")] == [
        line for line in lines
        if line.startswith(f"❌ `/weather config phase-1-deadline` failed for {NAMED} — ")
    ] != []
    assert not any("| Success" in line and "phase-1" in line for line in lines)
    assert last_said(behind).startswith("✅ Phase 2 deadline set to **3 day(s)**")


async def test_a_check_fault_when_a_deadline_runs_says_nothing_was_saved_records_a_failure_and_the_lane_goes_on(
    tmp_path,
):
    """A Phase 1 deadline passes its check when asked, a Phase 2 deadline is asked behind it,
    and the module's flag then faults once as it is read: the first read is the Phase 1 change's
    check as it comes up to run. Nothing of it is saved, the member is told so and to run it
    again, one failure line naming the kind of fault is recorded and no refusal line, the change
    is ended without stopping the queue, and the Phase 2 deadline behind it still runs."""
    league = await settings_league(tmp_path)

    asked = await _ask(league, 1, 6)
    behind = await _ask(league, 2, 3)
    modules = league.bot.module_service
    read = modules.is_weather_enabled
    reads = 0

    async def _faults_once() -> bool:
        nonlocal reads
        reads += 1
        if reads == 1:
            raise RuntimeError("the module flags could not be read")
        return await read()

    modules.is_weather_enabled = _faults_once
    await run_queue(league.bot)

    assert last_said(asked) == failure_reply(
        "`/weather config phase-1-deadline`",
        "Nothing was saved. Run `/weather config phase-1-deadline` again to retry.",
    )
    assert await _deadlines(league.db_path) == {
        "phase_1_days": 5, "phase_2_days": 3, "phase_3_hours": 2,
    }
    lines = await log_lines(league)
    assert [line for line in lines if line.startswith("❌ ")] == [
        f"❌ `/weather config phase-1-deadline` failed for {NAMED} — RuntimeError. "
        "The details are in the host's log."
    ]
    assert not any(line.startswith("⛔") for line in lines)
    assert [a["new_value"] for a in await audit_rows(league.db_path)] == [{"phase_2_days": 3}]
    assert [row["state"] for row in await change_rows(league.db_path)] == ["REFUSED", "DONE"]
    assert await stopped_job(league.db_path) is None
    assert last_said(behind).startswith("✅ Phase 2 deadline set to **3 day(s)**")
    assert reads >= 2
