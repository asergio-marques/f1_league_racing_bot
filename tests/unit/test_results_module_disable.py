"""Switching Results & Standings off — the most destructive toggle in the bot.

Issue #208. Disabling this module mid-season deletes that season's results entire and closes
every round still waiting on them. Two issues were filed against it being silent about that, and
both fixes live here.

**The cascade used to happen unannounced** (the silent half of issue #114): the reply named
results alone, and the league was told nothing about attendance going with it, its check-in and
attendance channels being cleared, or its being unable to come back while the season runs.

**A running season was silent for longer** (issue #167). Disabling mid-season destroys the
season's results, and a league with attendance *already off* was shown no warning at all before
it happened — the confirmation only appeared when there was a cascade to announce. It is now
owed to a running season in its own right, whatever attendance is doing.
`test_a_running_season_is_warned_even_with_attendance_already_off` is the regression test for
exactly that, and it is the case a reader re-coupling the two conditions would lose.

**The warning is built from what is actually at stake.** The two costs are independent: a league
can face a running season, a cascade, both, or — between seasons with attendance off — neither.
A fixed warning would either overstate or understate, and one that overstates is one nobody
reads.

**The order of the disable is load-bearing**, and the method's own docstring says why. The flag
goes down first, so nothing the erasure disturbs can post again on its way out; the season's
results are deleted next; and the rounds still awaiting results are closed *last*, because
closing the last of them finishes its division, and a division finishing is what lets
`/season complete` run. `test_the_flag_goes_down_before_the_results_are_purged` holds the first
half of that ordering.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.module_cog import (  # noqa: E402
    ModuleCog,
    _ConfirmDisableResultsView,
    _results_disable_warning,
)
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 12408
ACTOR_ID = 77
OTHER_ADMIN = 88


# ---------------------------------------------------------------------------
# The warning
# ---------------------------------------------------------------------------


def test_a_running_season_is_warned_about_its_results():
    warning = _results_disable_warning(season_active=True, attendance=False)

    assert "destroys this season's results" in warning
    assert "None of this can be undone" in warning


def test_the_season_warning_lists_everything_that_goes():
    """A manager weighing this needs the whole cost, not a summary — every one of these is
    something they might not have thought of."""
    warning = _results_disable_warning(season_active=True, attendance=False)

    for cost in (
        "every classification recorded this season is deleted",
        "removed from its channel",
        "closed as final with no results",
        "no further results are collected",
    ):
        assert cost in warning


def test_the_season_warning_says_what_survives():
    """Just as important: a manager who believed their points configurations were going
    too might not disable a module they needed to."""
    warning = _results_disable_warning(season_active=True, attendance=False)

    assert "verdicts channel" in warning
    assert "points configurations" in warning.lower()


def test_the_season_warning_says_it_cannot_be_switched_back():
    """Not until the season ends — which is the part that turns a reversible-sounding
    toggle into a decision."""
    warning = _results_disable_warning(season_active=True, attendance=False)

    assert "cannot be switched back on until" in warning


def test_the_cascade_is_announced_in_its_own_right():
    """The silent half of issue #114."""
    warning = _results_disable_warning(season_active=False, attendance=True)

    assert "Attendance goes with it" in warning


def test_a_league_facing_both_costs_is_told_both():
    """They are independent, and a manager told only one would be surprised by the other."""
    warning = _results_disable_warning(season_active=True, attendance=True)

    assert "destroys this season's results" in warning
    assert "Attendance goes with it" in warning


def test_a_league_facing_neither_cost_is_warned_about_nothing():
    """Between seasons with attendance off there is nothing at stake, and a warning
    nobody needs is a warning nobody reads."""
    assert _results_disable_warning(season_active=False, attendance=False) == ""


# ---------------------------------------------------------------------------
# Fixtures for the command
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "results_disable.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled) VALUES (?, 1)",
            (1,),
        )
        await db.commit()
    return db_path


def _make_cog(
    db_path: str,
    *,
    results_enabled: bool = True,
    attendance_enabled: bool = False,
    season=None,
) -> ModuleCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.end_rounds_awaiting_results = AsyncMock(return_value=[])
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


