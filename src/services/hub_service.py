"""The hub (issue #279): one channel every member of the league may use, and one panel in it.

Every command the bot has is a league manager's or a league admin's. The hub is the one
surface for everybody else: a channel set by `/bot hub-channel`, holding a single panel of
buttons. A button asks no tier; who may see the channel is who may press it.

**Core owns the channel and the panel; each module says what it adds.** A module registers a
`HubOption` at import, naming when it is offered — typically while the module is enabled — and
what a press does. Core knows none of them by name. The panel ships empty (decided
2026-09-22): nothing is registered here, and the panel says so until a module adds an option.

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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import discord

from utils.league_server import LeagueView

log = logging.getLogger(__name__)

__all__ = [
    "HubOption",
    "HubPanelView",
    "CUSTOM_ID_PREFIX",
    "register_option",
    "registered_options",
    "offered_options",
    "render_panel",
]

#: Every hub button's custom id starts so; the option's key follows.
CUSTOM_ID_PREFIX = "hub:"

PANEL_HEADING = "🏁 **League hub**"
PANEL_EMPTY = "Nothing is offered here yet."
PANEL_OFFERING = "Press an option below."
NO_LONGER_OFFERED = "⛔ That option is no longer offered here."


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
        return
    await option.respond(interaction)
