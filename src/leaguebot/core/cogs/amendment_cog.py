"""AmendmentCog — retired in 010-command-streamlining.

The /round amend command has been migrated to SeasonCog's 'round' group.
This module is kept as an empty stub to avoid import errors.
"""

from __future__ import annotations

from discord.ext import commands
from leaguebot.core.utils.league_bot import LeagueBot


class AmendmentCog(commands.Cog):
    """Retired stub — all logic moved to SeasonCog.round group."""

    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot
