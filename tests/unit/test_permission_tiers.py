"""The two tiers of authority, and the guards that ask for them.

Every property here is one a plausible tidy-up would undo, and several are the defects that
prompted the change (issue #116).

**Both tiers are roles.** Discord's permissions are not a route to either. A member holding
Administrator and nothing else is refused a league manager command as flatly as a member
holding nothing at all — which is the whole point of separating what somebody may do to a
Discord server from what they may do to a racing league.

**The higher tier carries the lower.** A league admin runs a league manager's command
without also holding the interaction role. Under the guard this replaced they were refused,
because the check read the role and a Discord permission is not a role.

**An unconfigured admin role refuses rather than falls back.** Every server configured
before the role existed holds none. Falling back to Administrator there would quietly
reinstate the conflation being removed, so the refusal names `/bot-admin-role` instead.

**The setup commands are the one exception, and they are exempt twice over** — from the
interaction channel and from the roles — because they repair the settings the other guards
read.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.server_config import ServerConfig  # noqa: E402
from utils.channel_guard import (  # noqa: E402
    CHANNEL_EXEMPT_ATTRIBUTE,
    LEAGUE_ADMIN,
    LEAGUE_MANAGER,
    TIER_ATTRIBUTE,
    bot_setup_only,
    is_league_admin,
    is_league_manager,
    league_admin_only,
    league_manager_only,
    may_set_up_bot,
)

SERVER_ID = 4242
CHANNEL = 111
MANAGER_ROLE = 222
ADMIN_ROLE = 444
LOG_CHANNEL = 333


def _config(*, admin_role: int | None = ADMIN_ROLE) -> ServerConfig:
    return ServerConfig(
        server_id=SERVER_ID,
        interaction_role_id=MANAGER_ROLE,
        league_admin_role_id=admin_role,
        interaction_channel_id=CHANNEL,
        log_channel_id=LOG_CHANNEL,
    )


def _role(role_id: int, name: str) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.name = name
    return role


_ROLES = {MANAGER_ROLE: _role(MANAGER_ROLE, "Stewards"), ADMIN_ROLE: _role(ADMIN_ROLE, "Owners")}


def _member(*, roles: tuple[int, ...] = (), administrator: bool = False) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = 7
    member.roles = [_ROLES[r] for r in roles]
    member.guild_permissions = MagicMock()
    member.guild_permissions.administrator = administrator
    return member


def _interaction(member: MagicMock, *, channel_id: int = CHANNEL) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.channel_id = channel_id
    interaction.user = member
    interaction.guild.get_role = lambda role_id: _ROLES.get(role_id)
    interaction.response.send_message = AsyncMock()
    return interaction


def _cog(config: ServerConfig | None) -> MagicMock:
    cog = MagicMock()
    cog.bot.config_service.get_server_config = AsyncMock(return_value=config)
    return cog


def _guarded(decorator):
    """A decorated command body that records whether it ran."""
    ran: list[bool] = []

    @decorator
    async def command(self, interaction, *args, **kwargs) -> None:
        ran.append(True)

    return command, ran


def _reply(interaction: MagicMock) -> str:
    return interaction.response.send_message.call_args.args[0]


# ── The predicates ────────────────────────────────────────────────────────


def test_the_admin_role_holder_is_a_league_admin():
    assert is_league_admin(_config(), _member(roles=(ADMIN_ROLE,))) is True


def test_administrator_alone_is_not_a_league_admin():
    """The conflation this model exists to remove, stated at the predicate."""
    assert is_league_admin(_config(), _member(administrator=True)) is False


def test_nobody_is_a_league_admin_while_no_admin_role_is_configured():
    assert is_league_admin(_config(admin_role=None), _member(roles=(ADMIN_ROLE,))) is False


def test_the_interaction_role_holder_is_a_league_manager():
    assert is_league_manager(_config(), _member(roles=(MANAGER_ROLE,))) is True


def test_a_league_admin_is_a_league_manager_without_the_interaction_role():
    """The higher tier carries the lower, so no member needs both roles."""
    assert is_league_manager(_config(), _member(roles=(ADMIN_ROLE,))) is True


def test_administrator_alone_is_not_a_league_manager():
    assert is_league_manager(_config(), _member(administrator=True)) is False


def test_administrator_may_set_the_bot_up():
    assert may_set_up_bot(_config(), _member(administrator=True)) is True


def test_a_league_admin_may_set_the_bot_up():
    assert may_set_up_bot(_config(), _member(roles=(ADMIN_ROLE,))) is True


def test_a_league_manager_may_not_set_the_bot_up():
    assert may_set_up_bot(_config(), _member(roles=(MANAGER_ROLE,))) is False


# ── league_admin_only ─────────────────────────────────────────────────────


async def test_a_league_admin_runs_a_league_admin_command():
    command, ran = _guarded(league_admin_only)
    await command(_cog(_config()), _interaction(_member(roles=(ADMIN_ROLE,))))
    assert ran == [True]


async def test_a_league_manager_is_refused_a_league_admin_command():
    command, ran = _guarded(league_admin_only)
    interaction = _interaction(_member(roles=(MANAGER_ROLE,)))
    await command(_cog(_config()), interaction)
    assert ran == []
    assert "league admin's" in _reply(interaction)
    assert "Owners" in _reply(interaction)


async def test_administrator_is_refused_a_league_admin_command():
    """Holding the Discord permission is not holding the tier."""
    command, ran = _guarded(league_admin_only)
    interaction = _interaction(_member(administrator=True))
    await command(_cog(_config()), interaction)
    assert ran == []
    assert "league admin's" in _reply(interaction)


async def test_a_league_admin_command_is_refused_while_no_admin_role_is_set():
    """Every server configured before the role existed lands here."""
    command, ran = _guarded(league_admin_only)
    interaction = _interaction(_member(administrator=True))
    await command(_cog(_config(admin_role=None)), interaction)
    assert ran == []
    assert "/bot-admin-role" in _reply(interaction)


# ── league_manager_only ───────────────────────────────────────────────────


async def test_a_league_manager_runs_a_league_manager_command():
    command, ran = _guarded(league_manager_only)
    await command(_cog(_config()), _interaction(_member(roles=(MANAGER_ROLE,))))
    assert ran == [True]


async def test_a_league_admin_runs_a_league_manager_command_without_the_interaction_role():
    """Issue #116's headline defect: this member used to be refused 127 commands."""
    command, ran = _guarded(league_manager_only)
    await command(_cog(_config()), _interaction(_member(roles=(ADMIN_ROLE,))))
    assert ran == [True]


