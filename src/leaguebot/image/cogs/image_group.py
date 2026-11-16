"""The `/images` group, the four subgroups beneath it, and what the subgroups share.

`IMAGES_GROUP` is the one `/images` group, and its four subgroups are declared here beneath it,
in the order a league sees them (`config`, `template`, `use-pfp`, `test`); each subgroup's class
takes its own as a class attribute. A group's children are kept in the order they join it, and
Discord is sent them in that order, so declaring the subgroups in one file fixes the order
whichever subgroup's file is imported first. What else is here is what more than one of them
uses: the stub of what a subgroup takes from the cog it is composed into, and the division
autocomplete the per-tier colour commands and the previews both offer.
"""
from __future__ import annotations

from abc import abstractmethod

import discord
from discord import app_commands

from leaguebot.core.db.database import AUTOCOMPLETE_TIMEOUT_SECONDS
from leaguebot.framework.utils.autocomplete import bounded_autocomplete
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.image.services.config.image_config_service import ImageConfigService
from leaguebot.image.services.validity.image_validity_service import ImageValidityService

IMAGES_GROUP = app_commands.Group(
    name="images",
    description="Image module commands.",
    default_permissions=None,
)

# The sixteen template filename setters live in their own group rather than under
# `config`. Discord allows at most 25 subcommands per group and forbids a third
# nesting level, and `config` would otherwise carry 35: 1 template directory +
# 16 filenames + 8 asset directories + 4 preferences + 4 per-tier + toggle + view.
#
# As split, `config` carries nineteen of the 25 and `template` the other 16. Each asset class
# added costs `config` one and each template costs `template` one, so the arithmetic is
# worth redoing rather than assuming.
CONFIG_GROUP = app_commands.Group(
    name="config",
    description="Configure image generation settings.",
    parent=IMAGES_GROUP,
)
TEMPLATE_GROUP = app_commands.Group(
    name="template",
    description="Set which SVG file backs each kind of image.",
    parent=IMAGES_GROUP,
)
USE_PFP_GROUP = app_commands.Group(
    name="use-pfp",
    description="Obtain driver portraits from Discord profile pictures.",
    parent=IMAGES_GROUP,
)
# One command per image kind, each drawn against the league's own division and, where
# the kind pertains to one, its own round. Discord allows a group of subcommands
# beneath a top-level command and no further nesting, which is the depth `config` and
# `template` already use.
TEST_GROUP = app_commands.Group(
    name="test",
    description="Preview an image against your own league's configuration.",
    parent=IMAGES_GROUP,
)


class ImageSubgroup:
    """What an `/images` subgroup's class takes from the `ImageCog` it is composed into.

    A subgroup's commands read the bot and call the cog's gate and its reply, which `ImageCog`
    holds, so the class declares them here for the type checker. Stubs, never the gate itself: a
    `Protocol` cannot be a base beside `commands.Cog`, whose metaclass it conflicts with, and
    typing `self` in every command would put a parameter annotation on each. `ImageCog` comes
    first in the order methods are found in, so what it defines is what runs.

    The division autocomplete is here for real, not as a stub, because two subgroups complete a
    division the same way and each takes it into its own class body. It stays a method of a
    class, never a function of the module: discord.py asks whether a callback is inside a class
    by its qualified name, to know whether it is handed the cog.
    """

    bot: LeagueBot

    @property
    @abstractmethod
    def _config_service(self) -> ImageConfigService:
        """The image configuration service."""

    @property
    @abstractmethod
    def _validity_service(self) -> ImageValidityService:
        """The image validity service."""

    @abstractmethod
    async def _module_gate(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Whether the Image module is on; where it is not, answer and return False."""

    @staticmethod
    @abstractmethod
    async def _reply(interaction: discord.Interaction, content: str) -> None:
        """Send an ephemeral response, following up when already deferred."""

    @bounded_autocomplete()
    async def _division_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The divisions of whichever season a preview draws (FR-003).

        The approved season where there is one, the season pending approval otherwise —
        so a league can complete on its divisions before its placements are confirmed.
        A division of a completed or cancelled season is deliberately absent: a preview is
        a check on what the league is running or about to run.

        On a server holding no season this offers nothing, which is why the parameter is
        optional there: such a server draws a fabricated league and needs no name.
        """
        # One connection rather than two: the season lookup and the division list share
        # it, which halves the connect/PRAGMA/close cost on a path racing Discord's
        # three-second budget. The shorter lock wait means a contended database gives up
        # in time to answer rather than answering into an expired token. A fault here is
        # answered with no choices, and logged, by `bounded_autocomplete`.
        divisions = await self.bot.season_service.get_previewable_divisions(
            timeout=AUTOCOMPLETE_TIMEOUT_SECONDS
        )

        typed = (current or "").strip().casefold()
        return [
            app_commands.Choice(name=division.name, value=division.name)
            for division in divisions
            if typed in division.name.casefold()
        ][:25]
