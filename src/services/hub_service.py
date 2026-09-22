"""The hub (issue #279): one channel every member of the league may use, and one panel in it.

Every command the bot has is a league manager's or a league admin's. The hub is the one
surface for everybody else: a channel set by `/bot hub-channel`, holding a single panel of
buttons. A button asks no tier; who may see the channel is who may press it.

**Core owns the channel and the panel; each module says what it adds.** A module registers a
`HubOption` at import, naming when it is offered — typically while the module is enabled — and
what a press does. This service knows none of them by name and registers nothing itself. Core
adds one option of its own, About, from `services/about_service.py` and offered always (decided
2026-09-22, #258); every other option is a module's. A panel offering nothing says so.

**A press is judged when it is made, not when the panel was posted.** A panel is refreshed
whenever what it offers may have changed, but a press can still arrive on one posted before a
module was disabled. The option is looked up by its key and asked again whether it is offered;
one that is not is refused, and the panel refreshed. The key is part of the button's custom id,
`hub:<key>`, and so must never change once an option has shipped.

**One persistent view carries every registered option.** Registered at start-up by
`recover_hub`, so a button survives a restart whether or not its option is offered at that
moment: the refusal above is the better answer to a stale press than silence.

**The channel is read-only to all but the bot.** The base role — or every member, where the
league has set none — and both tier roles may see it and press its buttons; nobody but the bot
may post there, so the panel is never pushed out of sight. The overwrites replace the channel's
own, as the signup channel's do, and are applied again whenever one of the three roles changes.
A base role that is set but has been deleted from the server keeps the hub **closed**, and says
so: opening it to every member would read a deleted role as a league that chose to have none.

**The panel is refreshed, never assumed.** `refresh_panel` edits the panel in place where it
stands and posts it again where it has been deleted, keeping the new message's id. It runs when
the hub is set, when a module is enabled or disabled, at start-up, and after a stale press. A
lock keeps two refreshes from both finding the panel missing and posting two.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import discord

from utils.league_server import LeagueView, league_guild

log = logging.getLogger(__name__)

__all__ = [
    "HubOption",
    "HubPanelView",
    "CUSTOM_ID_PREFIX",
    "register_option",
    "registered_options",
    "offered_options",
    "render_panel",
    "refresh_panel",
    "apply_hub_permissions",
    "reapply_hub_permissions",
    "recover_hub",
]

#: Every hub button's custom id starts so; the option's key follows.
CUSTOM_ID_PREFIX = "hub:"

PANEL_HEADING = "🏁 **League hub**"
PANEL_EMPTY = "Nothing is offered here yet."
PANEL_OFFERING = "Press an option below."
NO_LONGER_OFFERED = "⛔ That option is no longer offered here. The panel has been refreshed."


@dataclass(frozen=True)
class HubOption:
    """One button of the hub's panel, and what pressing it does.

    *key* is the button's identity, carried in its custom id: stable for as long as the option
    exists. *order* places it on the panel, lowest first, the key settling a tie. *offered* is
    asked each time the panel is drawn and each time the button is pressed; ``None`` offers it
    always. *respond* answers a press, which has passed the league-server check already.
    """

    key: str
    label: str
    order: int
    respond: Callable[[discord.Interaction], Awaitable[None]]
    offered: Callable[[Any], Awaitable[bool]] | None = None


_OPTIONS: dict[str, HubOption] = {}


def register_option(option: HubOption) -> None:
    """Add *option* to the panel's registry. A second option under one key is refused.

    Registering the same option twice is harmless, as a module imported twice would do; two
    options sharing a key would route one's presses to the other.
    """
    existing = _OPTIONS.get(option.key)
    if existing is not None and existing is not option:
        raise ValueError(f"a hub option is already registered as {option.key!r}")
    _OPTIONS[option.key] = option


def registered_options() -> list[HubOption]:
    """Every registered option, in panel order."""
    return sorted(_OPTIONS.values(), key=lambda option: (option.order, option.key))


async def is_offered(bot: Any, option: HubOption) -> bool:
    return option.offered is None or bool(await option.offered(bot))


async def offered_options(bot: Any) -> list[HubOption]:
    """The options the panel carries now, in panel order."""
    return [option for option in registered_options() if await is_offered(bot, option)]


def render_panel(options: list[HubOption]) -> tuple[str, "HubPanelView | None"]:
    """The panel's text, and its view — ``None`` where nothing is offered."""
    if not options:
        return f"{PANEL_HEADING}\n{PANEL_EMPTY}", None
    return f"{PANEL_HEADING}\n{PANEL_OFFERING}", HubPanelView(options)


class HubPanelView(LeagueView):
    """The hub's panel: one button per option, persistent, dispatching by the option's key.

    Built with the options offered when the panel is drawn, and at start-up with every option
    registered, so that each key is routed after a restart.
    """

    def __init__(self, options: list[HubOption]) -> None:
        super().__init__(timeout=None)
        for option in options:
            button: discord.ui.Button = discord.ui.Button(
                label=option.label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"{CUSTOM_ID_PREFIX}{option.key}",
            )
            button.callback = _callback_for(option.key)
            self.add_item(button)


