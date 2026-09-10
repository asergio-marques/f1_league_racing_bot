"""The two tiers of authority, and the guards that ask for them.

Every command of the bot sits in one tier and no other:

  **League admin** holds the league admin role. They govern what the bot is upon the server
  and everything that may undo a league entire — starting over, enabling and disabling a
  module, every command of test mode, and the commands that destroy what a league is built
  from where nothing puts it back.

  **League manager** holds the interaction role, or the league admin role. They run the
  league: its seasons, divisions, rounds, tracks, teams, drivers and seats, the
  configuration of every module, and the results and standings that follow.

Both tiers are roles the league configures. **Discord's own permissions are not a route to
either**, and `manage_guild` is no longer consulted anywhere. A Discord permission is a
property of the server — it carries the power to delete channels and ban members, and is
given for reasons that have nothing to do with a racing league. A tier is a property of the
league.

The single exception is `bot_setup_only`, worn by `/bot-init` and the four single-setting
commands. Those accept the Administrator permission as well as the role, and run from any
channel, because they are what repairs the settings every other guard reads: a deleted
interaction channel, or a league admin role removed from the server, would otherwise be
unrepairable.

**Order of the checks.** Channel first, then permission. That is the behaviour the bot has
always had; whether refusing an unauthorised member in the wrong channel discloses more than
it should is a separate question, tracked in issue #145, and is not settled here.

**Before the bot is set up.** With no `ServerConfig` there is no channel to check and no role
anyone can hold, so only `bot_setup_only` proceeds — on the Administrator permission, which
is the only thing that can exist at that point. Every other command is refused and told to
run `/bot-init`. The guard this replaced let such a command through untouched.

**A refusal names a role, never mentions one.** A refusal that pinged the league admin role
would notify every holder of it each time somebody mistyped a command.
"""

from __future__ import annotations

import functools
import logging
from typing import Callable, Any

import discord
from discord import Interaction

# `app_commands` is imported for its side effect on *other* modules, and removing it as an
# unused import breaks every command whose signature names it.
#
# `functools.wraps` gives each guard's wrapper the identity of the function it wraps, but not
# its globals: the wrapper is defined here, so `wrapper.__globals__` is this module's. Cogs
# run under `from __future__ import annotations`, so a parameter annotated
# `app_commands.Range[int, 1, 10]` reaches discord.py as the *string* "app_commands.Range[...]",
# and `_extract_parameters_from_callback` resolves it against `callback.__globals__` — this
# namespace. A name a command signature uses must therefore be importable here, however
# unused it looks. Pinned by `test_a_command_annotation_resolves_against_this_module`.
from discord import app_commands  # noqa: F401

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The two tiers
# ---------------------------------------------------------------------------

#: The tier a decorated command sits in, readable off its callback. Set by the three
#: decorators below and asserted in bulk by `tests/unit/test_command_tiers.py`, which is
#: what stops a new command shipping without a tier or drifting into the wrong one.
TIER_ATTRIBUTE = "__league_tier__"

#: Marks a command exempt from the interaction-channel rule. Only `bot_setup_only` sets it.
CHANNEL_EXEMPT_ATTRIBUTE = "__league_channel_exempt__"

LEAGUE_ADMIN = "league admin"
LEAGUE_MANAGER = "league manager"

_NOT_SET_UP = (
    "⛔ This server has not been set up yet. A server administrator must run `/bot-init`."
)
_NOT_IN_A_SERVER = "⛔ This command can only be used inside a server."
_WRONG_CHANNEL = "⛔ This command can only be used in the configured interaction channel."
_NO_ADMIN_ROLE = (
    "⛔ No league admin role is configured, so nobody holds the tier this command asks "
    "for. Somebody with Discord's **Administrator** permission can set one with "
    "`/bot-admin-role`, from any channel."
)


def _role_name(guild: Any, role_id: int | None, fallback: str) -> str:
    """The role's name for a refusal message.

    Its *name*, never its mention: a refusal that pinged the role would notify every holder
    of it each time somebody mistyped a command. Falls back to a description where the role
    has been deleted from the server, which is exactly when a member is most likely to meet
    one of these refusals.
    """
    if guild is None or role_id is None:
        return fallback
    role = guild.get_role(role_id)
    return f"**{role.name}**" if role is not None else fallback


def _member_of(interaction: Interaction) -> discord.Member | None:
    """The invoking member, or None where the interaction did not come from a server."""
    user = interaction.user
    return user if isinstance(user, discord.Member) else None


def has_administrator(member: discord.Member) -> bool:
    """Whether *member* holds Discord's Administrator permission.

    Consulted only by `bot_setup_only`. Everywhere else the tiers are roles.
    """
    return bool(member.guild_permissions.administrator)


def is_league_admin(config: Any, member: discord.Member) -> bool:
    """Whether *member* holds the league admin tier.

    False where the league has configured no admin role — there is no such thing as
    holding a tier nobody has defined, and inventing a fallback to a Discord permission is
    the conflation this model removes.
    """
    role_id = getattr(config, "league_admin_role_id", None)
    if role_id is None:
        return False
    return any(role.id == role_id for role in member.roles)


