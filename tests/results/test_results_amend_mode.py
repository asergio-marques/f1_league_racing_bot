"""Amendment mode — editing a running season's points without changing them yet.

Issue #208. A points configuration attached to a season is locked once that season is approved,
because the rounds already run were scored by it. Amendment mode is how a league changes it
anyway: edits go into a *modification store* rather than the live table, and are applied in one
reviewed step or thrown away.

**Disabling is refused while uncommitted changes exist.** The store holds edits nobody has
approved, and turning the mode off would discard them silently — a manager who spent an evening
retyping a points table would lose it to a command they thought was a toggle. The refusal names
both ways out: revert to discard deliberately, or review to apply.
`test_disabling_is_refused_while_changes_are_pending` is the one that matters here.

**Reverting is refused when the mode is not on.** There is no store to revert, and doing nothing
quietly would leave a manager believing they had discarded changes that were never staged.

**Both commands need a season.** The store hangs off one, and the points being amended are that
season's — so a server between seasons has nothing to amend.

The toggle is a single command doing two opposite things, which is why each direction is tested
for its own reply *and* its own log line: a league reading its log needs to see which way the
mode went, and the two entries are written separately.

**Both commands are settings changes on the change queue** (#439, slice 7): a refusal the state
when asked gives is answered at once, nothing queued; anything else is saved when the change runs,
its reply and line following. The tests drive the real commands on a league built by the
migrations, with the real services and queue, and run the queue: a season under way built on the
points configuration `Standard`, 25 for a win and 18 for second, its amendment mode in the state
each test names.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.results.cogs.results_cog import ResultsCog
from tests.support import saves
from tests.support.change_queue import MEMBER_ID, change_rows, run_queue
from tests.support.settings_league import interaction, log_lines, said, settings_league
from tests.support.undecorate import undecorate

SEASON_ID = 3
CONFIG = "Standard"
#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
#: When a change is staged, for the writer that takes it.
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
MODE = "SELECT amendment_active, modified_flag FROM season_amendment_state"
STAGED = "SELECT position, points FROM season_modification_entries ORDER BY position"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _league(tmp_path: Any, *, results: bool = True, season: bool = True,
                  mode: str | None = None) -> Any:
    """A league with results on (*results*); with *season*, a season under way (SEASON_ID) built
    on CONFIG, 25 for a win and 18 for second. *mode* is None (never amended: no store at all),
    "off" (amended once, the mode since turned off), "on" (on, nothing staged) or "staged" (on,
    P1 staged at 26)."""
    league = await settings_league(tmp_path, modules=("results",) if results else ())
    await saves.create_config(league.db_path, CONFIG)
    if season:
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
            for position, points in ((1, 25), (2, 18)):
                await db.execute(
                    "INSERT INTO season_points_entries "
                    "(season_id, config_name, session_type, position, points) "
                    "VALUES (?, ?, 'FEATURE_RACE', ?, ?)",
                    (SEASON_ID, CONFIG, position, points),
                )
            await db.commit()
    if mode is not None:
        await saves.enable_amendment_mode(league.db_path, SEASON_ID)
    if mode == "off":
        await saves.disable_amendment_mode(league.db_path, SEASON_ID)
    if mode == "staged":
        await saves.modify_session_points(
            league.db_path, SEASON_ID, CONFIG, "FEATURE_RACE", [(1, 26)],
            actor_id=MEMBER_ID, actor_name="Admin", now=NOW,
        )
    return league


async def _rows(db_path: str, sql: str) -> list[tuple[Any, ...]]:
    async with get_connection(db_path) as db:
        return [tuple(row) for row in await (await db.execute(sql)).fetchall()]


async def _toggle(league: Any) -> Any:
    """Run `/results amend toggle` as Admin, then the queue."""
    asked = interaction(league.bot, "results amend toggle")
    await undecorate(ResultsCog.amend_toggle)(ResultsCog(league.bot), asked)
    await run_queue(league.bot)
    return asked


async def _revert(league: Any) -> Any:
    """Run `/results amend revert` as Admin, then the queue."""
    asked = interaction(league.bot, "results amend revert")
    await undecorate(ResultsCog.amend_revert)(ResultsCog(league.bot), asked)
    await run_queue(league.bot)
    return asked


def _replied(asked: Any) -> str:
    return "\n".join(said(asked))


async def _logged(league: Any) -> str:
    return "\n".join(await log_lines(league))


# ---------------------------------------------------------------------------
# The module gate and the season
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", [_toggle, _revert], ids=["toggle", "revert"])
async def test_both_commands_are_refused_while_the_module_is_disabled(tmp_path, command):
    """Results off, the season's mode on with P1 staged: refused at once, nothing queued, the
    mode and the store as they were."""
    league = await _league(tmp_path, results=False, mode="staged")

    asked = await command(league)

    assert "not enabled" in _replied(asked)
    assert await change_rows(league.db_path) == []
    assert await _rows(league.db_path, MODE) == [(1, 1)]
    assert await _rows(league.db_path, STAGED) == [(1, 26), (2, 18)]


@pytest.mark.parametrize("command", [_toggle, _revert], ids=["toggle", "revert"])
async def test_both_commands_need_a_season(tmp_path, command):
    """The store hangs off one, and the points being amended are that season's."""
    league = await _league(tmp_path, season=False)

    asked = await command(league)

    # The refusal names the archive rule (issue #224): a server whose only season is
    # completed or cancelled reaches this same branch, because the command now asks for
    # the *live* season and an archived one is never returned.
    assert "there is none" in _replied(asked)
    assert "archive" in _replied(asked)
    assert await change_rows(league.db_path) == []
    assert await _rows(league.db_path, MODE) == []