def _callback_for(key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
    async def callback(interaction: discord.Interaction) -> None:
        await press(interaction, key)

    return callback


async def press(interaction: discord.Interaction, key: str) -> None:
    """Answer a press on the button keyed *key*, judging the option as it stands now."""
    bot = interaction.client
    option = _OPTIONS.get(key)
    if option is None or not await is_offered(bot, option):
        await interaction.response.send_message(NO_LONGER_OFFERED, ephemeral=True)
        fault = await refresh_panel(bot)
        if fault is not None:
            log.warning("hub: %s", fault)
        return
    await option.respond(interaction)


# ── Posting and refreshing the panel ──────────────────────────────────────

_REFRESH_LOCK = asyncio.Lock()


async def _hub_channel(bot: Any) -> tuple[Any, discord.Guild | None, Any, str | None]:
    """The server configuration, the league's guild, the hub channel and a fault, where any.

    The channel is None, with no fault, where no hub is set or the league's server is not to
    hand; None with a fault where the channel set is no longer in the server.
    """
    cfg = await bot.config_service.get_server_config()
    if cfg is None or cfg.hub_channel_id is None:
        return cfg, None, None, None
    guild = await league_guild(bot)
    if guild is None:
        return cfg, None, None, None
    channel = guild.get_channel(cfg.hub_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return cfg, guild, None, (
            f"The hub channel (id {cfg.hub_channel_id}) is not in the server."
        )
    return cfg, guild, channel, None


async def refresh_panel(bot: Any) -> str | None:
    """Bring the panel in line with what is offered now. Returns a line for the log, or None.

    Edits the panel where it stands; posts it, and keeps its id, where there is none or it has
    been deleted. Nothing is done while no hub channel is set.
    """
    async with _REFRESH_LOCK:
        cfg, _guild, channel, fault = await _hub_channel(bot)
        if channel is None:
            return fault
        content, view = render_panel(await offered_options(bot))
        if cfg.hub_message_id is not None:
            try:
                await channel.get_partial_message(cfg.hub_message_id).edit(
                    content=content, view=view
                )
                return None
            except discord.NotFound:
                pass  # deleted by hand: posted again below
            except discord.HTTPException as exc:
                return f"The hub panel in <#{channel.id}> could not be updated: {exc}"
        try:
            message = await channel.send(
                content, view=view, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException as exc:
            return f"The hub panel could not be posted in <#{channel.id}>: {exc}"
        await bot.config_service.set_core_setting("hub_message_id", message.id)
        return None


# ── Who may see the hub ───────────────────────────────────────────────────


async def apply_hub_permissions(bot: Any, guild: discord.Guild, channel: Any) -> str | None:
    """Make *channel* the hub's: read-only to all but the bot. Returns a fault, or None.

    Replaces the channel's own overwrites. See the module docstring for who may see it, and
    for why a base role deleted from the server keeps the hub closed.
    """
    cfg = await bot.config_service.get_server_config()
    base_role_id = cfg.base_role_id if cfg is not None else None
    base_role = guild.get_role(base_role_id) if base_role_id is not None else None
    fault = None
    if base_role_id is not None and base_role is None:
        fault = (
            f"The base role (id {base_role_id}) is not in the server, so the hub <#{channel.id}> "
            f"is closed to members until `/bot base-role` sets one."
        )

    read_only = discord.PermissionOverwrite(view_channel=True, send_messages=False)
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=base_role_id is None, send_messages=False
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True
        ),
    }
    if base_role is not None:
        overwrites[base_role] = read_only
    if cfg is not None:
        for role_id in (cfg.interaction_role_id, cfg.league_admin_role_id):
            role = guild.get_role(role_id) if role_id else None
            if role is not None:
                overwrites[role] = read_only
    try:
        await channel.edit(overwrites=overwrites)
    except discord.HTTPException as exc:
        return f"The hub <#{channel.id}>'s permissions could not be set: {exc}"
    return fault


async def reapply_hub_permissions(bot: Any) -> str | None:
    """Apply the hub's permissions again, after one of the roles they name has changed."""
    _cfg, guild, channel, fault = await _hub_channel(bot)
    if channel is None:
        return fault
    return await apply_hub_permissions(bot, guild, channel)


# ── Start-up ──────────────────────────────────────────────────────────────


async def recover_hub(bot: Any) -> str | None:
    """Route every registered option's button after a restart, and refresh the panel.

    Every option is registered, not only those offered now: a press on one a module has
    since stopped offering is refused in words rather than failing in silence.
    """
    options = registered_options()
    if options:
        bot.add_view(HubPanelView(options))
    return await refresh_panel(bot)
