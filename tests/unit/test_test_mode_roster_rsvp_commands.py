"""Two test-mode commands: clearing a rehearsal's drivers, and opening the check-in modal.

Issue #208. `/test-mode roster clear` and `/test-mode rsvp set-status` were uncovered. Both are
maintainer tools that write a league's real data, which is why the gates in front of them matter
more than the work behind them.

**Neither runs outside test mode.** These exist to put a rehearsal into a state; run against a
live season they would delete real drivers or overwrite real check-in answers. The test-mode
flag is the only thing between the two, and it is checked before anything else.

**`rsvp set-status` is gated on the attendance module as well** (issue #114). A check-in call
posted while the module was on leaves its `rsvp_embed_messages` row behind, so without the gate
the command finds that embed and writes answers for a module the league has since switched off —
which is a module producing output while disabled.

**Each refusal says what to do next.** "No RSVP embed for this division" is met by
`/test-mode advance`, not by re-running this command, and a maintainer who is not told that will
retype the same thing.

**Clearing nothing is reported, and not logged.** A division with no fake drivers in it is an
ordinary answer to the command rather than a failure, but nothing happened — and a log line
saying a rehearsal's roster was cleared when it was already empty would misdescribe the state
the databases are in.

**The division is matched case-insensitively, in the active season only.** A maintainer typing a
name into a slash command is not copying it letter for letter, and a division from a previous
season is not the one whose rehearsal is running.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.test_mode_cog import TestModeCog, _RsvpBulkSetModal  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 12408
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
EMBED_CHANNEL = 700
EMBED_MESSAGE = 800


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path, *, name: str = "test_mode_cmds", season_status: str = "ACTIVE"
) -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', ?)",
            (SEASON_ID, SERVER_ID, season_status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


def _embed_row(division_id: int = DIVISION_ID):
    return SimpleNamespace(
        division_id=division_id,
        round_id=ROUND_ID,
        channel_id=str(EMBED_CHANNEL),
        message_id=str(EMBED_MESSAGE),
    )


def _make_cog(
    db_path: str,
    *,
    test_mode: bool = True,
    config_missing: bool = False,
    attendance_enabled: bool = True,
    embed_rows=None,
) -> TestModeCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = MagicMock()
    bot.config_service.get_server_config = AsyncMock(
        return_value=None
        if config_missing
        else SimpleNamespace(test_mode_active=test_mode)
    )
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.attendance_service = MagicMock()
    bot.attendance_service.get_all_embed_messages = AsyncMock(
        return_value=embed_rows if embed_rows is not None else [_embed_row()]
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
    interaction.response.send_modal = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        if call.args
    )


async def _clear(cog, interaction, *, division="Pro", result=0):
    with patch(
        "services.test_roster_service.clear_test_drivers",
        new=AsyncMock(return_value=result),
    ) as clear:
        await undecorate(TestModeCog.roster_clear)(cog, interaction, division)
    return clear


async def _set_status(cog, interaction, *, division="Pro"):
    await undecorate(TestModeCog.rsvp_set_status)(cog, interaction, division)


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

    logged = str(cog.bot.output_router.post_log.await_args.args[1])
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


# ---------------------------------------------------------------------------
# /test-mode rsvp set-status
# ---------------------------------------------------------------------------


async def test_the_modal_is_opened_for_the_divisions_active_embed(tmp_path):
    db_path = await _make_db(tmp_path, name="rsvp_ok")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _set_status(cog, interaction)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, _RsvpBulkSetModal)


async def test_the_modal_carries_the_round_and_embed_it_will_edit(tmp_path):
    """It rebuilds that embed after applying the statuses; the wrong ids would edit another
    division's check-in."""
    db_path = await _make_db(tmp_path, name="rsvp_ids")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _set_status(cog, interaction)

    modal = interaction.response.send_modal.await_args.args[0]
    assert modal._round_id == ROUND_ID
    assert modal._division_id == DIVISION_ID
    assert modal._embed_channel_id == EMBED_CHANNEL
    assert modal._embed_message_id == EMBED_MESSAGE


async def test_the_division_is_matched_regardless_of_case(tmp_path):
    db_path = await _make_db(tmp_path, name="rsvp_case")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _set_status(cog, interaction, division="pRo")

    interaction.response.send_modal.assert_awaited_once()


async def test_an_unknown_division_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, name="rsvp_nodiv")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _set_status(cog, interaction, division="Rookie")

    assert "Rookie" in _replied(interaction)
    assert "not found in a season being raced" in _replied(interaction)
    interaction.response.send_modal.assert_not_awaited()


