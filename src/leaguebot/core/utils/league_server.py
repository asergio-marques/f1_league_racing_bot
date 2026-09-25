"""One bot serves one league, on one Discord server — and this is where that is enforced.

**The league's server is the one `server_configs` row.** The first `/bot init` claims it, and
`ConfigService.save_server_config` refuses to claim a second, so whichever server was set up
first is the league's until `/bot pack` clears the claim again (issue #247). No setting names
the server in advance: a bot that is invited only where its host puts it — Discord's *Public Bot*
switch off, as the README requires — never meets a second server anyway, and this is the
safeguard for the mistake, not the mechanism a league relies on.

**The server is checked here, once, and nowhere after.** Every slash command passes through
`LeagueCommandTree.interaction_check` before its body runs, and every event listener asks
`is_foreign_guild` before it reads anything; `tests/unit/test_one_league_server.py` holds each
new listener to it. Past that point nothing in the bot scopes by
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

**Before any server is set up**, or after `/bot pack` has freed the claim, no server is
foreign, and every command falls through to the tier guards — which refuse all but `/bot init`
and its four setting commands, as they always have. Buttons and forms are refused outright
while no server is claimed; see `LeagueView`.

**The same three classes are where a failure is answered.** A command, button or form that
raises is reported to its member, the log channel and the host's log by
`leaguebot.core.utils.interaction_errors.report_failure`, which each class's `on_error` calls (issue #156).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from leaguebot.core.utils.interaction_errors import describe, describe_form, report_failure
from leaguebot.core.utils.league_bot import LeagueBot, bot_of

log = logging.getLogger(__name__)

REFUSAL = "⛔ This bot serves another server's league and takes no commands here."

#: What a button or form is told while the bot serves no server at all (issue #247).
UNCLAIMED_REFUSAL = (
    "⛔ This bot is between servers and acts on nothing until `/bot init` claims one."
)


async def is_foreign_guild(bot: LeagueBot, guild_id: int | None) -> bool:
    """Whether *guild_id* is a server other than the league's.

    False outside a server (a DM carries no guild, and the tier guards refuse it themselves)
    and False while no league is set up, so that `/bot init` can reach the server that will
    become the league's.
    """
    if guild_id is None:
        return False
    league = await bot.config_service.get_league_server_id()
    return league is not None and guild_id != league


def guild_of(interaction: discord.Interaction) -> discord.Guild:
    """The server *interaction* came from, which by the time a command body asks is the league's.

    Every command body runs behind a tier guard, and a guard admits only a member, in the
    league's command channel — so an interaction that reaches a body has a server. discord.py
    types it as optional because an interaction in general need not. A None here means a body
    ran with no guard in front of it, and that is raised by name rather than left to surface
    as an ``AttributeError`` on ``None`` somewhere further in (#228).
    """
    guild = interaction.guild
    if guild is None:
        command = getattr(interaction.command, "qualified_name", None)
        what = f"/{command}" if command else "An interaction"
        raise RuntimeError(
            f"{what} reached its body outside a server: it needs a guard that refuses a "
            "direct message"
        )
    return guild


def channel_id_of(interaction: discord.Interaction) -> int:
    """The id of the channel *interaction* came from.

    A command, a press or a submission always comes from a channel. discord.py types the id as
    optional because an interaction in general need not carry one, so a None here is raised by
    name rather than stored as a key or looked up (#228).
    """
    channel_id = interaction.channel_id
    if channel_id is None:
        raise RuntimeError("An interaction arrived from no channel")
    return channel_id


async def league_guild(bot: LeagueBot) -> discord.Guild | None:
    """The league's Discord server, or None where none is set up or it is not in the cache.

    The one route from a scheduled job or a restart to the guild: the id lives in
    `server_configs` and nowhere else, so nothing reads it off a data row.
    """
    league = await bot.config_service.get_league_server_id()
    return None if league is None else bot.get_guild(league)


async def admits(
    client: LeagueBot, interaction: discord.Interaction, *, while_unclaimed: bool = True
) -> bool:
    """Whether *interaction* may proceed: True unless it comes from a server not the league's.

    *while_unclaimed* is whether it may proceed while no server is claimed. The command tree
    passes True, so that `/bot init` can reach the server that will become the league's;
    the views and modals pass False — see `LeagueView`.

    A refused interaction is answered with `REFUSAL` or `UNCLAIMED_REFUSAL`, seen by its
    member alone — save an autocomplete, to which Discord accepts no message.
    """
    league = await client.config_service.get_league_server_id()
    if league is None:
        # A direct message is refused too, where a view asks: a press in one acts on the
        # league's data as surely as a press in a server does.
        if while_unclaimed:
            return True
        message = UNCLAIMED_REFUSAL
        log.info(
            "refused an interaction from server %s while no server is claimed (user %s)",
            interaction.guild_id,
            getattr(interaction.user, "id", None),
        )
    elif interaction.guild_id is None or interaction.guild_id == league:
        # Outside a server the tier guards, or the view's own checks, decide.
        return True
    else:
        message = REFUSAL
        log.info(
            "refused an interaction from server %s, which is not the league's (user %s)",
            interaction.guild_id,
            getattr(interaction.user, "id", None),
        )
    if interaction.type is not discord.InteractionType.autocomplete:
        await interaction.response.send_message(message, ephemeral=True)
    return False


class LeagueCommandTree(app_commands.CommandTree[LeagueBot]):
    """The command tree, refusing every command from a server that is not the league's, and
    answering every command that fails."""

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await admits(self.client, interaction)

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError, /
    ) -> None:
        command = interaction.command
        if command is not None and command._has_any_error_handlers():
            # A command that handles its own errors has answered already, as the library's
            # own handler assumes.
            return
        await report_failure(interaction, error, what=describe(interaction))


class LeagueView(discord.ui.View):
    """The base of every view the bot posts, refusing a press from a server not the league's.

    Discord asks a view's `interaction_check` before any of its buttons or menus runs. A view
    that overrides it must call this one first.

    **While no server is claimed, every press is refused** (decided 2026-09-19, issue #247).
    Between `/bot pack` and the next `/bot init` no server is foreign, and the buttons the
    bot left on the server it moved from would otherwise act on the league's data from
    there. Before the first `/bot init` the bot has posted nothing, so nothing is lost.
    """

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await admits(bot_of(interaction), interaction, while_unclaimed=False)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item, /
    ) -> None:
        await report_failure(interaction, error, what=describe(interaction, item))


class LeagueModal(discord.ui.Modal):
    """The base of every modal the bot shows, refusing a submission from a server not the
    league's, as `LeagueView` refuses a press."""

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await admits(bot_of(interaction), interaction, while_unclaimed=False)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item | None = None,
        /,
    ) -> None:
        # A modal's failure names no item, and discord.py passes none. `item` is declared
        # because discord.py's own `Modal` narrows `BaseView.on_error`, and a subclass has to
        # satisfy both (#228).
        await report_failure(interaction, error, what=describe_form(self))


#: What a button or a menu built at runtime does when it is used.
Handler = Callable[[discord.Interaction], Awaitable[None]]


class CallbackButton(discord.ui.Button):
    """A button built at runtime, pressed through the handler it is given.

    discord.py's own way to give a button made in a loop its behaviour is to assign to its
    ``callback`` — a method, which the type check cannot follow an assignment to, and so could
    not check the handler against (#228). This takes the handler when the button is made and
    calls it from the ``callback`` discord.py calls: the same button, pressed the same way.
    """

    def __init__(
        self,
        *,
        on_press: Handler,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        label: str | None = None,
        disabled: bool = False,
        custom_id: str | None = None,
        row: int | None = None,
    ) -> None:
        super().__init__(
            style=style, label=label, disabled=disabled, custom_id=custom_id, row=row
        )
        self._on_press = on_press

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_press(interaction)


class CallbackSelect(discord.ui.Select):
    """A menu built at runtime, answered through the handler it is given, as `CallbackButton`."""

    def __init__(
        self,
        *,
        on_choose: Handler,
        options: list[discord.SelectOption],
        placeholder: str | None = None,
        min_values: int = 1,
        max_values: int = 1,
        row: int | None = None,
    ) -> None:
        super().__init__(
            options=options,
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
            row=row,
        )
        self._on_choose = on_choose

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_choose(interaction)


def warn_if_serving_several(bot: LeagueBot) -> None:
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
