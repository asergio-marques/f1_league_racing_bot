"""The hub's About option (issue #258): which bot this is, and which build of it is running.

A league member reporting a problem had no way to say which build they saw, nor to find the
project the bot comes from. About answers both, from the hub's panel, to whoever presses it.

**Core's one option** (decided 2026-09-22). Every other option of the hub's panel is a
module's, offered while its module is enabled; About belongs to no module and is offered
always. It sits after every module's option, `ABOUT_ORDER` being above any a module uses.

**The version and when it was made are the ones read at start-up** (#370), held on
`bot.running_version` and `bot.running_version_date`. A press never reads `VERSION` nor asks
git: the answer describes the code this process loaded, which a checkout updated underneath a
running bot would not. Where the version could not be told, the answer says so; where its date
could not, the line is left out. The date is a Discord timestamp, so each member reads it in
their own time zone.

**Answered to the presser alone.** The hub receives nothing but its panel, so a public answer
there would break the channel's one purpose; and a press changes nothing, so nothing is
recorded in the log channel.

**`ABOUT_KEY` never changes.** It is part of the button's custom id, and a panel already
posted carries it.
"""
from __future__ import annotations

from datetime import datetime

import discord

from leaguebot.core.services.hub_service import HubOption, register_option
from leaguebot.core.utils.league_bot import bot_of

PROJECT_NAME = "F1 League Racing Bot"
REPOSITORY_URL = "https://github.com/asergio-marques/f1_league_racing_bot"

ABOUT_KEY = "about"
ABOUT_LABEL = "About"
#: After every module's option: modules place theirs below this.
ABOUT_ORDER = 1000


def about_text(version: str | None, made: datetime | None = None) -> str:
    """The answer to a press: the bot's name, the version running, when it was made and where
    it comes from.

    The date is a full Discord timestamp, shown in the reader's own time zone, and is left out
    where it is not known. The link is in angle brackets, so Discord shows no preview beneath it.
    """
    lines = [f"**{PROJECT_NAME}**", f"Version: {version or 'unknown'}"]
    if made is not None:
        lines.append(f"Dated: <t:{int(made.timestamp())}:F>")
    lines.append(f"Source: <{REPOSITORY_URL}>")
    return "\n".join(lines)


async def respond(interaction: discord.Interaction) -> None:
    """Answer a press on About, to the presser alone."""
    bot = bot_of(interaction)
    await interaction.response.send_message(
        about_text(
            getattr(bot, "running_version", None), getattr(bot, "running_version_date", None)
        ),
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
