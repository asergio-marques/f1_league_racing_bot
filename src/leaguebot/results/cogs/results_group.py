"""The `/results` group, and what the subgroups beneath it share.

`RESULTS_GROUP` is the one `/results` group; each subgroup's class names it as its parent, so the
subgroups sit under one group whichever file declares them. The session choices and the form
refusal are the words two or more of the subgroups use.
"""
from __future__ import annotations

from abc import abstractmethod

import discord
from discord import app_commands

from leaguebot.core.utils.league_bot import LeagueBot

RESULTS_GROUP = app_commands.Group(name="results", description="Results & Standings commands")

SESSION_CHOICES = [
    app_commands.Choice(name="Sprint Qualifying", value="SPRINT_QUALIFYING"),
    app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
    app_commands.Choice(name="Feature Qualifying", value="FEATURE_QUALIFYING"),
    app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
]

RACE_SESSION_CHOICES = [
    app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
    app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
]

#: A form's refusal when the results module has been switched off since it was shown.
MODULE_OFF = "\u274c The Results & Standings module is not enabled on this server."


class ResultsSubgroup:
    """What a `/results` subgroup's class takes from the `ResultsCog` it is composed into.

    A subgroup's commands read the bot and call the cog's gates, which `ResultsCog` holds, so
    the class declares them here for the type checker. Stubs, never the gates themselves: a
    `Protocol` cannot be a base beside `commands.Cog`, whose metaclass it conflicts with, and
    typing `self` in every command would put a parameter annotation on each. `ResultsCog`
    comes first in the order methods are found in, so what it defines is what runs.
    """

    bot: LeagueBot

    @abstractmethod
    async def _module_gate(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Whether the results module is on; where it is not, refuse and return False."""

    @abstractmethod
    async def _answer_nothing_changed(
        self,
        interaction: discord.Interaction,
        command: str,
        reply: str,
        *details: str,
    ) -> None:
        """Answer a request that changes nothing, and record it in the log channel."""

    @abstractmethod
    async def _sync_gate(
        self, interaction: discord.Interaction, div, *, what: str
    ) -> bool:
        """Whether a division may be synced; where an amendment is open, refuse and return False."""
