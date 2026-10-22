"""A points edit that changes nothing is answered and recorded as such (#482; #439, slice 7).

The rule is the core specification's "The record of what changed": a request that changes
nothing replies that nothing changed, writes no audit entry, and records one line in the success
form saying so. `/results config session`, `fl` and `fl-plimit`, and `/results amend session`,
`fl` and `fl-plimit`, set to the value the table (or the modification store) already holds are
such requests, as is `/results amend revert` with nothing staged.

Each runs on the change queue: acknowledged at once, and judged held when it runs, never when it
is asked (core :125; results :177, :211), so each test runs the queue and reads the reply as the
acknowledgement's update and the line from the log.

A same-value amend stages nothing, so it leaves the modified flag as it stood and does not block
`/results amend toggle` off (owner, 2026-09-30). Where the table is out of order, the
nothing-changed reply still carries the warning naming the positions at fault. The refusal comes
first (slice 3, decision 23): a request that cannot be carried out is refused as today, which
`test_results_amend_refusals.py` and `test_results_config_refusals.py` pin.

The configuration, the season's points and the modification store live in a real database built
from the production migrations, so each case holds the values the command reads.
"""
from __future__ import annotations

from typing import Any

import pytest
from discord import app_commands

from leaguebot.core.db.database import get_connection
from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.results.models.points_config import SessionType
from tests.support import saves
from tests.support.change_queue import MEMBER_ID, run_queue
from tests.support.settings_league import (
    audit_rows,
    interaction,
    last_said,
    log_lines,
    said,
    settings_league,
)
from tests.support.undecorate import undecorate

#: Why the tests here fail until the points edits run on the queue.
UNBUILT = "#439: the points edits do not run on the change queue yet"
#: The member as a saved log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
CONFIG = "100%"
SEASON_ID = 1

_RACE = app_commands.Choice(name="Feature Race", value="FEATURE_RACE")


async def _league(tmp_path, table: list[tuple[int, int]]) -> Any:
    """A league, results alone on, holding CONFIG, whose Feature Race table is *table* with a
    fastest-lap bonus of 1 pt for the top 10, and a running season scored by the same table, in
    amendment mode with nothing staged."""
    league = await settings_league(tmp_path, modules=("results",))
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE', 'ONGOING')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, CONFIG),
        )
        for position, points in table:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, "
                "position, points) VALUES (?, ?, 'FEATURE_RACE', ?, ?)",
                (SEASON_ID, CONFIG, position, points),
            )
        await db.execute(
            "INSERT INTO season_points_fl (season_id, config_name, session_type, fl_points, "
            "fl_position_limit) VALUES (?, ?, 'FEATURE_RACE', 1, 10)",
            (SEASON_ID, CONFIG),
        )
        await db.commit()
    await saves.create_config(league.db_path, CONFIG)
    for position, points in table:
        await saves.set_session_points(
            league.db_path, CONFIG, SessionType.FEATURE_RACE, position, points
        )
    await saves.set_fl_bonus(league.db_path, CONFIG, SessionType.FEATURE_RACE, 1)
    await saves.set_fl_position_limit(league.db_path, CONFIG, SessionType.FEATURE_RACE, 10)
    await saves.enable_amendment_mode(league.db_path, SEASON_ID)
    return league


async def _ask(league, command: str, method, *args) -> Any:
    """`/<command>` (`ResultsCog.<method>`) run by Admin with *args*; the queue is not run."""
    asked = interaction(league.bot, command)
    await undecorate(method)(ResultsCog(league.bot), asked, *args)
    return asked


async def _modified_flag(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT modified_flag FROM season_amendment_state WHERE season_id = ?",
            (SEASON_ID,),
        )
        return (await cursor.fetchone())[0]


# What each command is given: the value the table already holds.
_CONFIG_SETS = {
    "results config session": (ResultsCog.config_session, (CONFIG, _RACE, 1, 25)),
    "results config fl": (ResultsCog.config_fl, (CONFIG, _RACE, 1)),
    "results config fl-plimit": (ResultsCog.config_fl_plimit, (CONFIG, _RACE, 10)),
}
_AMEND_SETS = {
    "results amend session": (ResultsCog.amend_session, (CONFIG, _RACE, 1, 25)),
    "results amend fl": (ResultsCog.amend_fl, (CONFIG, _RACE, 1)),
    "results amend fl-plimit": (ResultsCog.amend_fl_plimit, (CONFIG, _RACE, 10)),
}


async def _assert_nothing_changed(league, asked, command: str) -> None:
    """Admin is acknowledged, then told nothing changed, never that a value was set, and one
    line in the success form records it, naming the configuration."""
    assert said(asked)[0].startswith("⏳ ")
    replied = last_said(asked)
    assert "nothing changed" in replied.lower()
    assert "✅ Set" not in replied
    assert "Updated in modification store" not in replied
    [line] = await log_lines(league)
    assert line.splitlines()[0] == f"{NAMED} | /{command} | Nothing changed"
    assert CONFIG in line


