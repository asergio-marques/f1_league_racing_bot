"""channels.py - what the bot's configuration guarantees of a channel it looks up.

The framework owns this because the cast rests on a guarantee of the bot's own configuration, not
on anything true of channels in general.
"""

from __future__ import annotations

from typing import cast

import discord


def as_text_channel(channel: object) -> discord.TextChannel | None:
    """*channel*, looked up by an id the league configured, as the text channel it is.

    Every channel the bot is configured with is set by a command whose parameter is a
    ``discord.TextChannel`` — one per setting — and the only channels the bot makes are text
    channels, so one resolved from a stored id is a text channel by construction. discord.py
    types a lookup by id as any kind of channel, since it cannot know that; this says what the
    configuration guarantees, once, rather than at every post. None stays None: a channel deleted
    since it was set is the caller's to report.

    A cast rather than an ``isinstance`` check. The tests hand stand-in channels through every
    path that posts, and a check would quietly turn each of them into a missing channel.
    """
    return cast("discord.TextChannel | None", channel)