async def test_administrator_is_refused_a_league_manager_command():
    command, ran = _guarded(league_manager_only)
    interaction = _interaction(_member(administrator=True))
    await command(_cog(_config()), interaction)
    assert ran == []
    assert "don't have permission" in _reply(interaction)


async def test_a_league_manager_command_names_both_roles_when_it_refuses():
    command, _ = _guarded(league_manager_only)
    interaction = _interaction(_member())
    await command(_cog(_config()), interaction)
    assert "Stewards" in _reply(interaction)
    assert "Owners" in _reply(interaction)


# ── The channel ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "decorator,roles",
    [(league_admin_only, (ADMIN_ROLE,)), (league_manager_only, (MANAGER_ROLE,))],
    ids=["league_admin_only", "league_manager_only"],
)
async def test_a_tier_command_is_refused_outside_the_interaction_channel(decorator, roles):
    command, ran = _guarded(decorator)
    interaction = _interaction(_member(roles=roles), channel_id=CHANNEL + 1)
    await command(_cog(_config()), interaction)
    assert ran == []
    assert "interaction channel" in _reply(interaction)


async def test_the_channel_is_checked_before_the_tier():
    """Today's order, kept deliberately. Whether it discloses too much is issue #145."""
    command, _ = _guarded(league_admin_only)
    interaction = _interaction(_member(), channel_id=CHANNEL + 1)
    await command(_cog(_config()), interaction)
    assert "interaction channel" in _reply(interaction)


# ── bot_setup_only ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "member",
    [_member(administrator=True), _member(roles=(ADMIN_ROLE,))],
    ids=["administrator", "league admin"],
)
async def test_a_setup_command_runs_from_any_channel(member):
    command, ran = _guarded(bot_setup_only)
    await command(_cog(_config()), _interaction(member, channel_id=CHANNEL + 9999))
    assert ran == [True]


async def test_a_setup_command_refuses_a_league_manager():
    command, ran = _guarded(bot_setup_only)
    interaction = _interaction(_member(roles=(MANAGER_ROLE,)))
    await command(_cog(_config()), interaction)
    assert ran == []
    assert "Administrator" in _reply(interaction)


