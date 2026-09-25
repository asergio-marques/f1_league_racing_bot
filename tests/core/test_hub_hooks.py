"""What keeps the hub's panel and its permissions current (issue #279).

**The panel follows the modules.** A module's options are offered while it is enabled, so every
enable and disable refreshes the panel — the confirmed results disable included, since it
finishes after the command has returned. A refresh that fails is logged, and never fails the
toggle behind it.

**The permissions follow the roles.** The hub is seen by the base role (or everyone, where none
is set) and by both tier roles, so changing any of the three sets them again. The driver role
and the channel settings name nobody the hub cares about.

**A restart recovers the hub.** Its buttons are routed again and its panel posted afresh where
it was deleted.
"""
from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import leaguebot.__main__ as bot_module  # noqa: E402
import leaguebot.core.cogs.bot_cog as bot_cog  # noqa: E402
from leaguebot.core.cogs.bot_cog import BotCog  # noqa: E402
from leaguebot.core.cogs.module_cog import ModuleCog, _ConfirmDisableResultsView  # noqa: E402
from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.core.services import hub_service  # noqa: E402
from leaguebot.core.services.config_service import ConfigService  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

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


def _module_cog(*, refused: bool = False) -> ModuleCog:
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = MagicMock()
    cog.bot.output_router.post_log = AsyncMock()
    cog._refuse_module_change = AsyncMock(return_value=refused)
    for name in ("weather", "results", "attendance", "images", "signup"):
        setattr(cog, f"_enable_{name}", AsyncMock())
        setattr(cog, f"_disable_{name}", AsyncMock())
    return cog


# ── The panel follows the modules ─────────────────────────────────────────


@pytest.mark.parametrize("command", ["enable", "disable"])
@pytest.mark.parametrize("module", ["weather", "results", "attendance", "images", "signup"])
async def test_every_toggle_refreshes_the_panel(monkeypatch, command, module):
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr(hub_service, "refresh_panel", refresh)
    cog = _module_cog()

    await undecorate(getattr(ModuleCog, command))(cog, _interaction(), _choice(module))

    getattr(cog, f"_{command}_{module}").assert_awaited_once()
    refresh.assert_awaited_once_with(cog.bot)


async def test_a_refused_toggle_leaves_the_panel_alone(monkeypatch):
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr(hub_service, "refresh_panel", refresh)
    cog = _module_cog(refused=True)

    await undecorate(ModuleCog.enable)(cog, _interaction(), _choice("weather"))

    refresh.assert_not_awaited()


async def test_the_confirmed_results_disable_refreshes_the_panel():
    """It finishes on a button, after `/module disable` has already returned."""
    cog = MagicMock()
    cog._apply_results_disable = AsyncMock()
    cog._refresh_hub = AsyncMock()
    view = _ConfirmDisableResultsView(cog, actor_id=77)
    interaction = _interaction()

    await view.confirm.callback(interaction)

    cog._apply_results_disable.assert_awaited_once()
    cog._refresh_hub.assert_awaited_once()


async def test_a_panel_that_cannot_be_refreshed_is_logged(monkeypatch):
    monkeypatch.setattr(
        hub_service, "refresh_panel", AsyncMock(return_value="The hub channel is gone.")
    )
    cog = _module_cog()

    await cog._refresh_hub()

    assert "The hub channel is gone." in cog.bot.output_router.post_log.await_args.args[0]


async def test_a_refresh_that_raises_does_not_fail_the_toggle(monkeypatch):
    monkeypatch.setattr(hub_service, "refresh_panel", AsyncMock(side_effect=RuntimeError("x")))
    cog = _module_cog()

    await undecorate(ModuleCog.disable)(cog, _interaction(), _choice("weather"))

    cog._disable_weather.assert_awaited_once()


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


async def test_permissions_that_cannot_be_set_again_are_logged(monkeypatch):
    monkeypatch.setattr(
        hub_service, "reapply_hub_permissions", AsyncMock(return_value="No permission.")
    )
    bot = MagicMock()
    bot.output_router.post_log = AsyncMock()

    await bot_cog._reapply_hub_permissions(bot)

    assert "No permission." in bot.output_router.post_log.await_args.args[0]


async def test_permissions_that_raise_do_not_fail_the_role_command(monkeypatch):
    monkeypatch.setattr(
        hub_service, "reapply_hub_permissions", AsyncMock(side_effect=RuntimeError("x"))
    )

    await bot_cog._reapply_hub_permissions(MagicMock())


# ── A restart recovers the hub ────────────────────────────────────────────


def test_the_hub_is_recovered_at_start_up():
    """`on_ready` is a closure inside `main`, so its wiring is asserted on the source; the
    recovery itself is tested in `test_hub_service.py`."""
    assert "recover_hub(bot)" in inspect.getsource(bot_module.main)
