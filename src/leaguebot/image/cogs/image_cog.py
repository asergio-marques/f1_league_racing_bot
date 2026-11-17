"""`ImageCog`: the `/images` group, composed from one class for each of its subgroups.

The subgroups' commands live in `config_commands.py`, `template_commands.py`,
`use_pfp_commands.py` and `test_commands.py`, one file for each subgroup, under the one `/images`
group declared in `image_group.py`. The cog stays one cog for one group: it holds `/images` as a
class attribute, each subgroup's class takes its subgroup, declared beneath that group in
`image_group.py`, and discord.py binds a cog's group to the cog together with every child beneath
it, so every command runs with this cog's `self`. A cog too large to read in one file is split by
subgroup rather than into a cog for each, as the architecture's cog bullet gives it
(`docs/design/architecture.md`).

Every command here is a league manager's: `@league_manager_only` throughout. Naming a template
file or an asset directory used to ask for Discord's Administrator permission while the toggles
and the previews asked for Manage Server, a split that put the artwork a league draws with above
the league that draws it. Both are the configuration of a module, which the core specification
places at the league manager's tier.

Every response is ephemeral (FR-044), and every command refuses to act while the module is
disabled (FR-005). What is left here is what the subgroups share: the cog's bot, the gate their
commands call and the reply they send.
"""
from __future__ import annotations

import discord
from discord.ext import commands

# The subgroups join `/images` in `image_group.py`, in the order a league sees them, so the order
# of these imports does not matter.
from leaguebot.image.models.switch import SWITCH_KEY
from leaguebot.framework.utils.interaction_errors import describe as describe_command
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.log_lines import refuse
from leaguebot.utils.messages import chunk_message
from leaguebot.image.cogs.config_commands import ConfigCommands
from leaguebot.image.cogs.image_group import IMAGES_GROUP
from leaguebot.image.cogs.template_commands import TemplateCommands
from leaguebot.image.cogs.test_commands import TestCommands
from leaguebot.image.cogs.use_pfp_commands import UsePfpCommands
from leaguebot.image.models.image_constants import TEMPLATE_COMMAND_NAMES


class ImageCog(
    ConfigCommands,
    TemplateCommands,
    UsePfpCommands,
    TestCommands,
    commands.Cog,
):
    images = IMAGES_GROUP

    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    async def _module_gate(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Return True when the module is enabled; otherwise answer and return False.

        The refusal is recorded in the log channel, as every refusal of a command that changes
        something is. A command that changes nothing (`record=False`: the configuration report
        and the previews) answers and records nothing.

        Answers through `refuse` or :meth:`_reply`, never ``response.send_message`` directly,
        because its callers differ: a command that has already deferred must answer on the
        followup, and sending a fresh response there raises ``404 Unknown interaction``.
        """
        if await self.bot.module_service.is_enabled(SWITCH_KEY):
            return True
        reply = "❌ The Image module is not enabled. Use `/module enable images` first."
        if record:
            await refuse(interaction, reply, what=describe_command(interaction))
        else:
            await self._reply(interaction, reply)
        return False

    @property
    def _config_service(self):
        return self.bot.image_config_service

    @property
    def _validity_service(self):
        return self.bot.image_validity_service

    @staticmethod
    async def _reply(interaction: discord.Interaction, content: str) -> None:
        """Send an ephemeral response, following up when already deferred.

        A reply past Discord's limit goes in as many parts as it needs (`chunk_message`): sent
        whole, it raises after the change it reports has been made. The first part answers the
        interaction if nothing has, and the rest follow up on it.
        """
        for part in chunk_message(content):
            if interaction.response.is_done():
                await interaction.followup.send(part, ephemeral=True)
            else:
                await interaction.response.send_message(part, ephemeral=True)


def _verify_template_command_coverage() -> None:
    """Fail loudly at import if a template gains no command.

    The sixteen commands below are written out rather than generated, because
    discord.py resolves a command's parameters from the callback signature and cannot
    tell a class-external callback is a bound method. This check keeps the explicit list
    honest against `TEMPLATE_COLUMNS`, so adding a template to the constants without
    adding its command is a startup error rather than a silently missing command.
    """
    registered = {command.name for command in ImageCog.template.commands}
    expected = set(TEMPLATE_COMMAND_NAMES.values())
    missing = expected - registered
    if missing:
        raise RuntimeError(
            f"ImageCog is missing template commands: {', '.join(sorted(missing))}"
        )


def _verify_discord_group_limits() -> None:
    """Fail at import if a group would exceed Discord's 25-subcommand ceiling.

    Cheap insurance: the limit is enforced by Discord at command-sync time, which is far
    from the edit that broke it. This turns that into an immediate startup error.
    """
    for group in (ImageCog.images, ImageCog.config, ImageCog.template, ImageCog.test):
        count = len(group.commands)
        if count > 25:
            raise RuntimeError(
                f"/{group.name} has {count} subcommands; Discord allows at most 25."
            )


_verify_template_command_coverage()
_verify_discord_group_limits()