async def test_a_setup_command_runs_for_an_administrator_before_the_bot_is_configured():
    """The bootstrap: with no configuration there is no role for anybody to hold."""
    command, ran = _guarded(bot_setup_only)
    await command(_cog(None), _interaction(_member(administrator=True)))
    assert ran == [True]


async def test_a_setup_command_runs_for_an_administrator_while_no_admin_role_is_set():
    """The way back for a league that predates the role, or deleted it."""
    command, ran = _guarded(bot_setup_only)
    await command(_cog(_config(admin_role=None)), _interaction(_member(administrator=True)))
    assert ran == [True]


# ── Before the bot is set up ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "decorator", [league_admin_only, league_manager_only],
    ids=["league_admin_only", "league_manager_only"],
)
async def test_a_tier_command_is_refused_before_the_bot_is_configured(decorator):
    """The guard this replaced let these through untouched.

    With no `ServerConfig` there is no channel to check and no role anyone can hold, so
    passing the command to its body ran it for whoever asked.
    """
    command, ran = _guarded(decorator)
    interaction = _interaction(_member(administrator=True))
    await command(_cog(None), interaction)
    assert ran == []
    assert "/bot-init" in _reply(interaction)


# ── Refusals name roles, never mention them ───────────────────────────────


@pytest.mark.parametrize(
    "decorator,config",
    [
        (league_admin_only, _config()),
        (league_manager_only, _config()),
        (bot_setup_only, _config()),
    ],
    ids=["league_admin_only", "league_manager_only", "bot_setup_only"],
)
async def test_a_refusal_never_mentions_a_role(decorator, config):
    """A refusal that pinged the role would notify every holder on every mistyped command."""
    command, _ = _guarded(decorator)
    interaction = _interaction(_member())
    await command(_cog(config), interaction)
    assert "<@&" not in _reply(interaction)


async def test_a_refusal_describes_a_role_that_has_been_deleted():
    """Precisely when a member is most likely to meet one of these refusals."""
    command, _ = _guarded(league_admin_only)
    interaction = _interaction(_member())
    interaction.guild.get_role = lambda role_id: None
    await command(_cog(_config()), interaction)
    assert "the league admin role" in _reply(interaction)


# ── The tier is readable off the decorator ────────────────────────────────


@pytest.mark.parametrize(
    "decorator,tier,exempt",
    [
        (league_admin_only, LEAGUE_ADMIN, False),
        (league_manager_only, LEAGUE_MANAGER, False),
        (bot_setup_only, LEAGUE_ADMIN, True),
    ],
    ids=["league_admin_only", "league_manager_only", "bot_setup_only"],
)
def test_a_guard_records_the_tier_it_asks_for(decorator, tier, exempt):
    """What lets one test assert the tier of every command the bot has."""
    command, _ = _guarded(decorator)
    assert getattr(command, TIER_ATTRIBUTE) == tier
    assert getattr(command, CHANNEL_EXEMPT_ATTRIBUTE) is exempt


# ── The wrapper's globals ─────────────────────────────────────────────────


def test_a_command_annotation_resolves_against_this_module():
    """A guard's wrapper carries this module's globals, and discord.py reads them.

    `functools.wraps` copies a function's identity but not its namespace, so
    `wrapper.__globals__` is `utils.channel_guard`'s. Cogs run under
    `from __future__ import annotations`, so a parameter annotated
    `app_commands.Range[int, 1, 10]` arrives at discord.py as a string and
    `_extract_parameters_from_callback` resolves it against `callback.__globals__` — here.

    `app_commands` therefore has to be importable in the guard module even though nothing in
    it uses the name, and deleting it as an unused import raises `NameError: name
    'app_commands' is not defined` at *class body* time in every cog that annotates a
    parameter with it — an import-time explosion a long way from its cause.

    `/clean-bot` is the live example: `count: app_commands.Range[int, 1, 10]`.
    """
    from cogs.clean_cog import CleanCog

    callback = CleanCog.clean_bot.callback
    assert "app_commands" in callback.__globals__
    assert "discord" in callback.__globals__

    # And the parameter really did resolve, rather than being skipped.
    count = next(p for p in CleanCog.clean_bot.parameters if p.name == "count")
    assert count.min_value == 1
    assert count.max_value == 10
