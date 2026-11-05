"""What keeps the hub's panel and its permissions current (issue #279).

**The panel follows the modules.** A module's options are offered while it is enabled, so every
switch that changed something asks the change queue to refresh the panel, as the bot's change
(#439, slice 6b): each module's switch asks it as a follow-on once it has run (#283, S11e), as
turning results off always has, and the command refreshes nothing itself. A switch built as a
`Setting` asks it from an `After` step its save plans, an `ACT` step whose result carries the
refresh as its follow-on, so that `setting_change` plans it only where the save changed something
(`test_setting_change.py`) and needs nothing new; a switch-off of its own change type asks it in
its save, as results' does. A toggle refused, at the ask or as it runs, changed nothing and leaves
the panel alone. A refresh the hub refuses is the
queue's to stop on (`test_hub_service.py`), never a line the toggle writes.

**The permissions follow the roles.** The hub is seen by the base role (or everyone, where none
is set) and by both tier roles, so changing any of the three sets them again. The driver role
and the channel settings name nobody the hub cares about.

**A restart recovers the hub.** Its buttons are routed again and its panel's refresh asked of the
change queue, handed to the recovery by the start-up.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

import leaguebot.core.cogs.bot_cog as bot_cog
from leaguebot.core.cogs.bot_cog import BotCog
from leaguebot.core.cogs.module_cog import ModuleCog, _ConfirmDisableResultsView
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services import hub_service
from leaguebot.core.services.config_service import ConfigService
from tests.support.undecorate import undecorate

SERVER_ID = 27903


def _choice(name: str) -> app_commands.Choice:
    return app_commands.Choice(name=name, value=name)


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 77
    interaction.user.display_name = "Admin"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


HUB = 7930


@pytest.fixture
async def queued_bot(tmp_path):
    """The league's bot (`league_bot`) with a real change queue, its hub set to channel 7930, every
    module off and no season, its scheduler recording the removal of weather's jobs."""
    from datetime import datetime, timezone

    from tests.support.change_queue import league_bot, league_queue, seed_server
    from tests.support.settings_league import scheduler_double

    db_path = str(tmp_path / "hub_toggle.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET hub_channel_id = ?", (HUB,))
        await db.commit()
    bot = await league_bot(db_path, scheduler=scheduler_double())
    league_queue(bot, db_path, now=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc))
    return bot


async def _refreshes_asked(bot) -> list[dict]:
    """The payload of every hub refresh asked of the queue, in order."""
    import json

    from tests.support.change_queue import change_rows

    return [json.loads(row["payload"]) for row in await change_rows(bot.db_path)
            if row["kind"] == hub_service.HUB_REFRESH]


# ── The panel follows the modules ─────────────────────────────────────────


#: Each module's switch asked by the command, and the modules on beforehand so that it changes
#: something: attendance needs results on to come on, and results is turned off with attendance off
#: and no season, so that it is asked at once without a warning.
_ON_BEFORE = {
    ("weather", "enable"): (), ("weather", "disable"): ("weather",),
    ("results", "enable"): (), ("results", "disable"): ("results",),
    ("attendance", "enable"): ("results",), ("attendance", "disable"): ("results", "attendance"),
    ("images", "enable"): (), ("images", "disable"): ("images",),
    ("signup", "enable"): (), ("signup", "disable"): ("signup",),
}

#: Why a toggle other than turning results off fails until the build: the command asks its own
#: refresh as soon as it has asked the switch, whatever becomes of it, where the switch's `After`
#: step is to ask it once the save has changed something.
_REFRESHED_BY_THE_COMMAND = "#283: the command asks the refresh, not the switch's follow-on"


def _module_cog(bot) -> ModuleCog:
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


@pytest.mark.parametrize(
    ("module", "command"),
    [
        pytest.param(
            module, command, id=f"{module}-{command}",
            marks=() if (module, command) == ("results", "disable") else pytest.mark.xfail(
                strict=True, reason=_REFRESHED_BY_THE_COMMAND
            ),
        )
        for module in ("weather", "results", "attendance", "images", "signup")
        for command in ("enable", "disable")
    ],
)
async def test_every_toggle_asks_the_queue_to_refresh_the_panel(
    monkeypatch, queued_bot, command, module,
):
    """Every switch, turning results off included. The hub is set and the module stands so that
    the switch changes something; the manager runs `/module <command> <module>`: no refresh is
    asked while the switch waits on the queue, and the panel is not refreshed by the command; once
    the queue has run the switch, one refresh has been asked of the queue, naming the command."""
    from tests.support.change_queue import run_queue
    from tests.support.season_league import set_module_flag
    from tests.support.settings_league import interaction

    for on in _ON_BEFORE[(module, command)]:
        await set_module_flag(queued_bot.db_path, on, True)
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr(hub_service, "refresh_panel", refresh, raising=False)
    cog = _module_cog(queued_bot)

    await undecorate(getattr(ModuleCog, command))(
        cog, interaction(queued_bot, f"module {command}"), _choice(module)
    )
    assert await _refreshes_asked(queued_bot) == []
    refresh.assert_not_awaited()
    await run_queue(queued_bot)

    [asked] = await _refreshes_asked(queued_bot)
    assert f"/module {command}" in asked["command"]


async def _confirm_a_season(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-09-17', 'ACTIVE', 5)"
        )
        await db.commit()


