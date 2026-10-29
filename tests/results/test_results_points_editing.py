"""Editing a points table, and the warning a broken one earns.

Issue #208, continuing `tests/results/test_results_config_commands.py`. Where that file covered
creating and destroying a configuration, this one covers filling it in: the per-position points,
the fastest-lap bonus and its eligibility limit, and attaching a configuration to a season.

**A points edit warns rather than refuses** (decided 2026-09-14), and the warning has a job
beyond naming the fault. The edit has *already been applied* by the time the notice is written,
so the notice has to say who will refuse later — the season's approval, or the amendment's —
otherwise a manager reads it as a note they can pass over. `_ordering_notice` returns an empty
string when nothing is wrong, so callers concatenate it unconditionally; that is why
`test_a_table_in_order_earns_no_notice` matters as much as its opposite. Since #439 (slice 7) the
notice is the points configuration service's, `ordering_notice`, so the settings' replies can form
it once their change has run.

**The rule the notice enforces is that a lower position cannot be worth as much as the one
above it.** A table that breaks it would award a driver more for finishing behind somebody,
which is not a scoring system a championship can be run on.

**The fastest-lap commands refuse qualifying outright.** There is no fastest lap to award in a
qualifying session, and the refusal is distinct from "config not found" — a manager who typed
the wrong session needs to be told which of the two went wrong. That refusal, like every refusal
of these commands, is pinned with its log line in `test_results_config_refusals.py`.

**Attaching is refused outside SETUP.** The attachment decides how a whole season is scored, so
changing it mid-season would rescore the rounds already run by different rules than the ones
they were run under. The refusal for a configuration that does not exist says so separately, and
names the command that creates one — a manager attaching a mistyped name would otherwise think
the season was configured when it was not.

**Each command is a settings change on the change queue** (#439, slice 7): a refusal the state
when asked gives is answered at once, nothing queued; anything else is saved when the change runs,
and its reply and line follow. The command tests drive the real commands past their tier guards,
on a league built by the migrations with the real services and queue, and run the queue.
"""
from __future__ import annotations

from typing import Any

from discord import app_commands

from leaguebot.core.db.database import get_connection
from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.results.models.points_config import SessionType
from tests.support import saves
from tests.support.change_queue import change_rows, run_queue
from tests.support.settings_league import interaction, last_said, log_lines, said, settings_league
from tests.support.undecorate import undecorate

SEASON_ID = 3
CONFIG = "100%"


def _notice() -> tuple[Any, str, str]:
    """`ordering_notice` and the two consequences it names, where the service keeps them."""
    from leaguebot.results.services.points_config_service import (
        BLOCKS_AMENDMENT,
        BLOCKS_APPROVAL,
        ordering_notice,
    )
    return ordering_notice, BLOCKS_APPROVAL, BLOCKS_AMENDMENT


# ---------------------------------------------------------------------------
# The ordering notice
# ---------------------------------------------------------------------------


def test_a_table_in_order_earns_no_notice():
    """Callers concatenate this unconditionally, so it has to be empty rather than a
    cheerful sentence — every successful edit would otherwise carry one."""
    ordering_notice, _, _ = _notice()
    assert ordering_notice(CONFIG, "Feature Race", []) == ""


def test_a_broken_table_names_the_configuration_and_the_session():
    """A league can hold several configurations and each has four tables; a notice naming
    neither leaves a manager checking all of them."""
    ordering_notice, _, _ = _notice()
    notice = ordering_notice(CONFIG, "Feature Race", ["P5 (10) ≥ P4 (10)"])

    assert CONFIG in notice
    assert "Feature Race" in notice


def test_the_notice_lists_every_violation():
    """One fix often reveals the next, and a manager working from a partial list would
    come back to the same command repeatedly."""
    ordering_notice, _, _ = _notice()
    notice = ordering_notice(
        CONFIG, "Feature Race", ["P5 (10) ≥ P4 (10)", "P8 (4) ≥ P7 (3)"]
    )

    assert "P5" in notice
    assert "P8" in notice


