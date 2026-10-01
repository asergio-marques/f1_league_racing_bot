"""Disabling results & standings warns before it takes attendance with it — issue #114.

Attendance depends on results & standings, so `/module disable results` disables attendance
too. The cascade used to be silent: the reply named results alone, and a league could switch
attendance off — losing every division's check-in and attendance channels, and with no way to
switch it back on until the season ended — without ever being told it had happened.

The command now warns first wherever attendance is enabled, writes nothing until the league
confirms, and names both modules in the reply that follows. Where attendance is already off
there is nothing to warn about and the command behaves exactly as it always did. Either way the
disable is carried out on the change queue (#439): the manager is told at once that it is under
way, and that reply is updated with what went once it is done.

`core_specification.md` — "Where a module's specification states that another module depends
upon it, disabling it shall disable the dependent module too, and that cascade shall be
reported."

Every test here is `async def`: they construct a `discord.ui.View`, and apt's discord.py 2.5.0
calls `asyncio.get_running_loop()` in `View.__init__` where the pinned 2.7.1 defers it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.cogs.module_cog import ModuleCog, _ConfirmDisableResultsView
from leaguebot.core.services.season_service import SeasonService
from tests.support.change_queue import (
    INTERACTION_CHANNEL_ID,
    LOG_CHANNEL_ID,
    acknowledgement,
    attach_queue,
    league_double,
    member,
    member_interaction,
    queued_log_lines,
    run_queue,
    updated_reply,
)

NOT_BUILT = "#439: turning results off does not run on the change queue yet"

SERVER_ID = 6611
ACTOR_ID = 4242
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ACKNOWLEDGEMENT = (
    "⏳ Turning Results & Standings off. This message will be updated when it is done; "
    "if it takes longer, the log channel will say so."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path, *, attendance_enabled: bool, season_status: str = "SETUP"
) -> str:
    db_path = os.path.join(str(tmp_path), "cascade.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 900, ?, ?)",
            (SERVER_ID, INTERACTION_CHANNEL_ID, LOG_CHANNEL_ID),
        )
        await db.execute(
            "INSERT INTO results_module_config (id, module_enabled) VALUES (?, 1)",
            (1,),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled) VALUES (?, ?)",
            (1, int(attendance_enabled)),
        )
        # SETUP by default, so the cascade is the only thing at stake here. A running season
        # puts its own — far larger — warning in front of the disable, and that is tested in
        # `test_results_disable_purges_the_season.py`.
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', ?)",
            (season_status,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Division 1', 1, 555)"
        )
        await db.execute(
            "INSERT INTO attendance_division_config "
            "(division_id, rsvp_channel_id) VALUES (1, '880011')"
        )
        await db.commit()
    return db_path


def _make_cog(db_path: str, *, attendance_enabled: bool, queued: bool = False) -> ModuleCog:
    """A cog whose router and module flags are doubles; with *queued*, the bot as the builder
    makes it instead: real services, router and change queue on *db_path*, "now" pinned."""
    cog = ModuleCog.__new__(ModuleCog)
    if queued:
        from leaguebot.attendance.services.attendance_service import AttendanceService
        from leaguebot.core.services.module_service import ModuleService

        bot = league_double(db_path)
        bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
        bot.module_service = ModuleService(db_path)
        bot.season_service = SeasonService(db_path)
        bot.attendance_service = AttendanceService(db_path)
        attach_queue(bot, db_path, now=NOW)
        cog.bot = bot
        return cog
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.output_router.post_log = AsyncMock(return_value=None)
    # A real one: the disable asks it whether a season is running, and then to close any round
    # only the results module could have moved (issue #167).
    bot.season_service = SeasonService(db_path)
    bot.get_guild = MagicMock(return_value=None)
    cog.bot = bot
    return cog


def _make_interaction() -> MagicMock:
    """An interaction whose response knows whether it has been used, as Discord's does, so that
    a refusal answers by `response` until the interaction is answered or deferred (#482)."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "admin#0001"  # type: ignore[assignment]
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _log_lines(cog: ModuleCog) -> list[str]:
    """Every line written to the log channel."""
    return [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]


async def _queued_lines(cog: ModuleCog) -> list[str]:
    """Every line a queued cog wrote: those the log channel took, then those waiting on a retry."""
    return list(cog.bot.log_channel.sent) + await queued_log_lines(cog.bot.db_path)


async def _module_flags(db_path: str) -> tuple[int, int, int]:
    """Return ``(results_enabled, attendance_enabled, division_config_rows)``."""
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT module_enabled FROM results_module_config"
        )
        results = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT module_enabled FROM attendance_config"
        )
        attendance = (await cur.fetchone())[0]
        cur = await db.execute(
            "SELECT COUNT(*) FROM attendance_division_config"
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

    await cog._disable_results(interaction)

    interaction.response.send_message.assert_awaited()
    warning = interaction.response.send_message.await_args.args[0]
    assert "Attendance" in warning
    assert "check-in" in warning

    assert await _module_flags(db_path) == (1, 1, 1), "the warning wrote something"


async def test_the_warning_carries_a_confirmation(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True)
    interaction = _make_interaction()

    await cog._disable_results(interaction)

    view = interaction.response.send_message.await_args.kwargs["view"]
    assert isinstance(view, _ConfirmDisableResultsView)


# ---------------------------------------------------------------------------
# Cancelling and confirming
# ---------------------------------------------------------------------------


async def test_cancelling_leaves_both_modules_enabled(tmp_path):
    """Cancel changes nothing, and is recorded with what stands and what to do next beneath it
    (#482)."""
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID)
    interaction = _make_interaction()
    interaction.client = cog.bot

    await view.cancel.callback(interaction)

    assert await _module_flags(db_path) == (1, 1, 1)
    reply = interaction.response.send_message.await_args.args[0]
    assert "remain enabled" in reply
    lines = _log_lines(cog)
    assert len(lines) == 1
    first, *beneath = lines[0].splitlines()
    assert first == f"↩️ `/module disable` cancelled by Admin (<@{ACTOR_ID}>)"
    assert any("Both modules remain enabled." in text for text in beneath)
    assert any("Run `/module disable` again" in text for text in beneath)