@pytest.mark.parametrize(
    "refused",
    [
        pytest.param("by-the-stage", id="by-the-stage"),
        pytest.param("already-on", id="already-on", marks=pytest.mark.xfail(
            strict=True, reason=_REFRESHED_BY_THE_COMMAND)),
        pytest.param("as-it-runs", id="as-it-runs", marks=pytest.mark.xfail(
            strict=True, reason=_REFRESHED_BY_THE_COMMAND)),
    ],
)
async def test_a_refused_toggle_leaves_the_panel_alone(queued_bot, refused):
    """The manager runs `/module enable weather`, and it is refused: by the stage check, a season's
    placements being confirmed (*by-the-stage*); by the switch's own check at the ask, weather
    being on already (*already-on*); or by the switch's check as it starts, a season's placements
    confirmed while it waited on the queue (*as-it-runs*). It changed nothing, so no refresh is
    asked of the queue, before the queue runs or after."""
    from tests.support.change_queue import run_queue
    from tests.support.season_league import set_module_flag
    from tests.support.settings_league import interaction

    if refused == "by-the-stage":
        await _confirm_a_season(queued_bot.db_path)
    if refused == "already-on":
        await set_module_flag(queued_bot.db_path, "weather", True)
    cog = _module_cog(queued_bot)

    await undecorate(ModuleCog.enable)(
        cog, interaction(queued_bot, "module enable"), _choice("weather")
    )
    assert await _refreshes_asked(queued_bot) == []
    if refused == "as-it-runs":
        await _confirm_a_season(queued_bot.db_path)
    await run_queue(queued_bot)

    assert await _refreshes_asked(queued_bot) == []
    assert await queued_bot.module_service.is_weather_enabled() is (refused == "already-on")


async def _queued_results_off(tmp_path) -> tuple[ModuleCog, MagicMock]:
    """A module cog on the league's bot (`league_bot`), carrying a real change queue, with
    results on and no season."""
    from datetime import datetime, timezone

    from tests.support.change_queue import (
        league_bot,
        league_queue,
        member,
        member_interaction,
        seed_server,
    )

    db_path = str(tmp_path / "hub_results_off.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
        )
        await db.execute("UPDATE server_configs SET hub_channel_id = ?", (HUB,))
        await db.commit()
    bot = await league_bot(db_path)
    league_queue(bot, db_path, now=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc))
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    interaction = member_interaction(bot, user=member(77))
    interaction.command = None
    return cog, interaction


async def test_the_confirmed_results_disable_asks_the_queue_to_refresh_the_panel(tmp_path):
    """The confirmation asks the queue; the switch-off asks the refresh, naming
    `/module disable`, once the queue runs it — never before."""
    from tests.support.change_queue import run_queue

    cog, interaction = await _queued_results_off(tmp_path)
    view = _ConfirmDisableResultsView(cog, actor_id=77)

    await view.confirm.callback(interaction)
    assert await _refreshes_asked(cog.bot) == []
    await run_queue(cog.bot)

    [asked] = await _refreshes_asked(cog.bot)
    assert "/module disable" in asked["command"]


# ── The permissions follow the roles ──────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "hub_hooks.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, league_admin_role_id) "
            "VALUES (?, 900, 100, 101, 902)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


def _bot_cog(db_path) -> BotCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = ConfigService(db_path)
    bot.signup_module_service.move_base_role_overwrite = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    return BotCog(bot)


def _role(role_id: int = 5150):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = "Members"
    role.mention = f"<@&{role_id}>"
    return role


@pytest.mark.parametrize(
    "attribute,reapplies",
    [
        ("handle_base_role", True),
        ("handle_interaction_role", True),
        ("handle_admin_role", True),
        ("handle_driver_role", False),
    ],
)
async def test_the_roles_the_hub_names_set_its_permissions_again(
    db_path, monkeypatch, attribute, reapplies
):
    reapply = AsyncMock()
    monkeypatch.setattr(bot_cog, "_reapply_hub_permissions", reapply)
    cog = _bot_cog(db_path)

    await undecorate(getattr(BotCog, attribute))(cog, _interaction(), _role())

    assert reapply.await_count == (1 if reapplies else 0)


async def test_a_channel_setting_leaves_the_hub_s_permissions_alone(db_path, monkeypatch):
    reapply = AsyncMock()
    monkeypatch.setattr(bot_cog, "_reapply_hub_permissions", reapply)
    cog = _bot_cog(db_path)
    channel = MagicMock()
    channel.id = 4040

    await undecorate(BotCog.handle_log_channel)(cog, _interaction(), channel)

    reapply.assert_not_awaited()


async def test_permissions_that_cannot_be_set_again_are_logged(db_path, monkeypatch):
    """The line names the member and the command that changed the role (#482)."""
    monkeypatch.setattr(
        hub_service, "reapply_hub_permissions", AsyncMock(return_value="No permission.")
    )
    cog = _bot_cog(db_path)
    interaction = _interaction()
    interaction.command.qualified_name = "bot base-role"

    await undecorate(BotCog.handle_base_role)(cog, interaction, _role())

    [line] = [
        str(call.args[0])
        for call in cog.bot.output_router.post_log.await_args_list
        if "No permission." in str(call.args[0])
    ]
    assert "Admin (<@77>)" in line and "/bot base-role" in line


async def test_permissions_that_raise_do_not_fail_the_role_command(monkeypatch):
    monkeypatch.setattr(
        hub_service, "reapply_hub_permissions", AsyncMock(side_effect=RuntimeError("x"))
    )

    await bot_cog._reapply_hub_permissions(MagicMock())