# ---------------------------------------------------------------------------
# Turning it on
# ---------------------------------------------------------------------------


async def test_amendment_mode_is_enabled_when_it_was_off(tmp_path):
    league = await _league(tmp_path, mode="off")

    asked = await _toggle(league)

    assert await _rows(league.db_path, MODE) == [(1, 0)]
    assert "enabled" in _replied(asked)


async def test_a_season_that_has_never_amended_starts_a_store(tmp_path):
    """No state row at all is the same as off — a league amending for the first time must
    not be refused for never having done it before. The store starts as the season's points."""
    league = await _league(tmp_path)

    await _toggle(league)

    assert await _rows(league.db_path, MODE) == [(1, 0)]
    assert await _rows(league.db_path, STAGED) == [(1, 25), (2, 18)]


async def test_enabling_says_the_store_was_initialised(tmp_path):
    """The store is the thing the manager is about to type into, and a bare "enabled"
    would not tell them where their edits are going."""
    league = await _league(tmp_path, mode="off")

    asked = await _toggle(league)

    assert "Modification store initialised" in _replied(asked)


async def test_enabling_is_logged_as_enabled(tmp_path):
    """The toggle is one command doing two opposite things, so the log has to say which."""
    league = await _league(tmp_path, mode="off")

    await _toggle(league)

    assert "amendment_mode: enabled" in await _logged(league)


# ---------------------------------------------------------------------------
# Turning it off
# ---------------------------------------------------------------------------


async def test_amendment_mode_is_disabled_when_it_was_on(tmp_path):
    league = await _league(tmp_path, mode="on")

    asked = await _toggle(league)

    assert await _rows(league.db_path, MODE) == [(0, 0)]
    assert "disabled" in _replied(asked)


async def test_disabling_is_logged_as_disabled(tmp_path):
    league = await _league(tmp_path, mode="on")

    await _toggle(league)

    assert "amendment_mode: disabled" in await _logged(league)


async def test_disabling_is_refused_while_changes_are_pending(tmp_path):
    """The store holds edits nobody has approved. Turning the mode off would discard them
    silently — a manager who spent an evening retyping a points table would lose it to a
    command they thought was a toggle. Refused at once, nothing queued, the mode still on."""
    league = await _league(tmp_path, mode="staged")

    asked = await _toggle(league)

    assert "uncommitted changes exist" in _replied(asked)
    assert await change_rows(league.db_path) == []
    assert await _rows(league.db_path, MODE) == [(1, 1)]


async def test_the_refusal_names_both_ways_out(tmp_path):
    """Discarding deliberately and applying are genuinely different decisions, and a
    manager blocked here needs to be told they have both."""
    league = await _league(tmp_path, mode="staged")

    asked = await _toggle(league)

    replied = _replied(asked)
    assert "/results amend revert" in replied
    assert "/results amend review" in replied


async def test_a_refused_disable_is_not_logged_as_a_success(tmp_path):
    """The mode is still on, and a log saying otherwise would have a league believe their
    staged edits were gone: the log records the refusal and its reason instead."""
    league = await _league(tmp_path, mode="staged")

    await _toggle(league)

    assert await log_lines(league) == [
        f"⛔ `/results amend toggle` refused for {NAMED} — "
        "Cannot disable amendment mode — uncommitted changes exist. "
        "Use `/results amend revert` to discard or `/results amend review` to apply."
    ]


# ---------------------------------------------------------------------------
# Reverting
# ---------------------------------------------------------------------------


async def test_the_store_is_reverted_to_the_season_s_points(tmp_path):
    """P1 staged at 26: once the change has run the store holds the season's 25 again."""
    league = await _league(tmp_path, mode="staged")

    asked = await _revert(league)

    assert await _rows(league.db_path, STAGED) == [(1, 25), (2, 18)]
    assert "reverted" in _replied(asked)


@pytest.mark.parametrize("mode", ["off", None], ids=["mode-off", "no-store"])
async def test_reverting_is_refused_when_the_mode_is_not_on(tmp_path, mode):
    """There is no store to revert, and doing nothing quietly would leave a manager
    believing they had discarded changes that were never staged. Refused at once, nothing
    queued."""
    league = await _league(tmp_path, mode=mode)

    asked = await _revert(league)

    assert "not active" in _replied(asked)
    assert await change_rows(league.db_path) == []


async def test_a_refused_revert_is_recorded_as_a_refusal(tmp_path):
    """Nothing was reverted, so the log records the refusal and its reason, never a revert."""
    league = await _league(tmp_path, mode="off")

    await _revert(league)

    assert await log_lines(league) == [
        f"⛔ `/results amend revert` refused for {NAMED} — Amendment mode is not active."
    ]


async def test_a_successful_revert_is_logged(tmp_path):
    league = await _league(tmp_path, mode="staged")

    await _revert(league)

    assert "amend revert" in await _logged(league)
