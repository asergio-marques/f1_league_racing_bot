"""The hub's About option (issue #258): which bot this is, and which build of it is running.

A league member reporting a problem had no way to say which build they saw, nor to find the
project the bot comes from. About answers both, from the hub's panel, to whoever presses it.

**Core's one option** (decided 2026-09-22). Every other option of the hub's panel is a
module's, offered while its module is enabled; About belongs to no module and is offered
always. It sits after every module's option, `ABOUT_ORDER` being above any a module uses.

**The version is the one read at start-up** (#370), held on `bot.running_version`. A press
never reads `VERSION` nor asks git: the answer describes the code this process loaded, which
a checkout updated underneath a running bot would not. Where the version could not be told,
the answer says so.

**Answered to the presser alone.** The hub receives nothing but its panel, so a public answer
there would break the channel's one purpose; and a press changes nothing, so nothing is
recorded in the log channel.

**`ABOUT_KEY` never changes.** It is part of the button's custom id, and a panel already
posted carries it.
"""
from __future__ import annotations

from typing import Any

import discord

from services.hub_service import HubOption, register_option

PROJECT_NAME = "F1 League Racing Bot"
REPOSITORY_URL = "https://github.com/asergio-marques/f1_league_racing_bot"

ABOUT_KEY = "about"
ABOUT_LABEL = "About"
#: After every module's option: modules place theirs below this.
ABOUT_ORDER = 1000


def about_text(version: str | None) -> str:
    """The answer to a press: the bot's name, the version running and where it comes from.

    The link is in angle brackets, so Discord shows no preview beneath it.
    """
    return (
        f"**{PROJECT_NAME}**\n"
        f"Version: {version or 'unknown'}\n"
        f"Source: <{REPOSITORY_URL}>"
    )


async def respond(interaction: discord.Interaction) -> None:
    """Answer a press on About, to the presser alone."""
    bot: Any = interaction.client
    await interaction.response.send_message(
        about_text(getattr(bot, "running_version", None)),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


ABOUT_OPTION = HubOption(
    key=ABOUT_KEY,
    label=ABOUT_LABEL,
    order=ABOUT_ORDER,
    respond=respond,
    offered=None,
)

register_option(ABOUT_OPTION)
