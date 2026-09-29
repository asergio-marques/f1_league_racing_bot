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
reinstate the conflation being removed, so the refusal names `/bot admin-role` instead.

**The setup commands are the one exception, and they are exempt twice over** — from the
interaction channel and from the roles — because they repair the settings the other guards
read.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.models.server_config import ServerConfig
from leaguebot.core.utils.channel_guard import (
    CHANGES_NOTHING_ATTRIBUTE,
    CHANNEL_EXEMPT_ATTRIBUTE,
    LEAGUE_ADMIN,
    LEAGUE_MANAGER,
    TIER_ATTRIBUTE,
    bot_setup_only,
    changes_nothing,
    is_league_admin,
    is_league_manager,
    league_admin_only,
    league_manager_only,
    may_set_up_bot,
    server_owner_only,
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
    member.display_name = "Alex"
    member.roles = [_ROLES[r] for r in roles]
    member.guild_permissions = MagicMock()
    member.guild_permissions.administrator = administrator
    return member


def _interaction(
    member: MagicMock, *, channel_id: int = CHANNEL, command: str = "round add"
) -> MagicMock:
    """*member* using `/<command>` on the league's server, before anything has answered it."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.channel_id = channel_id
    interaction.user = member
    interaction.command.qualified_name = command
    interaction.guild.get_role = lambda role_id: _ROLES.get(role_id)
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.client.output_router.post_log = AsyncMock()
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


def _logged(interaction: MagicMock) -> list[str]:
    """Every line written to the league's log channel."""
    return [str(c.args[0]) for c in interaction.client.output_router.post_log.await_args_list]


_GUARD_LOG = "leaguebot.core.utils.channel_guard"


def _host_records_refusal(
    caplog: pytest.LogCaptureFixture, command: str, *, guild: int | None
) -> bool:
    """Whether the guards wrote one line to the host's log naming *command* as the member
    typed it, the member's id (7) and, where there is one, the server's id."""
    for record in caplog.records:
        if record.name != _GUARD_LOG or record.levelno < logging.INFO:
            continue
        line = record.getMessage()
        if (
            command in line
            and re.search(r"(?<!\d)7(?!\d)", line)
            and (guild is None or str(guild) in line)
        ):
            return True
    return False


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
    assert "/bot admin-role" in _reply(interaction)


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
    assert "/bot init" in _reply(interaction)


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


# ── A refusal is recorded in the log channel (#482) ───────────────────────

_NOT_RECORDED = "#482: a guard refusal is not yet recorded in the log channel"
_NOT_IN_HOST_LOG = (
    "#482: a refusal kept out of the log channel does not yet write a host-log line naming "
    "the command, the user id and the server"
)


async def test_a_wrong_channel_refusal_is_recorded():
    """A manager's command used outside the interaction channel: the member is told, as ever,
    and the log channel records who was refused what and why."""
    command, ran = _guarded(league_manager_only)
    interaction = _interaction(_member(roles=(MANAGER_ROLE,)), channel_id=CHANNEL + 1)

    await command(_cog(_config()), interaction)

    assert ran == []
    interaction.response.send_message.assert_awaited_once_with(
        "⛔ This command can only be used in the configured interaction channel.", ephemeral=True
    )
    assert _logged(interaction) == [
        "⛔ `/round add` refused for Alex (<@7>) — "
        "This command can only be used in the configured interaction channel."
    ]


async def test_a_member_of_neither_tier_is_recorded_by_name_and_mention():
    """The line names the member by display name and mention, and the roles by name alone."""
    command, _ = _guarded(league_manager_only)
    interaction = _interaction(_member())

    await command(_cog(_config()), interaction)

    assert _logged(interaction) == [
        "⛔ `/round add` refused for Alex (<@7>) — You don't have permission to use this "
        "command. You need **Stewards**, or **Owners**."
    ]


async def test_a_league_manager_refused_an_admin_command_is_recorded():
    command, _ = _guarded(league_admin_only)
    interaction = _interaction(_member(roles=(MANAGER_ROLE,)), command="module enable")

    await command(_cog(_config()), interaction)

    assert _logged(interaction) == [
        "⛔ `/module enable` refused for Alex (<@7>) — "
        "This command is a league admin's. You need **Owners**."
    ]


async def test_an_admin_command_refused_for_want_of_an_admin_role_is_recorded():
    command, _ = _guarded(league_admin_only)
    interaction = _interaction(_member(administrator=True), command="module enable")

    await command(_cog(_config(admin_role=None)), interaction)

    [line] = _logged(interaction)
    assert line.startswith(
        "⛔ `/module enable` refused for Alex (<@7>) — No league admin role is configured"
    )


@pytest.mark.parametrize(
    "decorator", [league_admin_only, league_manager_only],
    ids=["league_admin_only", "league_manager_only"],
)
async def test_a_refusal_before_the_bot_is_set_up_goes_to_the_host_log_alone(decorator, caplog):
    """With no configuration there is no log channel to write to (owner decision on #482,
    2026-09-29: host log only). The member is still told to run `/bot init`, and the host's
    log records the refusal."""
    caplog.set_level(logging.INFO, logger=_GUARD_LOG)
    command, _ = _guarded(decorator)
    interaction = _interaction(_member(roles=(MANAGER_ROLE,)))

    await command(_cog(None), interaction)

    assert "/bot init" in _reply(interaction)
    interaction.client.output_router.post_log.assert_not_awaited()
    assert _host_records_refusal(caplog, "round add", guild=SERVER_ID)


async def test_a_command_used_in_a_direct_message_goes_to_the_host_log_alone(caplog):
    """A direct message to a manager's command in a group not limited to servers: it arrives
    with no guild and the direct message's own channel id, so it meets the wrong-channel
    refusal first. The bot cannot tell from a direct message whether the person belongs to the
    league, so nothing is written to the league's log channel (#482, assumed A1); the host's
    log records it instead."""
    caplog.set_level(logging.INFO, logger=_GUARD_LOG)
    command, ran = _guarded(league_manager_only)
    user = MagicMock(spec=discord.User)
    user.id = 7
    user.display_name = "Alex"
    interaction = _interaction(user, channel_id=987654321)
    interaction.guild = None
    interaction.guild_id = None

    await command(_cog(_config()), interaction)

    assert ran == []
    interaction.response.send_message.assert_awaited_once_with(
        "⛔ This command can only be used in the configured interaction channel.", ephemeral=True
    )
    interaction.client.output_router.post_log.assert_not_awaited()
    assert _host_records_refusal(caplog, "round add", guild=None)


async def test_a_setup_command_refused_on_a_set_up_server_is_recorded():
    command, ran = _guarded(bot_setup_only)
    interaction = _interaction(_member(roles=(MANAGER_ROLE,)), command="bot admin-role")

    await command(_cog(_config()), interaction)

    assert ran == []
    assert _logged(interaction) == [
        "⛔ `/bot admin-role` refused for Alex (<@7>) — You need **Owners**, or Discord's "
        "**Administrator** permission, to change the bot's settings."
    ]


async def test_a_setup_command_refused_before_the_bot_is_set_up_goes_to_the_host_log_alone(
    caplog,
):
    caplog.set_level(logging.INFO, logger=_GUARD_LOG)
    command, ran = _guarded(bot_setup_only)
    interaction = _interaction(_member(roles=(MANAGER_ROLE,)), command="bot init")

    await command(_cog(None), interaction)

    assert ran == []
    assert "Administrator" in _reply(interaction)
    interaction.client.output_router.post_log.assert_not_awaited()
    assert _host_records_refusal(caplog, "bot init", guild=SERVER_ID)


def _not_the_owner() -> MagicMock:
    interaction = _interaction(_member(administrator=True), command="bot factory-reset")
    interaction.guild.owner_id = 1
    return interaction


async def test_a_factory_reset_refused_on_a_set_up_server_is_recorded():
    command, ran = _guarded(server_owner_only)
    interaction = _not_the_owner()

    await command(_cog(_config()), interaction)

    assert ran == []
    assert _logged(interaction) == [
        "⛔ `/bot factory-reset` refused for Alex (<@7>) — Only this server's owner may "
        "factory-reset the bot, whatever roles or permissions anyone else holds."
    ]


async def test_a_factory_reset_refused_before_the_bot_is_set_up_goes_to_the_host_log_alone(
    caplog,
):
    caplog.set_level(logging.INFO, logger=_GUARD_LOG)
    command, ran = _guarded(server_owner_only)
    interaction = _not_the_owner()

    await command(_cog(None), interaction)

    assert ran == []
    assert "owner" in _reply(interaction)
    interaction.client.output_router.post_log.assert_not_awaited()
    assert _host_records_refusal(caplog, "bot factory-reset", guild=SERVER_ID)


async def test_the_owner_s_factory_reset_reads_no_setting():
    """A factory reset may be the way out of a configuration past repair, so the owner's path
    reads none of it: the configuration is read only once the owner check has failed, to
    decide whether the refusal is recorded."""
    command, ran = _guarded(server_owner_only)
    interaction = _interaction(_member(), command="bot factory-reset")
    interaction.guild.owner_id = 7
    cog = _cog(_config())

    await command(cog, interaction)

    assert ran == [True]
    cog.bot.config_service.get_server_config.assert_not_awaited()


# ── A view, list or preview records nothing, a refusal included (#482) ────


@pytest.mark.parametrize(
    "member,channel_id",
    [
        pytest.param(_member(roles=(MANAGER_ROLE,)), CHANNEL + 1, id="wrong channel"),
        pytest.param(_member(), CHANNEL, id="missing roles"),
    ],
)
async def test_a_refused_view_is_not_recorded(member, channel_id, caplog):
    """A view, a list or a preview changes nothing and shall record nothing (core
    specification, "The record of what changed"), its refusal included. The member is answered
    as ever and the host's log keeps the refusal; the same command left unmarked is recorded,
    so the mark and not the refusal is what keeps the line out of the log channel."""
    caplog.set_level(logging.INFO, logger=_GUARD_LOG)
    ran: list[bool] = []

    @league_manager_only
    @changes_nothing
    async def view(self, interaction, *args, **kwargs) -> None:
        ran.append(True)

    @league_manager_only
    async def twin(self, interaction, *args, **kwargs) -> None:
        ran.append(True)

    viewed = _interaction(member, channel_id=channel_id, command="track list")
    await view(_cog(_config()), viewed)

    assert ran == []
    viewed.response.send_message.assert_awaited_once()
    assert viewed.response.send_message.await_args.kwargs == {"ephemeral": True}
    viewed.client.output_router.post_log.assert_not_awaited()
    assert _host_records_refusal(caplog, "track list", guild=SERVER_ID)

    acted = _interaction(member, channel_id=channel_id, command="track add")
    await twin(_cog(_config()), acted)

    assert ran == []
    [line] = _logged(acted)
    assert line.startswith("⛔ `/track add` refused for Alex (<@7>) — ")


def test_the_view_commands_are_marked_as_changing_nothing():
    """Exactly the view, list and preview commands carry the mark, and no acting command does.

    The three review commands lead to a change — confirming a season's configuration, approving
    its placements, approving an amendment — so a refusal of any of them is recorded, and they
    must not carry the mark."""
    from tests.core.test_command_tiers import COMMANDS

    views = {
        "attendance config show",
        "weather config view",
        "track list",
        "season status",
        "test-mode review",
        "test-mode backup status",
        "test-mode roster list",
        "results config list",
        "results config view",
        "team list",
        "team lineup",
        "signup config view",
        "signup time-slot list",
        "signup unassigned list",
        "signup unassigned export",
        "images config view",
        "images test calendar",
        "images test lineup",
        "images test results",
        "images test standings",
        "images test attendance",
        "images test rsvp",
        "images test verdict",
        "images test verdict-banner",
        "images test weather-p1",
        "images test weather-p2",
        "images test weather-p3",
        "images test weather-mystery",
    }
    assert views <= set(COMMANDS), "a view command named here no longer exists"

    marked = {
        name
        for name, command in COMMANDS.items()
        if getattr(command.callback, CHANGES_NOTHING_ATTRIBUTE, False)
    }

    assert marked == views


async def test_a_factory_reset_refusal_survives_an_unreadable_configuration(caplog):
    """A configuration past repair is what a factory reset exists for, so a read that fails
    must not cost the member their answer: the refusal still reaches them, nothing is written
    to the log channel, and the host's log keeps the failed read."""
    caplog.set_level(logging.INFO, logger=_GUARD_LOG)
    command, ran = _guarded(server_owner_only)
    interaction = _not_the_owner()
    cog = _cog(_config())
    cog.bot.config_service.get_server_config = AsyncMock(
        side_effect=sqlite3.OperationalError("database disk image is malformed")
    )

    await command(cog, interaction)

    assert ran == []
    interaction.response.send_message.assert_awaited_once_with(
        "⛔ Only this server's owner may factory-reset the bot, whatever roles or "
        "permissions anyone else holds.",
        ephemeral=True,
    )
    interaction.client.output_router.post_log.assert_not_awaited()
    assert any(
        record.name == _GUARD_LOG
        and record.levelno >= logging.ERROR
        and record.exc_info is not None
        and isinstance(record.exc_info[1], sqlite3.OperationalError)
        for record in caplog.records
    )


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
    `wrapper.__globals__` is `leaguebot.core.utils.channel_guard`'s. Cogs run under
    `from __future__ import annotations`, so a parameter annotated
    `app_commands.Range[int, 1, 10]` arrives at discord.py as a string and
    `_extract_parameters_from_callback` resolves it against `callback.__globals__` — here.

    `app_commands` therefore has to be importable in the guard module even though nothing in
    it uses the name, and deleting it as an unused import raises `NameError: name
    'app_commands' is not defined` at *class body* time in every cog that annotates a
    parameter with it — an import-time explosion a long way from its cause.

    `/clean-bot` is the live example: `count: app_commands.Range[int, 1, 10]`.
    """
    from leaguebot.core.cogs.clean_cog import CleanCog

    callback = CleanCog.clean_bot.callback
    assert "app_commands" in callback.__globals__
    assert "discord" in callback.__globals__

    # And the parameter really did resolve, rather than being skipped.
    count = next(p for p in CleanCog.clean_bot.parameters if p.name == "count")
    assert count.min_value == 1
    assert count.max_value == 10