async def test_a_division_from_another_season_is_not_found(tmp_path):
    """A rehearsal runs against the active season; a division from a previous one is not
    the one whose check-in is open."""
    db_path = await _make_db(tmp_path, name="rsvp_oldseason", season_status="COMPLETED")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _set_status(cog, interaction)

    assert "not found in a season being raced" in _replied(interaction)


async def test_a_division_of_a_season_pending_completion_is_not_found(tmp_path):
    """Issue #220: check-ins belong to the three ongoing stages, not to a season whose
    divisions are all done."""
    db_path = await _make_db(tmp_path, name="rsvp_pending")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET stage = 'PENDING_COMPLETION'")
        await db.commit()
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _set_status(cog, interaction)

    assert "not found in a season being raced" in _replied(interaction)
    interaction.response.send_modal.assert_not_awaited()


async def test_a_division_with_no_open_check_in_says_what_to_run_first(tmp_path):
    """Met by `/test-mode advance`, not by re-running this — and a maintainer who is not
    told that will retype the same thing."""
    db_path = await _make_db(tmp_path, name="rsvp_noembed")
    cog = _make_cog(db_path, embed_rows=[])
    interaction = _interaction()

    await _set_status(cog, interaction)

    replied = _replied(interaction)
    assert "No active RSVP embed" in replied
    assert "/test-mode advance" in replied
    interaction.response.send_modal.assert_not_awaited()


async def test_another_divisions_check_in_is_not_used(tmp_path):
    """Every division of a round has its own embed; picking the wrong one would write one
    division's answers onto another's board."""
    db_path = await _make_db(tmp_path, name="rsvp_otherdiv")
    cog = _make_cog(db_path, embed_rows=[_embed_row(division_id=99)])
    interaction = _interaction()

    await _set_status(cog, interaction)

    assert "No active RSVP embed" in _replied(interaction)


async def test_the_right_embed_is_chosen_from_several(tmp_path):
    db_path = await _make_db(tmp_path, name="rsvp_many")
    other = _embed_row(division_id=99)
    other.round_id = 999
    cog = _make_cog(db_path, embed_rows=[other, _embed_row()])
    interaction = _interaction()

    await _set_status(cog, interaction)

    assert interaction.response.send_modal.await_args.args[0]._round_id == ROUND_ID


@pytest.mark.parametrize("kwargs", [{"test_mode": False}, {"config_missing": True}])
async def test_setting_statuses_outside_test_mode_is_refused(tmp_path, kwargs):
    """Run against a live season this overwrites real drivers' check-in answers."""
    db_path = await _make_db(tmp_path, name="rsvp_notestmode")
    cog = _make_cog(db_path, **kwargs)
    interaction = _interaction()

    await _set_status(cog, interaction)

    assert "Test mode is not active" in _replied(interaction)
    interaction.response.send_modal.assert_not_awaited()


async def test_a_disabled_attendance_module_is_refused(tmp_path):
    """Issue #114. A call posted while the module was on leaves its embed row behind, so
    without this gate the command finds that embed and writes check-in answers for a module
    the league has switched off."""
    db_path = await _make_db(tmp_path, name="rsvp_module_off")
    cog = _make_cog(db_path, attendance_enabled=False)
    interaction = _interaction()

    await _set_status(cog, interaction)

    assert "Attendance module is not enabled" in _replied(interaction)
    interaction.response.send_modal.assert_not_awaited()


async def test_the_module_gate_runs_before_the_division_is_looked_up(tmp_path):
    """So a league with the module off is told the actual reason rather than being sent to
    check a division name that is perfectly correct."""
    db_path = await _make_db(tmp_path, name="rsvp_gate_order")
    cog = _make_cog(db_path, attendance_enabled=False)
    interaction = _interaction()

    await _set_status(cog, interaction, division="Rookie")

    assert "Attendance module is not enabled" in _replied(interaction)
    assert "not found" not in _replied(interaction)


async def test_the_command_does_not_defer(tmp_path):
    """`send_modal` must be an interaction's first response, and a deferred interaction
    cannot open one."""
    db_path = await _make_db(tmp_path, name="rsvp_nodefer")
    cog = _make_cog(db_path)
    interaction = _interaction()
    interaction.response.defer = AsyncMock()

    await _set_status(cog, interaction)

    interaction.response.defer.assert_not_awaited()
