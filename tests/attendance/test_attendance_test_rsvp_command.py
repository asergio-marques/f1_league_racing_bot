"""`/attendance test rsvp`: opening the modal that sets fake drivers' check-in answers.

Attendance's own test tool, under attendance's own group. It sat under core's `/test-mode` until
#462 moved it, and only its name changed. Issue #208 first covered it there. It is a maintainer
tool that writes a league's real data, which is why the gates in front of it matter more than the
work behind it. The modal it opens has tests of its own, in `test_rsvp_bulk_set_modal.py`.

**It does not run outside test mode, and says so first.** It exists to put a rehearsal into a
state; run against a live season it would overwrite real check-in answers. It is test mode's, a
league admin's like every test mode command, and every test mode command but the toggle is
refused while test mode is off, whatever else is off with it.

**It is gated on the attendance module as well** (issue #114). A check-in call posted while the
module was on leaves its `rsvp_embed_messages` row behind, so without the gate the command finds
that embed and writes answers for a module the league has since switched off — which is a module
producing output while disabled.

**Each refusal says what to do next.** "No RSVP embed for this division" is met by
`/test-mode advance`, not by re-running this command, and a maintainer who is not told that will
retype the same thing.

**The division is matched case-insensitively, in a season being raced only.** A maintainer typing
a name into a slash command is not copying it letter for letter, and a division from a previous
season is not the one whose rehearsal is running.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog, _RsvpBulkSetModal
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.undecorate import undecorate

SERVER_ID = 12409
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
EMBED_CHANNEL = 700
EMBED_MESSAGE = 800


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path, *, name: str = "test_rsvp_cmd", season_status: str = "ACTIVE"
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
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', ?)",
            (SEASON_ID, season_status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


#: `_make_cog`'s default: the division has a call standing.
_UNSET = object()


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
    current_embed=_UNSET,
) -> AttendanceCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(
        return_value=None
        if config_missing
        else SimpleNamespace(test_mode_active=test_mode)
    )
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.attendance_service = MagicMock()
    bot.attendance_service.get_current_embed_message = AsyncMock(
        return_value=_embed_row() if current_embed is _UNSET else current_embed
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = bot
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


async def _set_status(cog, interaction, *, division="Pro"):
    await undecorate(AttendanceCog.test_rsvp)(cog, interaction, division)


# ---------------------------------------------------------------------------
# /attendance test rsvp
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
    cog = _make_cog(db_path, current_embed=None)
    interaction = _interaction()

    await _set_status(cog, interaction)

    replied = _replied(interaction)
    assert "No active RSVP embed" in replied
    assert "/test-mode advance" in replied
    interaction.response.send_modal.assert_not_awaited()


async def test_the_division_s_own_current_call_is_the_one_used(tmp_path):
    """Every division of a round has its own embed; picking the wrong one would write one
    division's answers onto another's board. Which of a division's calls is current, now that
    it can hold more than one (#425), is `get_current_embed_message`'s, and is tested there
    against a real database."""
    db_path = await _make_db(tmp_path, name="rsvp_own_division")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _set_status(cog, interaction)

    cog.bot.attendance_service.get_current_embed_message.assert_awaited_once_with(DIVISION_ID)


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

    # Word for word: the attendance cog's own gate is worded differently.
    assert _replied(interaction) == (
        "❌ The Attendance module is not enabled, so there is no check-in to set."
    )
    interaction.response.send_modal.assert_not_awaited()


@pytest.mark.parametrize("kwargs", [{"test_mode": False}, {"config_missing": True}])
async def test_test_mode_is_checked_before_the_module(tmp_path, kwargs):
    """Every test mode command but the toggle is refused while test mode is off, whatever
    else is off with it — so a league admin outside test mode is told that, not that the
    attendance module is off."""
    db_path = await _make_db(tmp_path, name="rsvp_both_off")
    cog = _make_cog(db_path, attendance_enabled=False, **kwargs)
    interaction = _interaction()

    await _set_status(cog, interaction)

    assert _replied(interaction) == "ℹ️ Test mode is not active."
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
