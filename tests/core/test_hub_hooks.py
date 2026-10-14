"""What keeps the hub's panel and its permissions current (issue #279).

**The panel follows the modules.** A module's options are offered while it is enabled, so every
enable and disable asks the change queue to refresh the panel, as the bot's change (#439,
slice 6b): the toggle refreshes nothing itself. Turning results off is the exception in form, not
in effect: it is carried out on the change queue, whose switch-off asks the refresh in its own
save once the flag is down, so the command leaves it alone. A refresh the hub refuses is the
queue's to stop on (`test_hub_service.py`), never a line the toggle writes.

**The permissions follow the roles.** The hub is seen by the base role (or everyone, where none
is set) and by both tier roles, so changing any of the three sets them again. The driver role
and the channel settings name nobody the hub cares about.

**A restart recovers the hub.** Its buttons are routed again and its panel's refresh asked of the
change queue, handed to the recovery by the start-up.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

import leaguebot.__main__ as bot_module
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


NO_TOGGLE_ASK = (
    "#439: a module toggle refreshes the hub panel at once, rather than asking the change "
    "queue for it"
)
NO_RECOVER_HUB_QUEUE = (
    "#439: the start-up recovers the hub without handing it the change queue"
)
HUB = 7930


def _module_cog(bot, *, refused: bool = False) -> ModuleCog:
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    cog._refuse_module_change = AsyncMock(return_value=refused)
    for name in ("weather", "results", "attendance", "images", "signup"):
        setattr(cog, f"_enable_{name}", AsyncMock())
        setattr(cog, f"_disable_{name}", AsyncMock())
    return cog


@pytest.fixture
async def queued_bot(tmp_path):
    """The league's bot with a real change queue, its hub set to channel 7930."""
    from datetime import datetime, timezone

    from tests.support.change_queue import attach_queue, league_double, seed_server

    db_path = str(tmp_path / "hub_toggle.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET hub_channel_id = ?", (HUB,))
        await db.commit()
    bot = league_double(db_path)
    attach_queue(bot, db_path, now=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc))
    return bot


async def _refreshes_asked(bot) -> list[dict]:
    """The payload of every hub refresh asked of the queue, in order."""
    import json

    from tests.support.change_queue import change_rows

    return [json.loads(row["payload"]) for row in await change_rows(bot.db_path)
            if row["kind"] == hub_service.HUB_REFRESH]


# ── The panel follows the modules ─────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=NO_TOGGLE_ASK)
@pytest.mark.parametrize(
    ("module", "command"),
    [
        pytest.param(module, command, id=f"{module}-{command}")
        for module in ("weather", "results", "attendance", "images", "signup")
        for command in ("enable", "disable")
        if (module, command) != ("results", "disable")
    ],
)
async def test_every_toggle_asks_the_queue_to_refresh_the_panel(
    monkeypatch, queued_bot, command, module,
):
    """Every toggle but turning results off, whose switch-off asks the refresh in its own save.
    The hub is set; the manager runs `/module <command> <module>`: one refresh is asked of the
    queue, naming the command, and the panel is not refreshed by the toggle itself."""
    refresh = AsyncMock(return_value=None)
    monkeypatch.setattr(hub_service, "refresh_panel", refresh, raising=False)
    cog = _module_cog(queued_bot)
    interaction = _interaction()
    interaction.command.qualified_name = f"module {command}"

    await undecorate(getattr(ModuleCog, command))(cog, interaction, _choice(module))

    getattr(cog, f"_{command}_{module}").assert_awaited_once()
    [asked] = await _refreshes_asked(queued_bot)
    assert f"/module {command}" in asked["command"]
    refresh.assert_not_awaited()


async def test_turning_results_off_by_the_command_asks_no_refresh_of_its_own(queued_bot):
    """`/module disable results` only asks for its confirmation: the queue's switch-off asks
    the refresh, so the command asks none."""
    cog = _module_cog(queued_bot)

    await undecorate(ModuleCog.disable)(cog, _interaction(), _choice("results"))

    cog._disable_results.assert_awaited_once()
    assert await _refreshes_asked(queued_bot) == []


async def test_a_refused_toggle_leaves_the_panel_alone(queued_bot):
    """The toggle is refused: no refresh is asked of the queue."""
    cog = _module_cog(queued_bot, refused=True)

    await undecorate(ModuleCog.enable)(cog, _interaction(), _choice("weather"))

    assert await _refreshes_asked(queued_bot) == []


async def _queued_results_off(tmp_path) -> tuple[ModuleCog, MagicMock]:
    """A module cog whose bot carries a real change queue, with results on and no season."""
    from datetime import datetime, timezone

    from leaguebot.attendance.services.attendance_service import AttendanceService
    from tests.support.change_queue import (
        attach_queue,
        league_double,
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
    bot = league_double(db_path)
    bot.attendance_service = AttendanceService(db_path)
    attach_queue(bot, db_path, now=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc))
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


# ── A restart recovers the hub ────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=NO_RECOVER_HUB_QUEUE)
def test_the_hub_is_recovered_at_start_up():
    """`on_ready` is a closure inside `main`, so its wiring is asserted on the source: the
    start-up hands the recovery the change queue, which it asks for the panel. The recovery
    itself is tested in `test_hub_service.py`."""
    assert "recover_hub(bot, queue=bot.change_queue)" in inspect.getsource(bot_module.main)
