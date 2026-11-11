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
and the channel settings name nobody the hub cares about. The interaction role, the league
admin role and the base role are core `Setting`s on the change queue (#283, S13a and S13b): each
sets the permissions again from an `After` step its save plans, once the queue has saved the role,
never from the command. A refusal Discord returns there is logged as "Hub permissions not updated",
and any other fault is the step's to report in `setting_change`'s way: either leaves the role
saved, says so in the reply where it raised, and stops nothing.

**A restart recovers the hub.** Its buttons are routed again and its panel's refresh asked of the
change queue, handed to the recovery by the start-up.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from leaguebot.core.cogs.bot_cog import BotCog
from leaguebot.core.cogs.module_cog import ModuleCog, _ConfirmDisableResultsView
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.modules import hub_service
from tests.support.undecorate import undecorate

SERVER_ID = 27903


def _choice(name: str) -> app_commands.Choice:
    return app_commands.Choice(name=name, value=name)


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

def _module_cog(bot) -> ModuleCog:
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


@pytest.mark.parametrize(
    ("module", "command"),
    [
        pytest.param(module, command, id=f"{module}-{command}")
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
        pytest.param("already-on", id="already-on"),
        pytest.param("as-it-runs", id="as-it-runs"),
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


def _role(role_id: int = 5150):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = "Members"
    role.mention = f"<@&{role_id}>"
    return role


#: The member the role and channel settings below are run by, as a saved log line names them,
#: the mention wrapped so it notifies nobody.
NAMED = "Admin (`<@77>`)"
#: The role a setting below is set to: a role of the league's server, which holds no setting yet.
NEW_ROLE = 5150
#: What a role setting whose hub permissions raise adds to its success.
NOT_UPDATED = "⚠️ The setting is saved, but the hub's permissions could not be updated."


def _hub(*, edit_raises: BaseException | None = None) -> MagicMock:
    """The hub channel, 7930, as the league's server finds it: its `edit` recording the
    overwrites it is given, or raising *edit_raises*."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = HUB
    channel.edit = AsyncMock(side_effect=edit_raises)
    return channel


def _hub_guild(hub: MagicMock) -> MagicMock:
    """The league's server, as `test_hub_service._guild` builds it: it finds the hub *hub*, and
    the interaction role (900), the league admin role (902) and `NEW_ROLE`."""
    known = {role_id: _role(role_id) for role_id in (900, 902, NEW_ROLE)}
    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=lambda cid: hub if cid == HUB else None)
    guild.get_role = MagicMock(side_effect=known.get)
    guild.default_role = _role(1)
    guild.me = _role(2)
    return guild


async def _roles_league(db_path: str, hub: MagicMock) -> Any:
    """The bot the entry point builds on *db_path* (`db_path`'s league, its hub set to 7930), its
    configuration read from the database, the league's server finding *hub*, the log channel
    (101) and channel 4040 recording what each is sent, and its real change queue."""
    from datetime import datetime, timezone

    from tests.support.change_queue import league_bot, league_queue, text_channel

    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET hub_channel_id = ?", (HUB,))
        await db.commit()
    bot = await league_bot(db_path, real_config=True)
    bot.get_guild = MagicMock(return_value=_hub_guild(hub))
    channels = {101: bot.log_channel, 4040: text_channel(4040)}
    bot.get_channel = MagicMock(side_effect=channels.get)
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: channels[cid])
    league_queue(bot, db_path, now=datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc))
    return bot


def _asked(bot: Any, command: str) -> Any:
    """`/<command>` run on the league's server, as the bot finds it, by Admin (77)."""
    from tests.support.change_queue import member
    from tests.support.settings_league import interaction

    asked = interaction(bot, command, user=member(77))
    asked.guild_id = SERVER_ID
    asked.guild = bot.get_guild(SERVER_ID)
    return asked


def _lines(bot: Any) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    from tests.support.settings_league import unruled

    return [unruled(str(line)) for line in bot.log_channel.sent]


def _success(command: str, column: str) -> str:
    return f"{NAMED} | {command} | Success\n  {column}: `<@&{NEW_ROLE}>`"


#: The base role's success line: it names the role, as its log line always has.
_BASE_ROLE_SET = f"{NAMED} | /bot base-role | Success\n  role: Members (`<@&{NEW_ROLE}>`)"


async def _role_set_to(db_path: str, column: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT {column} FROM server_configs")
        return (await cursor.fetchone())[0]


async def test_a_channel_setting_leaves_the_hub_s_permissions_alone(db_path):
    """The league's hub is set; an administrator runs `/bot log-channel` with channel 4040, and
    the queue runs it. The hub channel's permissions are never edited."""
    from tests.support.change_queue import run_queue

    hub = _hub()
    bot = await _roles_league(db_path, hub)
    channel = MagicMock()
    channel.id = 4040
    channel.mention = "<#4040>"

    await undecorate(BotCog.handle_log_channel)(
        BotCog(bot), _asked(bot, "bot log-channel"), channel
    )
    await run_queue(bot)

    hub.edit.assert_not_awaited()


async def test_the_driver_role_leaves_the_hub_s_permissions_alone(db_path):
    """The league's hub is set; a manager sets the driver role to role 5150, one the bot can
    grant, and the queue runs it. The driver role names nobody the hub cares about: the role is
    saved, and the hub channel's permissions are never edited."""
    from tests.support.change_queue import run_queue

    hub = _hub()
    bot = await _roles_league(db_path, hub)
    role = _role(NEW_ROLE)
    role.is_default.return_value = False
    role.managed = False
    role.guild.me = None

    await undecorate(BotCog.handle_driver_role)(
        BotCog(bot), _asked(bot, "bot driver-role"), role
    )
    await run_queue(bot)

    assert await _role_set_to(db_path, "driver_role_id") == NEW_ROLE
    hub.edit.assert_not_awaited()


@pytest.mark.parametrize(
    "attribute,column,command",
    [
        pytest.param("handle_interaction_role", "interaction_role_id", "bot interaction-role",
                     id="interaction-role"),
        pytest.param("handle_admin_role", "league_admin_role_id", "bot admin-role",
                     id="admin-role"),
        pytest.param("handle_base_role", "base_role_id", "bot base-role", id="base-role"),
    ],
)
async def test_a_role_setting_sets_the_hub_s_permissions_again_as_its_follow_on(
    db_path, attribute, column, command
):
    """The league's hub is set; an administrator sets the interaction role, or the league admin
    role, or a manager the base role, to role 5150. The command sets no permission itself: the
    hub is untouched while the setting waits on the queue. Once the queue has run it, the hub's
    permissions have been set once, and they name the new role."""
    from tests.support.change_queue import run_queue

    hub = _hub()
    bot = await _roles_league(db_path, hub)

    await undecorate(getattr(BotCog, attribute))(
        BotCog(bot), _asked(bot, command), _role(NEW_ROLE)
    )
    hub.edit.assert_not_awaited()
    await run_queue(bot)

    hub.edit.assert_awaited_once()
    assert NEW_ROLE in {role.id for role in hub.edit.await_args.kwargs["overwrites"]}
    assert await _role_set_to(db_path, column) == NEW_ROLE


_REFUSED = discord.HTTPException(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")


_INTERACTION_ROLE = (
    "handle_interaction_role", "interaction_role_id", "/bot interaction-role", "Interaction role",
    _success("/bot interaction-role", "interaction_role_id"),
)
_BASE_ROLE = ("handle_base_role", "base_role_id", "/bot base-role", "Base role", _BASE_ROLE_SET)


@pytest.mark.parametrize(
    "setting,fault,raised",
    [
        pytest.param(_INTERACTION_ROLE, _REFUSED, False, id="refused"),
        pytest.param(_INTERACTION_ROLE, RuntimeError("x"), True, id="raises"),
        pytest.param(_BASE_ROLE, _REFUSED, False, id="base-role-refused"),
        pytest.param(_BASE_ROLE, RuntimeError("x"), True, id="base-role-raises"),
    ],
)
async def test_hub_permissions_that_fail_after_a_role_setting_leave_it_saved(
    db_path, setting, fault, raised
):
    """The league's hub is set; an administrator sets the interaction role, or a manager the base
    role, to role 5150, and setting the hub's permissions again fails once the queue has saved
    it: Discord refuses the edit (*refused*), or it raises (*raises*). Either way the role is
    saved and the queue is not stopped. A refusal keeps the success reply, and the log channel
    gets the success line, then a line naming the member, the command and the refusal. A raise
    adds to the reply that the permissions could not be updated, and the log channel gets the
    success line, then the queue's failure line naming the kind of fault."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said

    attribute, column, command, label, success = setting
    bot = await _roles_league(db_path, _hub(edit_raises=fault))
    asked = _asked(bot, command[1:])

    await undecorate(getattr(BotCog, attribute))(BotCog(bot), asked, _role(NEW_ROLE))
    await run_queue(bot)

    assert await _role_set_to(db_path, column) == NEW_ROLE
    if raised:
        assert last_said(asked) == f"✅ **{label}** set to <@&{NEW_ROLE}>.\n{NOT_UPDATED}"
        line = (
            f"❌ `{command}` failed for {NAMED} — RuntimeError. The details are in the host's log."
        )
    else:
        assert last_said(asked) == f"✅ **{label}** set to <@&{NEW_ROLE}>."
        line = (
            f"{NAMED} | `{command}` | Hub permissions not updated: The hub <#{HUB}>'s "
            f"permissions could not be set: {_REFUSED}"
        )
    assert _lines(bot) == [success, line]
    assert await stopped_job(db_path) is None


