"""Three small `/results` commands: detaching a configuration, and the two fastest-lap amends.

Issue #208. `/results config detach`, `/results amend fl` and `/results amend fl-plimit` were
uncovered. None is complicated; each carries one rule worth stating.

**Detaching is for a season still in setup.** Once a season is active its points are
snapshotted, and pulling a configuration out from under a running championship would leave the
snapshot describing a table the season no longer names. After that point, `/results amend` is
the route — through a modification store a league admin reviews.

**Detaching something not attached is an answer, not a failure.** The manager wanted it gone
and it is gone; the reply says so rather than reporting an error for a state that is already
what they asked for.

**The fastest-lap amends write the modification store, not the season.** Nothing a league sees
changes until the amendment is reviewed and approved, which is why both refuse outright when
amendment mode is off rather than writing somewhere nothing will read.

**All three are settings changes on the change queue** (#439, slice 7): a refusal the state when
asked gives is answered at once, nothing queued; anything else is saved when the change runs, its
reply and line following. The tests drive the real commands on a league built by the migrations,
with the real services and queue, and run the queue: the points configuration `Standard` (25 for
a win, a fastest-lap bonus of 1 point for the top 10), and the season each test names.
"""
from __future__ import annotations

from typing import Any

import pytest
from discord import app_commands

from leaguebot.core.db.database import get_connection
from leaguebot.results.cogs.results_cog import ResultsCog
from tests.support import saves
from tests.support.change_queue import MEMBER_ID, change_rows, run_queue
from tests.support.settings_league import interaction, log_lines, said, settings_league
from tests.support.undecorate import undecorate

SEASON_ID = 1
CONFIG = "Standard"
#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
LINKS = "SELECT config_name FROM season_points_links ORDER BY config_name"
STAGED_FL = (
    "SELECT fl_points, fl_position_limit FROM season_modification_fl "
    "WHERE session_type = 'FEATURE_RACE'"
)
_RACE = app_commands.Choice(name="Feature Race", value="FEATURE_RACE")


async def _league(tmp_path: Any, *, results: bool = True, season: str | None = "SETUP",
                  attached: bool = True, amending: bool = False) -> Any:
    """A league with results on (*results*) and the configuration CONFIG. *season* is the status
    of its one season (SEASON_ID), "SETUP" or "ACTIVE", or None for no season; with *attached*,
    CONFIG is attached to it, its Feature Race 25 for P1 and a fastest-lap bonus of 1 point in
    the top 10. With *amending*, the season's amendment mode is on, its store a copy of those."""
    league = await settings_league(tmp_path, modules=("results",) if results else ())
    await saves.create_config(league.db_path, CONFIG)
    if season is None:
        return league
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 1, '2026-01-01', ?, ?)",
            (SEASON_ID, season, "CONFIGURATION" if season == "SETUP" else "ONGOING"),
        )
        if attached:
            await db.execute(
                "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
                (SEASON_ID, CONFIG),
            )
            await db.execute(
                "INSERT INTO season_points_entries "
                "(season_id, config_name, session_type, position, points) "
                "VALUES (?, ?, 'FEATURE_RACE', 1, 25)",
                (SEASON_ID, CONFIG),
            )
            await db.execute(
                "INSERT INTO season_points_fl "
                "(season_id, config_name, session_type, fl_points, fl_position_limit) "
                "VALUES (?, ?, 'FEATURE_RACE', 1, 10)",
                (SEASON_ID, CONFIG),
            )
        await db.commit()
    if amending:
        await saves.enable_amendment_mode(league.db_path, SEASON_ID)
    return league


async def _rows(db_path: str, sql: str) -> list[tuple[Any, ...]]:
    async with get_connection(db_path) as db:
        return [tuple(row) for row in await (await db.execute(sql)).fetchall()]


async def _detach(league: Any) -> Any:
    """Run `/results config detach Standard` as Admin, then the queue."""
    asked = interaction(league.bot, "results config detach")
    await undecorate(ResultsCog.config_detach)(ResultsCog(league.bot), asked, CONFIG)
    await run_queue(league.bot)
    return asked


async def _fl(league: Any, command: str, value: int) -> Any:
    """Run `/results amend fl` (*command* "amend_fl") or `fl-plimit` ("amend_fl_plimit") for
    Standard's Feature Race with *value* as Admin, then the queue."""
    name = {"amend_fl": "results amend fl", "amend_fl_plimit": "results amend fl-plimit"}[command]
    asked = interaction(league.bot, name)
    await undecorate(getattr(ResultsCog, command))(
        ResultsCog(league.bot), asked, CONFIG, _RACE, value
    )
    await run_queue(league.bot)
    return asked


def _replied(asked: Any) -> str:
    return "\n".join(said(asked))


async def _logged(league: Any) -> str:
    return "\n".join(await log_lines(league))


# ---------------------------------------------------------------------------
# /results config detach
# ---------------------------------------------------------------------------


async def test_a_configuration_is_detached(tmp_path):
    """Season #1 in setup with Standard attached: once the change has run, Standard is no
    longer attached, the reply says so, and the line records the success."""
    league = await _league(tmp_path)

    asked = await _detach(league)

    assert await _rows(league.db_path, LINKS) == []
    assert "detached from the current season" in _replied(asked)
    logged = await _logged(league)
    assert "/results config detach | Success" in logged
    assert f"config: {CONFIG}" in logged


