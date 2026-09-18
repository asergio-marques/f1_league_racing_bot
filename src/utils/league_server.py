"""One bot serves one league, on one Discord server — and this is where that is enforced.

**The league's server is the one `server_configs` row.** The first `/bot-init` writes it, and
`ConfigService.save_server_config` refuses to write a second, so whichever server was set up
first is the league's until `/bot-reset full:True` deletes the row again. No setting names the
server in advance: a bot that is invited only where its host puts it — Discord's *Public Bot*
switch off, as the README requires — never meets a second server anyway, and this is the
safeguard for the mistake, not the mechanism a league relies on.

**The server is checked here, once, and nowhere after.** Every slash command passes through
`LeagueCommandTree.interaction_check` before its body runs, and the four event listeners ask
`is_foreign_guild` before they read anything. Past that point nothing in the bot scopes by
server: the tables carry no `server_id`, the services take none, and a query reads the
league's data because the database holds no other. A per-query scope that could never fail
would only suggest to a reader that it might (decided 2026-09-18, issue #244).

**Buttons, menus and modals are checked too**, by `LeagueView` and `LeagueModal`, which every
view and modal in the bot derives from. The tree does not see them, and "the bot only posts in
the league's channels" is not enough: the persistent views answer their custom ids on any
message, so once a league has moved to another server, every button left on the old one would
otherwise still act on the league's data. `tests/unit/test_one_league_server.py` holds that no
view or modal derives from discord.py's own classes directly.

**Upon another server the bot stays, and refuses.** It does not leave: leaving would make an
owner's mistake silent, where a refusal and the start-up warning below make it visible.
Autocomplete is refused by offering nothing, Discord accepting no message in reply to one.

**Before any server is set up**, no server is foreign, and every command falls through to the
tier guards — which refuse all but `/bot-init` and its four setting commands, as they always
have.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

log = logging.getLogger(__name__)

REFUSAL = "⛔ This bot serves another server's league and takes no commands here."


async def is_foreign_guild(bot: Any, guild_id: int | None) -> bool:
    """Whether *guild_id* is a server other than the league's.

    False outside a server (a DM carries no guild, and the tier guards refuse it themselves)
    and False while no league is set up, so that `/bot-init` can reach the server that will
    become the league's.
    """
    if guild_id is None:
        return False
    league = await bot.config_service.get_league_server_id()
    return league is not None and guild_id != league


async def league_guild(bot: Any) -> discord.Guild | None:
    """The league's Discord server, or None where none is set up or it is not in the cache.

    The one route from a scheduled job or a restart to the guild: the id lives in
    `server_configs` and nowhere else, so nothing reads it off a data row.
    """
    league = await bot.config_service.get_league_server_id()
    return None if league is None else bot.get_guild(league)


async def admits(client: Any, interaction: discord.Interaction) -> bool:
    """Whether *interaction* may proceed: True unless it comes from a server not the league's.

    A refused interaction is answered with `REFUSAL`, seen by its member alone — save an
    autocomplete, to which Discord accepts no message.
    """
    if not await is_foreign_guild(client, interaction.guild_id):
        return True
    log.info(
        "refused an interaction from server %s, which is not the league's (user %s)",
        interaction.guild_id,
        getattr(interaction.user, "id", None),
    )
    if interaction.type is not discord.InteractionType.autocomplete:
        await interaction.response.send_message(REFUSAL, ephemeral=True)
    return False


class LeagueCommandTree(app_commands.CommandTree):
    """The command tree, refusing every command from a server that is not the league's."""

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await admits(self.client, interaction)


class LeagueView(discord.ui.View):
    """The base of every view the bot posts, refusing a press from a server not the league's.

    Discord asks a view's `interaction_check` before any of its buttons or menus runs. A view
    that overrides it must call this one first.
    """

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await admits(interaction.client, interaction)


class LeagueModal(discord.ui.Modal):
    """The base of every modal the bot shows, refusing a submission from a server not the
    league's, as `LeagueView` refuses a press."""

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await admits(interaction.client, interaction)


def warn_if_serving_several(bot: Any) -> None:
    """Log a warning to the host when the bot sits in more than one server.

    A warning and nothing more: the refusal above already keeps the league's data to the
    league. This is what tells the host the bot was put somewhere it should not be.
    """
    guilds = sorted(bot.guilds, key=lambda guild: guild.id)
    if len(guilds) <= 1:
        return
    log.warning(
        "One bot serves one league, but this bot sits in %d servers: %s. Commands are "
        "refused everywhere but the league's own; remove the bot from the others.",
        len(guilds),
        ", ".join(f"{guild.name} ({guild.id})" for guild in guilds),
    )