def is_league_manager(config: Any, member: discord.Member) -> bool:
    """Whether *member* holds the league manager tier.

    The interaction role, or the league admin role — a league admin can do everything a
    league manager may, so the higher tier carries the lower within it and no member needs
    both roles.
    """
    if is_league_admin(config, member):
        return True
    role_id = getattr(config, "interaction_role_id", None)
    if role_id is None:
        return False
    return any(role.id == role_id for role in member.roles)


def may_set_up_bot(config: Any, member: discord.Member) -> bool:
    """Whether *member* may change the bot's four settings.

    The league admin role, or Discord's Administrator permission. The permission is
    admitted here and nowhere else: these are the commands that repair the settings every
    other guard reads, so a league whose admin role was deleted — or which has not chosen
    one yet — must still have a way back.
    """
    if has_administrator(member):
        return True
    return is_league_admin(config, member)


async def _refuse(interaction: Interaction, message: str) -> None:
    await interaction.response.send_message(message, ephemeral=True)


def _tier_guard(tier: str) -> Callable[[Callable], Callable]:
    """Build one of the two channel-bound tier decorators."""

    def decorate(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(self: Any, interaction: Interaction, *args: Any, **kwargs: Any) -> None:
            config = await self.bot.config_service.get_server_config(interaction.guild_id)
            if config is None:
                await _refuse(interaction, _NOT_SET_UP)
                return

            if interaction.channel_id != config.interaction_channel_id:
                log.warning(
                    "%s: command /%s blocked — wrong channel (channel_id=%s, expected=%s) "
                    "by user %s (id=%s) in guild %s",
                    tier,
                    func.__name__,
                    interaction.channel_id,
                    config.interaction_channel_id,
                    interaction.user,
                    interaction.user.id,
                    interaction.guild_id,
                )
                await _refuse(interaction, _WRONG_CHANNEL)
                return

            member = _member_of(interaction)
            if member is None:
                await _refuse(interaction, _NOT_IN_A_SERVER)
                return

            if tier == LEAGUE_ADMIN:
                if config.league_admin_role_id is None:
                    await _refuse(interaction, _NO_ADMIN_ROLE)
                    return
                if not is_league_admin(config, member):
                    name = _role_name(
                        interaction.guild, config.league_admin_role_id, "the league admin role"
                    )
                    await _refuse(
                        interaction,
                        f"⛔ This command is a league admin's. You need {name}.",
                    )
                    return
            elif not is_league_manager(config, member):
                manager = _role_name(
                    interaction.guild, config.interaction_role_id, "the interaction role"
                )
                admin = _role_name(
                    interaction.guild, config.league_admin_role_id, "the league admin role"
                )
                await _refuse(
                    interaction,
                    f"⛔ You don't have permission to use this command. "
                    f"You need {manager}, or {admin}.",
                )
                return

            await func(self, interaction, *args, **kwargs)

        setattr(wrapper, TIER_ATTRIBUTE, tier)
        setattr(wrapper, CHANNEL_EXEMPT_ATTRIBUTE, False)
        return wrapper

    return decorate


#: A league admin's command, given in the interaction channel.
league_admin_only = _tier_guard(LEAGUE_ADMIN)

#: A league manager's command, given in the interaction channel. A league admin passes it
#: too, holding the lower tier within the higher.
league_manager_only = _tier_guard(LEAGUE_MANAGER)


def bot_setup_only(func: Callable) -> Callable:
    """`/bot-init` and the four commands that change one setting each.

    A league admin's tier, but reachable by Discord's Administrator permission as well, and
    from any channel. Both exemptions have the same cause: these commands repair the very
    settings the other guards read, so gating them on those settings would lock a league out
    of the one failure the commands exist to fix.
    """

    @functools.wraps(func)
    async def wrapper(self: Any, interaction: Interaction, *args: Any, **kwargs: Any) -> None:
        member = _member_of(interaction)
        if member is None:
            await _refuse(interaction, _NOT_IN_A_SERVER)
            return

        # Read before the permission check because the league admin role is one of the
        # things being repaired: a server with no configuration at all has no role for
        # anyone to hold, and the Administrator permission is the only way in.
        config = await self.bot.config_service.get_server_config(interaction.guild_id)

        if not may_set_up_bot(config, member):
            name = _role_name(
                getattr(interaction, "guild", None),
                getattr(config, "league_admin_role_id", None),
                "the league admin role",
            )
            await _refuse(
                interaction,
                f"⛔ You need {name}, or Discord's **Administrator** permission, "
                f"to change the bot's settings.",
            )
            return

        await func(self, interaction, *args, **kwargs)

    setattr(wrapper, TIER_ATTRIBUTE, LEAGUE_ADMIN)
    setattr(wrapper, CHANNEL_EXEMPT_ATTRIBUTE, True)
    return wrapper