@pytest.mark.parametrize("command", sorted(_CONFIG_SETS))
async def test_a_config_set_to_the_value_it_holds_changes_nothing(tmp_path, command):
    """100% gives 25 pts for a Feature Race win and 1 pt for its fastest lap in the top 10.
    Admin sets one of those values to what it already is, and the queue runs."""
    league = await _league(tmp_path, [(1, 25), (2, 18)])
    before = len(await audit_rows(league.db_path))
    method, args = _CONFIG_SETS[command]

    asked = await _ask(league, command, method, *args)
    await run_queue(league.bot)

    await _assert_nothing_changed(league, asked, command)
    assert len(await audit_rows(league.db_path)) == before


@pytest.mark.xfail(strict=True, reason=UNBUILT)
@pytest.mark.parametrize("command", sorted(_AMEND_SETS))
async def test_an_amend_set_to_the_value_staged_changes_nothing(tmp_path, command):
    """The season is in amendment mode with nothing staged; its store gives 25 pts for a win
    and 1 pt for the fastest lap in the top 10. Admin amends one of those to what it already
    is, and the queue runs: no audit entry, and the modified flag stays clear."""
    league = await _league(tmp_path, [(1, 25), (2, 18)])
    before = len(await audit_rows(league.db_path))
    method, args = _AMEND_SETS[command]

    asked = await _ask(league, command, method, *args)
    await run_queue(league.bot)

    await _assert_nothing_changed(league, asked, command)
    assert len(await audit_rows(league.db_path)) == before
    assert await _modified_flag(league.db_path) == 0


@pytest.mark.parametrize("command", sorted(_AMEND_SETS))
async def test_a_same_value_amend_leaves_amendment_mode_free_to_switch_off(tmp_path, command):
    """After an amend that staged nothing has run, `/results amend toggle` switches amendment
    mode off when it runs, never refusing for uncommitted changes."""
    league = await _league(tmp_path, [(1, 25), (2, 18)])
    method, args = _AMEND_SETS[command]
    await _ask(league, command, method, *args)
    await run_queue(league.bot)

    toggle = await _ask(league, "results amend toggle", ResultsCog.amend_toggle)
    await run_queue(league.bot)

    assert said(toggle)[0].startswith("⏳ ")
    assert last_said(toggle) == "✅ Amendment mode disabled."
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT amendment_active FROM season_amendment_state WHERE season_id = ?",
            (SEASON_ID,),
        )
        assert (await cursor.fetchone())[0] == 0


async def test_a_revert_with_nothing_staged_records_that_nothing_changed(tmp_path):
    """Amendment mode is on and nothing is staged. Admin runs `/results amend revert` and the
    queue runs: told nothing changed, and one nothing-changed line, never a revert."""
    league = await _league(tmp_path, [(1, 25), (2, 18)])

    asked = await _ask(league, "results amend revert", ResultsCog.amend_revert)
    await run_queue(league.bot)

    assert said(asked)[0].startswith("⏳ ")
    replied = last_said(asked)
    assert "nothing changed" in replied.lower()
    assert "reverted" not in replied
    [line] = await log_lines(league)
    assert line.splitlines()[0] == f"{NAMED} | /results amend revert | Nothing changed"
    assert await _modified_flag(league.db_path) == 0


@pytest.mark.parametrize(
    "command",
    [
        "results config session",
        pytest.param("results amend session", marks=pytest.mark.xfail(strict=True, reason=UNBUILT)),
    ],
)
async def test_a_same_value_set_on_an_out_of_order_table_keeps_the_warning(tmp_path, command):
    """The Feature Race table gives 18 pts for a win and 25 for second, out of order. Admin
    sets second place to the 25 it already holds, and the queue runs: told nothing changed, and
    still warned which positions are at fault."""
    league = await _league(tmp_path, [(1, 18), (2, 25)])
    method = {
        "results config session": ResultsCog.config_session,
        "results amend session": ResultsCog.amend_session,
    }[command]

    asked = await _ask(league, command, method, CONFIG, _RACE, 2, 25)
    await run_queue(league.bot)

    assert said(asked)[0].startswith("⏳ ")
    replied = last_said(asked)
    assert "nothing changed" in replied.lower()
    assert "out of order" in replied
    assert "position 1 (18 pts) < position 2 (25 pts)" in replied
    [line] = await log_lines(league)
    assert line.splitlines()[0] == f"{NAMED} | /{command} | Nothing changed"


@pytest.mark.parametrize(
    ("current", "requested", "stands"),
    [
        pytest.param({"points": 25}, {"points": 25}, True, id="same-value"),
        pytest.param({"points": 25}, {"points": 30}, False, id="another-value"),
        pytest.param({"points": None}, {"points": 0}, False, id="absent-is-not-zero"),
        pytest.param({}, {"points": 25}, False, id="nothing-read"),
        pytest.param(
            {"fl_points": 1, "fl_position_limit": 10}, {"fl_position_limit": 10}, True,
            id="only-what-is-asked",
        ),
        pytest.param(
            {"fl_points": 1, "fl_position_limit": None}, {"fl_position_limit": 10}, False,
            id="limit-not-set",
        ),
    ],
)
def test_the_judgement_of_values_that_stand(current, requested, stands):
    """Given the values the cog has read and the values asked for, the request changes nothing
    only where every value asked for is already held; a value not held at all is never the same
    as one worth nothing."""
    from leaguebot.results.services.points_config_service import values_stand

    assert values_stand(current, requested) is stands