def test_the_notice_states_the_rule_that_was_broken():
    """Naming the positions is not enough — a manager has to know *why* those two are a
    problem before they can decide which to change."""
    ordering_notice, _, _ = _notice()
    notice = ordering_notice(CONFIG, "Feature Race", ["P5 (10) ≥ P4 (10)"])

    assert "cannot be worth as much as the one above it" in notice


def test_the_notice_says_the_change_was_saved_anyway():
    """The edit has already been applied. A warning that read like a refusal would have a
    manager re-running a command that had already worked, and by default it says what this will
    cost at approval."""
    ordering_notice, _, _ = _notice()
    notice = ordering_notice(CONFIG, "Feature Race", ["P5 (10) ≥ P4 (10)"])

    assert "has been saved" in notice
    assert "season cannot be approved" in notice


def test_the_notice_says_which_refusal_is_coming():
    """Its whole job beyond naming the fault. The two consequences differ, and a manager
    mid-amendment is not blocked by the season's approval."""
    ordering_notice, blocks_approval, blocks_amendment = _notice()
    approval = ordering_notice(CONFIG, "Feature Race", ["x"], blocks_approval)
    amendment = ordering_notice(CONFIG, "Feature Race", ["x"], blocks_amendment)

    assert "season cannot be approved" in approval
    assert "amendment cannot be approved" in amendment
    assert approval != amendment


# ---------------------------------------------------------------------------
# Fixtures for the commands
# ---------------------------------------------------------------------------


async def _league(tmp_path: Any, *, results: bool = True, season: str | None = "SETUP") -> Any:
    """A league with results on (*results*), holding CONFIG with an empty table, and a season
    in *season*'s status (None: no season)."""
    league = await settings_league(tmp_path, modules=("results",) if results else ())
    await saves.create_config(league.db_path, CONFIG)
    if season is not None:
        stage = "CONFIGURATION" if season == "SETUP" else "ONGOING"
        async with get_connection(league.db_path) as db:
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status, stage) "
                "VALUES (?, 1, '2026-01-01', ?, ?)",
                (SEASON_ID, season, stage),
            )
            await db.commit()
    return league


async def _run(league: Any, command: str, method: str, *args: Any) -> Any:
    """Run `/results config <command>` with *args* as Admin, then the queue."""
    asked = interaction(league.bot, f"results config {command}")
    await undecorate(getattr(ResultsCog, method))(ResultsCog(league.bot), asked, *args)
    await run_queue(league.bot)
    return asked