async def test_cancelling_a_results_only_disable_is_recorded(tmp_path):
    """With attendance off, Cancel is recorded with results still enabled and nothing
    deleted beneath it, and what to do next (#482)."""
    db_path = await _make_db(tmp_path, attendance_enabled=False)
    cog = _make_cog(db_path, attendance_enabled=False)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID, cascade_attendance=False)
    interaction = _make_interaction()
    interaction.client = cog.bot

    await view.cancel.callback(interaction)

    assert (await _module_flags(db_path))[0] == 1
    lines = _log_lines(cog)
    assert len(lines) == 1
    first, *beneath = lines[0].splitlines()
    assert first == f"↩️ `/module disable` cancelled by Admin (<@{ACTOR_ID}>)"
    assert any(
        "Results & Standings remains enabled and nothing was deleted." in text
        for text in beneath
    )
    assert any("Run `/module disable` again" in text for text in beneath)


async def test_a_confirmation_left_unanswered_is_recorded_as_lapsed(tmp_path):
    """Left unanswered until it times out, the confirmation changes nothing and is recorded as
    lapsed, naming the admin who started it, with what stands and to run the command again
    beneath it (#482)."""
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True)
    guild = MagicMock()
    guild.get_member = MagicMock(
        side_effect=lambda member_id: (
            MagicMock(id=ACTOR_ID, display_name="Admin") if member_id == ACTOR_ID else None
        )
    )
    cog.bot.get_guild = MagicMock(return_value=guild)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID)

    await view.on_timeout()

    assert await _module_flags(db_path) == (1, 1, 1)
    lines = _log_lines(cog)
    assert len(lines) == 1
    first, *beneath = lines[0].splitlines()
    assert first == (
        f"⌛ `/module disable` lapsed unconfirmed (started by Admin (<@{ACTOR_ID}>))"
    )
    assert any("Both modules remain enabled." in text for text in beneath)
    assert any("/module disable" in text for text in beneath)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_confirming_disables_both_and_names_both(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True, queued=True)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID)
    interaction = member_interaction(cog.bot, user=member(ACTOR_ID))

    await view.confirm.callback(interaction)
    await run_queue(cog.bot)

    results, attendance, div_rows = await _module_flags(db_path)
    assert results == 0
    assert attendance == 0
    assert div_rows == 0, "the cascade left the per-division channels behind"

    reply = updated_reply(interaction)
    assert "Results & Standings module disabled" in reply
    assert "Attendance module disabled" in reply