async def test_detaching_from_a_running_season_is_refused(tmp_path):
    """Its points are snapshotted; `/results amend` is the route once it has started. Refused at
    once, nothing queued, Standard still attached, and the log records the refusal and its
    reason."""
    league = await _league(tmp_path, season="ACTIVE")

    asked = await _detach(league)

    assert "only allowed for seasons in SETUP" in _replied(asked)
    assert await change_rows(league.db_path) == []
    assert await _rows(league.db_path, LINKS) == [(CONFIG,)]
    assert await log_lines(league) == [
        f"⛔ `/results config detach` refused for {NAMED} — "
        "Config detachment is only allowed for seasons in SETUP."
    ]


async def test_detaching_what_is_not_attached_is_an_answer(tmp_path):
    """The manager wanted it gone and it is gone. Nothing changed, and the log says so in one
    line, as every request that changes nothing does (#482)."""
    league = await _league(tmp_path, attached=False)

    asked = await _detach(league)

    assert "is not attached to this season" in _replied(asked)
    assert "❌" not in _replied(asked)
    lines = await log_lines(league)
    assert len(lines) == 1
    assert lines[0].splitlines()[0].endswith("| /results config detach | Nothing changed")
    assert CONFIG in lines[0]


async def test_detaching_with_no_season_is_refused(tmp_path):
    """No season at all: refused at once, nothing queued."""
    league = await _league(tmp_path, season=None)

    asked = await _detach(league)

    assert "No season found" in _replied(asked)
    assert await change_rows(league.db_path) == []


async def test_detaching_is_refused_while_the_module_is_off(tmp_path):
    """Results off: refused before anything is read or deferred, nothing queued, Standard still
    attached."""
    league = await _league(tmp_path, results=False)

    asked = await _detach(league)

    assert "not enabled" in _replied(asked)
    asked.response.defer.assert_not_awaited()
    assert await change_rows(league.db_path) == []
    assert await _rows(league.db_path, LINKS) == [(CONFIG,)]


# ---------------------------------------------------------------------------
# /results amend fl and fl-plimit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,value,stored,phrase,logged",
    [
        ("amend_fl", 2, (2, 10), "FL bonus → 2 pts", "fl_bonus: 2"),
        ("amend_fl_plimit", 8, (1, 8), "FL position limit → top 8", "fl_position_limit: 8"),
    ],
)
async def test_the_modification_store_is_written(tmp_path, command, value, stored, phrase,
                                                 logged):
    """A running season in amendment mode, its store holding a bonus of 1 point for the top 10:
    once the change has run the store holds the new value beside the other, and the season's own
    fastest lap is untouched."""
    league = await _league(tmp_path, season="ACTIVE", amending=True)

    asked = await _fl(league, command, value)

    assert await _rows(league.db_path, STAGED_FL) == [stored]
    assert await _rows(
        league.db_path, "SELECT fl_points, fl_position_limit FROM season_points_fl"
    ) == [(1, 10)]
    assert "Updated in modification store" in _replied(asked)
    assert phrase in _replied(asked)
    assert logged in await _logged(league)


@pytest.mark.parametrize("command", ["amend_fl", "amend_fl_plimit"])
async def test_an_amend_outside_amendment_mode_is_refused(tmp_path, command):
    """Writing a store nothing will read would report a change that never reaches the
    league. Refused at once, nothing queued, and the log records the refusal and its reason."""
    league = await _league(tmp_path, season="ACTIVE")
    name = {"amend_fl": "results amend fl", "amend_fl_plimit": "results amend fl-plimit"}[command]

    asked = await _fl(league, command, 2)

    assert "Amendment mode is not active" in _replied(asked)
    assert await change_rows(league.db_path) == []
    assert await log_lines(league) == [
        f"⛔ `/{name}` refused for {NAMED} — Amendment mode is not active."
    ]


@pytest.mark.parametrize("command", ["amend_fl", "amend_fl_plimit"])
async def test_an_amend_with_no_season_is_refused(tmp_path, command):
    """No season: refused at once in the shared gate's words, nothing queued."""
    league = await _league(tmp_path, season=None)

    asked = await _fl(league, command, 2)

    # The shared gate's wording (issue #224): the branch a server whose only season is
    # completed or cancelled reaches too, an archived one never being returned.
    assert "there is none" in _replied(asked)
    assert "archive" in _replied(asked)
    assert await change_rows(league.db_path) == []


@pytest.mark.parametrize("command", ["amend_fl", "amend_fl_plimit"])
async def test_an_amend_is_refused_while_the_module_is_off(tmp_path, command):
    """Results off, the season amending: refused at once, nothing queued, the store as it
    was."""
    league = await _league(tmp_path, results=False, season="ACTIVE", amending=True)

    asked = await _fl(league, command, 2)

    assert "not enabled" in _replied(asked)
    assert await change_rows(league.db_path) == []
    assert await _rows(league.db_path, STAGED_FL) == [(1, 10)]
