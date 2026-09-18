"""`/module enable` and `/module disable` — the dependencies between the bot's modules.

Issue #208. `module_cog.py` sat at 31.9%. It is the command that decides which of the bot's
five modules a league is running, and the rules it enforces are the ones that keep the modules
from being switched on in an order that cannot work.

**The dependencies are the substance.** Attendance requires Results & Standings, because
attendance points are distributed after post-race penalties are approved — with Results off
there is no approval to hang them on. Each guard is tested with its own refusal, and the refusal
has to name what is missing rather than merely refusing: a manager reading "cannot be enabled"
has nowhere to go.

**Enabling Results or Attendance is refused once a season's placements are confirmed** (FR-003). Both change
how a round is scored, and turning one on mid-season would score the remaining rounds by
different rules than the ones already run — the standings would then be a mixture nobody could
reproduce.

**Every enable and disable writes an audit entry.** No module is enabled once a season's
placements are confirmed (issue #220); that gate stands in front of every handler here and is
tested in `test_module_stage_gates.py`, so weather no longer catches up on a running season.

Enabling something already enabled is a warning rather than an error, and does no work — a
manager running the command twice must not get two audit entries or a second set of jobs.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.module_cog import ModuleCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 9608
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "modules.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


def _division(div_id: int, name: str, *, forecast_channel_id: int | None = 5000):
    return SimpleNamespace(
        id=div_id, name=name, tier=1, forecast_channel_id=forecast_channel_id
    )


def _make_cog(
    db_path: str,
    *,
    weather_enabled: bool = False,
    results_enabled: bool = False,
    attendance_enabled: bool = False,
    season=None,
    divisions=None,
) -> ModuleCog:
    bot = MagicMock()
    bot.db_path = db_path

    bot.module_service = MagicMock()
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.module_service.set_weather_enabled = AsyncMock(return_value=None)

    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division(11, "Division 1")]
    )
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])

    bot.scheduler_service = MagicMock()
    bot.scheduler_service.cancel_all_weather_for_server = AsyncMock(return_value=None)

    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
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


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, new_value, old_value FROM audit_entries "
            "WHERE server_id = ? ORDER BY id",
            (SERVER_ID,),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def _weather_flag(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT weather_module_enabled FROM server_configs WHERE server_id = ?",
            (SERVER_ID,),
        )
        return (await cursor.fetchone())["weather_module_enabled"]


def _season():
    return SimpleNamespace(id=1, season_number=1)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------


async def test_weather_is_enabled(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._enable_weather(interaction, SERVER_ID)

    assert await _weather_flag(db_path) == 1
    assert "enabled" in _replied(interaction)


async def test_enabling_weather_twice_does_no_work(tmp_path):
    """A manager running the command again must not get a second audit entry or a second
    set of scheduled jobs."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, weather_enabled=True)
    interaction = _interaction()

    await cog._enable_weather(interaction, SERVER_ID)

    assert "already enabled" in _replied(interaction)
    assert await _audit(db_path) == []


async def test_weather_may_be_enabled_between_seasons(tmp_path):
    """With no active season there are no divisions to check, and a league setting the bot
    up before its first season must not be blocked."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=None)

    await cog._enable_weather(_interaction(), SERVER_ID)

    assert await _weather_flag(db_path) == 1


async def test_enabling_weather_is_audited(tmp_path):
    db_path = await _make_db(tmp_path)

    await _make_cog(db_path)._enable_weather(_interaction(), SERVER_ID)

    entry = (await _audit(db_path))[0]
    assert entry["change_type"] == "MODULE_ENABLE"
    assert json.loads(entry["new_value"])["module"] == "weather"


async def test_disabling_weather_cancels_every_scheduled_job(tmp_path):
    """Leaving them armed would post forecasts for a switched-off module."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, weather_enabled=True)
    interaction = _interaction()

    await cog._disable_weather(interaction, SERVER_ID)

    cog.bot.scheduler_service.cancel_all_weather_for_server.assert_awaited_once()
    cog.bot.module_service.set_weather_enabled.assert_awaited_once_with(SERVER_ID, False)
    assert "cancelled" in _replied(interaction)


async def test_disabling_weather_twice_does_no_work(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, weather_enabled=False)
    interaction = _interaction()

    await cog._disable_weather(interaction, SERVER_ID)

    assert "already disabled" in _replied(interaction)
    cog.bot.scheduler_service.cancel_all_weather_for_server.assert_not_awaited()
    assert await _audit(db_path) == []


