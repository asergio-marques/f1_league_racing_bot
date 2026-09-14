"""Disabling results & standings warns before it takes attendance with it — issue #114.

Attendance depends on results & standings, so `/module disable results` disables attendance
too. The cascade used to be silent: the reply named results alone, and a league could switch
attendance off — losing every division's check-in and attendance channels, and with no way to
switch it back on until the season ended — without ever being told it had happened.

The command now warns first wherever attendance is enabled, writes nothing until the league
confirms, and names both modules in the reply that follows. Where attendance is already off
there is nothing to warn about and the command behaves exactly as it always did.

`core_specification.md` — "Where a module's specification states that another module depends
upon it, disabling it shall disable the dependent module too, and that cascade shall be
reported."

Every test here is `async def`: they construct a `discord.ui.View`, and apt's discord.py 2.5.0
calls `asyncio.get_running_loop()` in `View.__init__` where the pinned 2.7.1 defers it.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from cogs.module_cog import ModuleCog, _ConfirmDisableResultsView  # noqa: E402

SERVER_ID = 6611
ACTOR_ID = 4242


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, attendance_enabled: bool) -> str:
    db_path = os.path.join(str(tmp_path), "cascade.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO results_module_config (server_id, module_enabled) VALUES (?, 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config (server_id, module_enabled) VALUES (?, ?)",
            (SERVER_ID, int(attendance_enabled)),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (1, ?, 1, '2026-01-01', 'ACTIVE')",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Division 1', 1, 555)"
        )
        await db.execute(
            "INSERT INTO attendance_division_config "
            "(division_id, server_id, rsvp_channel_id) VALUES (1, ?, '880011')",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


def _make_cog(db_path: str, *, attendance_enabled: bool) -> ModuleCog:
    cog = ModuleCog.__new__(ModuleCog)
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.output_router.post_log = AsyncMock(return_value=None)
    cog.bot = bot
    return cog


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "admin#0001"  # type: ignore[assignment]
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _module_flags(db_path: str) -> tuple[int, int, int]:
    """Return ``(results_enabled, attendance_enabled, division_config_rows)``."""
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT module_enabled FROM results_module_config WHERE server_id = ?", (SERVER_ID,)
        )
        results = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT module_enabled FROM attendance_config WHERE server_id = ?", (SERVER_ID,)
        )
        attendance = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT COUNT(*) FROM attendance_division_config WHERE server_id = ?", (SERVER_ID,)
        )
        div_rows = (await cur.fetchone())[0]
    return results, attendance, div_rows


# ---------------------------------------------------------------------------
# The warning
# ---------------------------------------------------------------------------


async def test_the_cascade_is_named_before_anything_is_written(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True)
    interaction = _make_interaction()

    await cog._disable_results(interaction, SERVER_ID)

    interaction.response.send_message.assert_awaited()
    warning = interaction.response.send_message.await_args.args[0]
    assert "Attendance" in warning
    assert "check-in" in warning

    assert await _module_flags(db_path) == (1, 1, 1), "the warning wrote something"


async def test_the_warning_carries_a_confirmation(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True)
    interaction = _make_interaction()

    await cog._disable_results(interaction, SERVER_ID)

    view = interaction.response.send_message.await_args.kwargs["view"]
    assert isinstance(view, _ConfirmDisableResultsView)


# ---------------------------------------------------------------------------
# Cancelling and confirming
# ---------------------------------------------------------------------------


async def test_cancelling_leaves_both_modules_enabled(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID, SERVER_ID)
    interaction = _make_interaction()

    await view.cancel.callback(interaction)

    assert await _module_flags(db_path) == (1, 1, 1)
    reply = interaction.response.send_message.await_args.args[0]
    assert "remain enabled" in reply


async def test_confirming_disables_both_and_names_both(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID, SERVER_ID)
    interaction = _make_interaction()

    await view.confirm.callback(interaction)

    results, attendance, div_rows = await _module_flags(db_path)
    assert results == 0
    assert attendance == 0
    assert div_rows == 0, "the cascade left the per-division channels behind"

    reply = interaction.followup.send.await_args.args[0]
    assert "Results & Standings module disabled" in reply
    assert "Attendance module disabled" in reply


async def test_only_the_actor_may_confirm(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID, SERVER_ID)
    interaction = _make_interaction()
    interaction.user.id = ACTOR_ID + 1

    await view.confirm.callback(interaction)

    assert await _module_flags(db_path) == (1, 1, 1)


# ---------------------------------------------------------------------------
# Nothing to warn about
# ---------------------------------------------------------------------------


async def test_no_warning_where_attendance_is_already_off(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=False)
    cog = _make_cog(db_path, attendance_enabled=False)
    interaction = _make_interaction()

    await cog._disable_results(interaction, SERVER_ID)

    interaction.response.send_message.assert_not_awaited()
    interaction.response.defer.assert_awaited()

    results, attendance, _ = await _module_flags(db_path)
    assert results == 0
    assert attendance == 0

    reply = interaction.followup.send.await_args.args[0]
    assert reply == "✅ Results & Standings module disabled."