def _interaction(user_id: int = ACTOR_ID):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "Admin#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _view_of(interaction):
    for call in interaction.response.send_message.await_args_list:
        if "view" in call.kwargs:
            return call.kwargs["view"]
    return None


def _purge(rounds: int = 0, *, on_call=None):
    async def _run(db_path, server_id, bot):
        if on_call is not None:
            await on_call()
        return {"rounds": rounds, "sessions": rounds * 2, "standings": rounds * 20,
                "messages": rounds * 3}

    return patch("services.results_purge_service.purge_season_results", new=AsyncMock(side_effect=_run))


async def _flag(db_path: str) -> int | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT module_enabled FROM results_module_config WHERE server_id = ?",
            (SERVER_ID,),
        )
        row = await cursor.fetchone()
    return row["module_enabled"] if row else None


async def _audit_types(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type FROM audit_entries WHERE server_id = ? ORDER BY id",
            (SERVER_ID,),
        )
        return [r["change_type"] for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# When the confirmation is owed
# ---------------------------------------------------------------------------


async def test_disabling_twice_does_no_work(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, results_enabled=False)
    interaction = _interaction()

    with _purge():
        await cog._disable_results(interaction, SERVER_ID)

    assert "already disabled" in _replied(interaction)
    assert await _audit_types(db_path) == []


async def test_a_league_with_nothing_at_stake_is_not_asked(tmp_path):
    """Between seasons with attendance off, there is nothing to warn about — and asking
    anyway is how a confirmation stops being read."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=None, attendance_enabled=False)
    interaction = _interaction()

    with _purge():
        await cog._disable_results(interaction, SERVER_ID)

    assert _view_of(interaction) is None
    assert await _flag(db_path) == 0


async def test_a_running_season_is_warned_even_with_attendance_already_off(tmp_path):
    """Issue #167. The confirmation used to appear only where there was a cascade to
    announce, so a league with attendance off lost a season's results with no warning at
    all. A reader re-coupling the two conditions would lose this again."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(
        db_path, season=SimpleNamespace(id=3), attendance_enabled=False
    )
    interaction = _interaction()

    with _purge():
        await cog._disable_results(interaction, SERVER_ID)

    assert _view_of(interaction) is not None
    assert await _flag(db_path) is None  # nothing written yet


async def test_a_cascade_is_warned_about_between_seasons(tmp_path):
    """The other half of the same independence: no season running, but attendance is on."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=None, attendance_enabled=True)
    interaction = _interaction()

    with _purge():
        await cog._disable_results(interaction, SERVER_ID)

    assert _view_of(interaction) is not None


async def test_nothing_is_written_until_the_league_confirms(tmp_path):
    """The warning is shown *before* anything irreversible, so a manager who closes the
    message loses nothing."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=SimpleNamespace(id=3), attendance_enabled=True)

    with _purge(rounds=4) as purge:
        await cog._disable_results(_interaction(), SERVER_ID)

    purge.assert_not_awaited()
    assert await _audit_types(db_path) == []


# ---------------------------------------------------------------------------
# Applying the disable
# ---------------------------------------------------------------------------


async def test_the_flag_goes_down_before_the_results_are_purged(tmp_path):
    """So nothing the erasure disturbs can post again on its way out — the module-output
    rule, applied to the act of switching the module off."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    seen: list[int | None] = []

    async def _observe():
        seen.append(await _flag(db_path))

    with _purge(rounds=1, on_call=_observe):
        await cog._apply_results_disable(
            _interaction(), SERVER_ID, cascade_attendance=False
        )

    assert seen == [0]


async def test_the_rounds_are_closed_after_the_purge(tmp_path):
    """Closing the last of them finishes its division, and a division finishing is what
    lets `/season complete` run — so it has to come after the results are gone."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    order: list[str] = []

    async def _observe():
        order.append("purge")

    cog.bot.season_service.end_rounds_awaiting_results = AsyncMock(
        side_effect=lambda *a: order.append("close") or []
    )

    with _purge(rounds=1, on_call=_observe):
        await cog._apply_results_disable(
            _interaction(), SERVER_ID, cascade_attendance=False
        )

    assert order == ["purge", "close"]