async def test_disabling_weather_is_audited(tmp_path):
    db_path = await _make_db(tmp_path)

    await _make_cog(db_path, weather_enabled=True)._disable_weather(
        _interaction(), SERVER_ID
    )

    entry = (await _audit(db_path))[0]
    assert entry["change_type"] == "MODULE_DISABLE"
    assert json.loads(entry["old_value"])["module"] == "weather"


# ---------------------------------------------------------------------------
# Results & Standings
# ---------------------------------------------------------------------------


async def test_results_is_enabled(tmp_path):
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _make_cog(db_path)._enable_results(interaction, SERVER_ID)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT module_enabled FROM results_module_config WHERE server_id = ?",
            (SERVER_ID,),
        )
        assert (await cursor.fetchone())["module_enabled"] == 1


async def test_results_cannot_be_enabled_mid_season(tmp_path):
    """FR-003. It changes how a round is scored, so the remaining rounds would be scored
    by different rules than the ones already run."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=_season())
    interaction = _interaction()

    await cog._enable_results(interaction, SERVER_ID)

    assert "once a season's placements are confirmed" in _replied(interaction)
    assert await _audit(db_path) == []


async def test_enabling_results_twice_does_no_work(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, results_enabled=True)
    interaction = _interaction()

    await cog._enable_results(interaction, SERVER_ID)

    assert "already enabled" in _replied(interaction)
    assert await _audit(db_path) == []


# ---------------------------------------------------------------------------
# Attendance — the dependency
# ---------------------------------------------------------------------------


async def test_attendance_requires_results_first(tmp_path):
    """Attendance points are distributed after post-race penalties are approved; with
    Results off there is no approval to hang them on."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, results_enabled=False)
    interaction = _interaction()

    await cog._enable_attendance(interaction, SERVER_ID)

    assert "requires the Results & Standings module" in _replied(interaction)
    assert await _audit(db_path) == []


async def test_attendance_is_enabled_once_results_is(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, results_enabled=True)
    interaction = _interaction()

    await cog._enable_attendance(interaction, SERVER_ID)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT module_enabled FROM attendance_config",
        )
        assert (await cursor.fetchone())["module_enabled"] == 1


async def test_enabling_attendance_writes_the_packaged_defaults(tmp_path):
    """The league can drive the module the moment it is on, without a setup command that
    does not exist. Both sanction thresholds start unset — a league opts into them."""
    db_path = await _make_db(tmp_path)

    await _make_cog(db_path, results_enabled=True)._enable_attendance(
        _interaction(), SERVER_ID
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours, "
            "autoreserve_threshold, autosack_threshold FROM attendance_config",
        )
        row = await cursor.fetchone()
    assert (row["rsvp_notice_days"], row["rsvp_last_notice_hours"], row["rsvp_deadline_hours"]) == (5, 24, 2)
    assert row["autoreserve_threshold"] is None
    assert row["autosack_threshold"] is None


async def test_attendance_cannot_be_enabled_mid_season(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, results_enabled=True, season=_season())
    interaction = _interaction()

    await cog._enable_attendance(interaction, SERVER_ID)

    assert "once a season's placements are confirmed" in _replied(interaction)


async def test_the_results_dependency_is_checked_before_the_season(tmp_path):
    """A league with neither in place is told about the dependency, which is the thing
    they have to fix first — being told about the season would send them the wrong way."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, results_enabled=False, season=_season())
    interaction = _interaction()

    await cog._enable_attendance(interaction, SERVER_ID)

    assert "requires the Results & Standings module" in _replied(interaction)


async def test_enabling_attendance_twice_does_no_work(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, results_enabled=True, attendance_enabled=True)
    interaction = _interaction()

    await cog._enable_attendance(interaction, SERVER_ID)

    assert "already enabled" in _replied(interaction)
    assert await _audit(db_path) == []


async def test_enabling_attendance_is_audited(tmp_path):
    db_path = await _make_db(tmp_path)

    await _make_cog(db_path, results_enabled=True)._enable_attendance(
        _interaction(), SERVER_ID
    )

    assert (await _audit(db_path))[0]["change_type"] == "ATTENDANCE_MODULE_ENABLED"