async def test_only_the_actor_may_confirm(tmp_path):
    """Another member's press changes nothing, is refused, and the refusal is recorded (#482)."""
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    cog = _make_cog(db_path, attendance_enabled=True)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID)
    interaction = _make_interaction()
    interaction.client = cog.bot
    interaction.user.id = ACTOR_ID + 1
    interaction.user.display_name = "Other"

    await view.confirm.callback(interaction)

    assert await _module_flags(db_path) == (1, 1, 1)
    interaction.response.send_message.assert_awaited_once_with(
        "⛔ Not your action.", ephemeral=True
    )
    assert _log_lines(cog) == [
        f"⛔ the “✅ Disable both” button refused for Other (<@{ACTOR_ID + 1}>) — "
        "Not your action."
    ]


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_disable_whose_season_cannot_be_wound_down_says_so_in_its_line(
    tmp_path, monkeypatch,
):
    """The disable goes through, and a season that could not be wound down afterwards is
    reported in a line of its own: the wind-down is a change of its own, asked for in the
    switch-off's save, so its fault names it and the switch-off's line carries no "not done"
    (#439)."""
    from leaguebot.core.services import hub_service, season_lifecycle_service

    monkeypatch.setattr(hub_service, "refresh_panel", AsyncMock(return_value=None))
    monkeypatch.setattr(
        season_lifecycle_service, "wind_down_ongoing",
        AsyncMock(side_effect=RuntimeError("the scheduler is down")),
    )
    db_path = await _make_db(tmp_path, attendance_enabled=False, season_status="ACTIVE")
    async with get_connection(db_path) as db:
        # Placements still open, so the season has the wind-down's work left to do.
        await db.execute("UPDATE seasons SET stage = 'ONGOING_PLACEMENTS' WHERE id = 1")
        await db.execute("UPDATE divisions SET status = 'ACTIVE' WHERE id = 1")
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, track_name, scheduled_at, format, "
            "status) VALUES (1, 1, 'Bahrain', '2026-01-01T12:00:00', 'NORMAL', "
            "'AWAITING_RESULTS')"
        )
        await db.commit()
    cog = _make_cog(db_path, attendance_enabled=False, queued=True)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID, cascade_attendance=False)
    interaction = member_interaction(cog.bot, user=member(ACTOR_ID))

    await view.confirm.callback(interaction)
    await run_queue(cog.bot)

    assert (await _module_flags(db_path))[0] == 0
    lines = await _queued_lines(cog)
    switch_off = [line for line in lines if "| /module disable results |" in line]
    assert len(switch_off) == 1
    first, *beneath = switch_off[0].splitlines()
    assert first.startswith(f"Admin (`<@{ACTOR_ID}>`) | /module disable results")
    assert not any(text.startswith("  not done:") for text in beneath)
    assert any(
        line.startswith(
            "❌ Winding the season down after `/module disable results` failed for "
            f"Admin (`<@{ACTOR_ID}>`) — RuntimeError. The details are in the host's log."
        )
        for line in lines
    )


# ---------------------------------------------------------------------------
# Nothing to warn about
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_no_warning_where_neither_attendance_nor_a_season_is_at_stake(tmp_path):
    """With attendance off and no season running, the disable costs the league nothing.

    It is then the cheap command it always was: no confirmation, nothing deleted, and the
    module comes straight back when the league next wants it. The manager is told at once that
    it is under way, and that reply is updated once it is done.
    """
    db_path = await _make_db(tmp_path, attendance_enabled=False)
    cog = _make_cog(db_path, attendance_enabled=False, queued=True)
    interaction = member_interaction(cog.bot, user=member(ACTOR_ID))

    await cog._disable_results(interaction)

    interaction.response.defer.assert_not_awaited()
    assert acknowledgement(interaction) == ACKNOWLEDGEMENT
    assert "view" not in interaction.response.send_message.await_args.kwargs

    await run_queue(cog.bot)

    results, attendance, _ = await _module_flags(db_path)
    assert results == 0
    assert attendance == 0
    assert updated_reply(interaction) == "✅ Results & Standings module disabled."