async def test_a_disable_between_seasons_still_takes_all_three_steps(tmp_path):
    """The last two simply find nothing to do, which is why they are unconditional."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    with _purge(rounds=0) as purge:
        await cog._apply_results_disable(
            _interaction(), SERVER_ID, cascade_attendance=False
        )

    purge.assert_awaited_once()
    cog.bot.season_service.end_rounds_awaiting_results.assert_awaited_once()
    assert await _flag(db_path) == 0


async def test_a_purged_season_is_audited_separately(tmp_path):
    """The disable and the destruction are two different facts, and a league reading its
    log needs to see what was deleted as well as that the module went."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    with _purge(rounds=4):
        await cog._apply_results_disable(
            _interaction(), SERVER_ID, cascade_attendance=False
        )

    types = await _audit_types(db_path)
    assert "MODULE_DISABLE" in types
    assert "RESULTS_SEASON_PURGED" in types


async def test_a_disable_that_destroyed_nothing_writes_no_purge_entry(tmp_path):
    """Nothing was destroyed, so an entry saying so would be a record of an event that did
    not happen."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    with _purge(rounds=0):
        await cog._apply_results_disable(
            _interaction(), SERVER_ID, cascade_attendance=False
        )

    assert "RESULTS_SEASON_PURGED" not in await _audit_types(db_path)


async def test_the_reply_counts_what_was_destroyed(tmp_path):
    """A manager who has just confirmed needs to see the scale of what happened."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    with _purge(rounds=4):
        await cog._apply_results_disable(
            interaction, SERVER_ID, cascade_attendance=False
        )

    replied = _replied(interaction)
    assert "This season's results are gone" in replied
    assert "8 session result(s)" in replied


async def test_the_reply_says_what_survived_the_purge(tmp_path):
    """Verdicts already announced cannot be taken back, and the points configurations are
    kept — both are things a manager would otherwise go looking for."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    with _purge(rounds=4):
        await cog._apply_results_disable(
            interaction, SERVER_ID, cascade_attendance=False
        )

    replied = _replied(interaction)
    assert "Verdicts already announced remain" in replied
    assert "Points configurations and division channels are kept" in replied


async def test_a_disable_between_seasons_reports_no_destruction(tmp_path):
    """There was none, and a count of zeroes would read as something having gone wrong."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    with _purge(rounds=0):
        await cog._apply_results_disable(
            interaction, SERVER_ID, cascade_attendance=False
        )

    assert "results are gone" not in _replied(interaction)


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


async def test_the_cascade_disables_attendance_and_says_so(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, attendance_enabled=True)
    interaction = _interaction()

    with _purge():
        await cog._apply_results_disable(
            interaction, SERVER_ID, cascade_attendance=True
        )

    replied = _replied(interaction)
    assert "Attendance module disabled with it" in replied
    assert "check-in and attendance channels have been cleared" in replied


async def test_the_cascade_says_what_attendance_keeps(tmp_path):
    """Its timings, penalties and thresholds survive — so a league re-enabling it later
    does not have to configure it again."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, attendance_enabled=True)
    interaction = _interaction()

    with _purge():
        await cog._apply_results_disable(
            interaction, SERVER_ID, cascade_attendance=True
        )

    assert "timings, penalties and thresholds are kept" in _replied(interaction)


async def test_no_cascade_where_attendance_is_already_off(tmp_path):
    """Asked for, but there is nothing to cascade to — and claiming otherwise would tell a
    league a module went off that was never on."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, attendance_enabled=False)
    interaction = _interaction()

    with _purge():
        await cog._apply_results_disable(
            interaction, SERVER_ID, cascade_attendance=True
        )

    assert "Attendance module disabled with it" not in _replied(interaction)


# ---------------------------------------------------------------------------
# The confirmation view
# ---------------------------------------------------------------------------


async def test_only_the_admin_who_asked_may_confirm(tmp_path):
    """A second admin pressing another's confirmation would destroy a season's results
    they had not been asked about."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID, SERVER_ID, cascade_attendance=False)
    interaction = _interaction(user_id=OTHER_ADMIN)

    with _purge():
        await type(view).confirm(view, interaction, MagicMock())

    assert await _flag(db_path) is None
