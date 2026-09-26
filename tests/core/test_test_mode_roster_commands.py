"""`/test-mode roster clear`: clearing a rehearsal's fake drivers from a division.

Issue #208. The command was uncovered. It is a maintainer tool that writes a league's real data,
which is why the gates in front of it matter more than the work behind it. Its companion that
opened the check-in modal is attendance's now, `/attendance test rsvp`, and is covered in
`tests/attendance/test_attendance_test_rsvp_command.py`.

**It does not run outside test mode.** It exists to put a rehearsal into a state; run against a
live season it would delete real drivers. The test-mode flag is the only thing between the two,
and it is checked before anything else.

**Clearing nothing is reported, and not logged.** A division with no fake drivers in it is an
ordinary answer to the command rather than a failure, but nothing happened — and a log line
saying a rehearsal's roster was cleared when it was already empty would misdescribe the state
the databases are in.

"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.cogs.test_mode_cog import TestModeCog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.undecorate import undecorate

SERVER_ID = 12408
SEASON_ID = 1
DIVISION_ID = 11


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "test_mode_cmds") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


def _make_cog(
    db_path: str,
    *,
    test_mode: bool = True,
    config_missing: bool = False,
) -> TestModeCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(
        return_value=None
        if config_missing
        else SimpleNamespace(test_mode_active=test_mode)
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = TestModeCog.__new__(TestModeCog)
    cog.bot = bot
    # The stage the roster may change in has tests of its own (test_test_mode_roster_stage).
    cog._refuse_roster_change_outside_placements = AsyncMock(return_value=False)
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Maintainer"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        if call.args
    )


async def _clear(cog, interaction, *, division="Pro", result=0):
    with patch(
        "leaguebot.core.services.test_roster_service.clear_test_drivers",
        new=AsyncMock(return_value=result),
    ) as clear:
        await undecorate(TestModeCog.roster_clear)(cog, interaction, division)
    return clear


# ---------------------------------------------------------------------------
# /test-mode roster clear
# ---------------------------------------------------------------------------


async def test_the_divisions_fake_drivers_are_removed(tmp_path):
    db_path = await _make_db(tmp_path, name="clear_ok")
    cog = _make_cog(db_path)
    interaction = _interaction()

    clear = await _clear(cog, interaction, result=5)

    clear.assert_awaited_once()
    assert clear.await_args.kwargs["division_name"] == "Pro"
    assert "Removed **5**" in _replied(interaction)


async def test_clearing_an_empty_division_says_so(tmp_path):
    """An ordinary answer rather than a failure — a maintainer who cleared it a minute ago
    should not be shown an error for doing it twice."""
    db_path = await _make_db(tmp_path, name="clear_empty")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _clear(cog, interaction, result=0)

    assert "No fake drivers found" in _replied(interaction)


async def test_clearing_nothing_is_not_logged(tmp_path):
    """Nothing happened, and a log line saying a rehearsal's roster was cleared when it was
    already empty would misdescribe the state the databases are in."""
    db_path = await _make_db(tmp_path, name="clear_nolog")
    cog = _make_cog(db_path)

    await _clear(cog, _interaction(), result=0)

    cog.bot.output_router.post_log.assert_not_awaited()


async def test_a_clear_is_logged_with_what_it_removed(tmp_path):
    """Test mode writes a league's real tables, so the log is what distinguishes a
    rehearsal's state from a real one afterwards."""
    db_path = await _make_db(tmp_path, name="clear_log")
    cog = _make_cog(db_path)

    await _clear(cog, _interaction(), result=5)

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "/test-mode roster clear" in logged
    assert "Pro" in logged
    assert "5" in logged


async def test_a_refusal_from_the_service_is_passed_on(tmp_path):
    """It returns a string to explain itself — an unknown division, a season in the wrong
    state — and a maintainer cannot act on a refusal they are not shown."""
    db_path = await _make_db(tmp_path, name="clear_refused")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _clear(cog, interaction, result="Division 'Rookie' not found")

    assert "Division 'Rookie' not found" in _replied(interaction)
    cog.bot.output_router.post_log.assert_not_awaited()


@pytest.mark.parametrize(
    "kwargs", [{"test_mode": False}, {"config_missing": True}]
)
async def test_clearing_outside_test_mode_is_refused(tmp_path, kwargs):
    """Run against a live season this deletes real drivers."""
    db_path = await _make_db(tmp_path, name="clear_notestmode")
    cog = _make_cog(db_path, **kwargs)
    interaction = _interaction()

    clear = await _clear(cog, interaction)

    assert "only available when test mode is enabled" in _replied(interaction)
    clear.assert_not_awaited()