async def _points(db_path: str, position: int) -> int | None:
    """The points CONFIG's Feature Race table holds for *position*."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT points FROM points_config_entries AS e JOIN points_config_store AS s "
            "ON s.id = e.config_id WHERE s.config_name = ? AND e.session_type = ? "
            "AND e.position = ?",
            (CONFIG, SessionType.FEATURE_RACE.value, position),
        )
        row = await cursor.fetchone()
    return None if row is None else row[0]


def _replied(asked) -> str:
    return "\n".join(said(asked))


def _choice(session: SessionType, label: str) -> Any:
    return app_commands.Choice(name=label, value=session.value)


RACE = _choice(SessionType.FEATURE_RACE, "Feature Race")


# ---------------------------------------------------------------------------
# /results config session
# ---------------------------------------------------------------------------


async def _session(league, *, position: int = 1, points: int = 25):
    return await _run(league, "session", "config_session", CONFIG, RACE, position, points)


async def test_a_position_s_points_are_set(tmp_path):
    """Admin sets Feature Race P1 to 25 in 100%: acknowledged at once, and once the change has
    run the table holds it and the reply names the position."""
    league = await _league(tmp_path)

    asked = await _session(league)

    assert said(asked)[0].startswith("⏳ ")
    assert await _points(league.db_path, 1) == 25
    assert "position 1" in last_said(asked)


async def test_an_edit_that_breaks_the_order_is_saved_with_a_warning(tmp_path):
    """The decision this file turns on: the edit is applied and the manager warned, rather
    than the command refusing and leaving them unable to build the table in any order but
    descending. P4 holds 10; Admin sets P5 to 10."""
    league = await _league(tmp_path)
    await saves.set_session_points(league.db_path, CONFIG, SessionType.FEATURE_RACE, 4, 10)

    asked = await _session(league, position=5, points=10)

    assert await _points(league.db_path, 5) == 10
    replied = last_said(asked)
    assert "out of order" in replied
    assert "has been saved" in replied


async def test_an_edit_that_keeps_the_order_carries_no_warning(tmp_path):
    league = await _league(tmp_path)

    asked = await _session(league)

    assert "out of order" not in _replied(asked)


async def test_the_edit_is_logged_with_what_changed(tmp_path):
    league = await _league(tmp_path)

    await _session(league, position=3, points=15)

    (logged,) = await log_lines(league)
    assert "config session" in logged
    assert "position: 3" in logged
    assert "points: 15" in logged


async def test_editing_is_refused_while_the_module_is_disabled(tmp_path):
    league = await _league(tmp_path, results=False)

    asked = await _session(league)

    assert "not enabled" in _replied(asked)
    assert await change_rows(league.db_path) == []
    assert await _points(league.db_path, 1) is None


# ---------------------------------------------------------------------------
# /results config fl and fl-plimit
# ---------------------------------------------------------------------------


async def _fl(league, *, points: int = 2):
    return await _run(league, "fl", "config_fl", CONFIG, RACE, points)


async def _plimit(league, *, limit: int = 8):
    return await _run(league, "fl-plimit", "config_fl_plimit", CONFIG, RACE, limit)


async def _fastest_lap(db_path: str) -> tuple[Any, Any] | None:
    """CONFIG's Feature Race fastest-lap bonus and position limit, where a row holds them."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT f.fl_points, f.fl_position_limit FROM points_config_fl AS f "
            "JOIN points_config_store AS s ON s.id = f.config_id "
            "WHERE s.config_name = ? AND f.session_type = ?",
            (CONFIG, SessionType.FEATURE_RACE.value),
        )
        row = await cursor.fetchone()
    return None if row is None else (row[0], row[1])


async def test_the_fastest_lap_bonus_is_set(tmp_path):
    """Admin sets the Feature Race bonus to 2: once the change has run it is held, and the reply
    names the session."""
    league = await _league(tmp_path)

    asked = await _fl(league, points=2)

    assert (await _fastest_lap(league.db_path))[0] == 2
    assert "Feature Race" in last_said(asked)


async def test_the_eligibility_limit_is_set(tmp_path):
    """Admin limits the Feature Race bonus to the top 8: held once the change has run."""
    league = await _league(tmp_path)

    asked = await _plimit(league, limit=8)

    assert (await _fastest_lap(league.db_path))[1] == 8
    assert "top 8" in last_said(asked)


# ---------------------------------------------------------------------------
# /results config append
# ---------------------------------------------------------------------------


async def _append(league):
    return await _run(league, "append", "config_append", CONFIG)


async def _attached(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name FROM season_points_links WHERE season_id = ?", (SEASON_ID,)
        )
        return [row[0] for row in await cursor.fetchall()]


async def test_a_configuration_is_attached_to_the_season(tmp_path):
    """The season is in setup: once the change has run, 100% is attached and the reply says so."""
    league = await _league(tmp_path)

    asked = await _append(league)

    assert await _attached(league.db_path) == [CONFIG]
    assert "attached" in last_said(asked)


async def test_attaching_outside_setup_is_refused(tmp_path):
    """The attachment decides how a whole season is scored, so changing it mid-season
    would rescore rounds already run by rules they were not run under. The season is active:
    refused at once, nothing attached."""
    league = await _league(tmp_path, season="ACTIVE")

    asked = await _append(league)

    assert "only allowed for seasons in SETUP" in _replied(asked)
    assert await _attached(league.db_path) == []


async def test_a_successful_attachment_is_logged(tmp_path):
    league = await _league(tmp_path)

    await _append(league)

    (logged,) = await log_lines(league)
    assert "config append" in logged


