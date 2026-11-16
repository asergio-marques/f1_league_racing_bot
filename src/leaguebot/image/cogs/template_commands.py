"""`TemplateCommands`: the `/images template` subgroup, the sixteen template filename commands.

Each takes the filename of an SVG inside the configured template directory, and all sixteen
delegate to `_set_template_filename`, which holds the whole body. The folder itself is set by
`/images config template-directory`, in `config_commands.py`.
"""
from __future__ import annotations

import discord
from discord import app_commands

from leaguebot.core.utils.channel_guard import league_manager_only
from leaguebot.core.utils.interaction_errors import describe as describe_command
from leaguebot.core.utils.log_lines import refuse
from leaguebot.image.cogs.image_group import TEMPLATE_GROUP, ImageSubgroup
from leaguebot.image.models.image_constants import TEMPLATE_LABELS
from leaguebot.image.services.config.image_settings import TEMPLATE


class TemplateCommands(ImageSubgroup):
    template = TEMPLATE_GROUP

    async def _set_template_filename(
        self, interaction: discord.Interaction, column: str, filename: str
    ) -> None:
        """Shared body for the sixteen template filename commands.

        Validate, **then** store (FR-005). A configuration that cannot be used is refused
        at the moment it is named — the one moment the manager is present, holding the
        file, and able to fix it. Nothing is written until every check passes, so a
        rejection leaves the stored value exactly as it stood.
        """
        # Validation parses the named SVG from disk, which outruns Discord's three-second
        # window on a slow host as readily as the sixteen-template sweep does.
        await interaction.response.defer(ephemeral=True)

        if not await self._module_gate(interaction):
            return

        label = TEMPLATE_LABELS[column]
        candidate = filename.strip()

        if "/" in candidate or "\\" in candidate:
            await self._reject(
                interaction,
                label,
                "this sets a filename inside the configured template directory, not a "
                "path. Use `/images config template-directory` to move the folder.",
            )
            return

        # The check at the ask parses the named file, and the change runs it again as it
        # comes up. Nothing is written until every check passes, so a rejection leaves the
        # stored value exactly as it stood.
        await self.bot.change_queue.ask(
            TEMPLATE,
            {"column": column, "value": candidate},
            interaction=interaction,
            what=describe_command(interaction),
        )

    async def _reject(
        self, interaction: discord.Interaction, label: str, reason: str
    ) -> None:
        """Refuse a template command, naming the fault and leaving the config alone.

        Recorded in the log channel as a refusal of the command, so a manager who cannot get a
        template accepted leaves a record of what they tried.
        """
        await refuse(
            interaction,
            f"❌ **{label}** template was **not** changed — {reason}\n"
            f"The previously configured filename is still in force.",
            what=describe_command(interaction),
        )

    # ── The sixteen template filename commands ────────────────────────────
    #
    # Identical in shape; only the column differs. Each delegates to
    # `_set_template_filename`, which holds the whole body.

    @template.command(name="calendar", description="Set the calendar template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_calendar_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "calendar_template", filename)

    @template.command(name="lineup", description="Set the lineup template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_lineup_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "lineup_template", filename)

    @template.command(
        name="results-qualifying",
        description="Set the qualifying session results template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_results_qualifying_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "results_qualifying_template", filename)

    @template.command(
        name="results-race",
        description="Set the race session results template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_results_race_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "results_race_template", filename)

    @template.command(
        name="standings-drivers",
        description="Set the driver standings template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_standings_drivers_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "standings_drivers_template", filename)

    @template.command(
        name="standings-constructors",
        description="Set the constructor standings template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_standings_constructors_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "standings_constructors_template", filename)

    @template.command(name="attendance", description="Set the attendance sheet template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_attendance_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "attendance_template", filename)

    @template.command(name="rsvp", description="Set the check-in call template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_rsvp_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "rsvp_template", filename)

    @template.command(name="weather-p1", description="Set the weather phase 1 template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_weather_p1_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p1_template", filename)

    @template.command(
        name="weather-p2",
        description="Set the weather phase 2 template filename (non-sprint rounds).",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_weather_p2_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p2_template", filename)

    @template.command(
        name="weather-p3",
        description="Set the weather phase 3 template filename (non-sprint rounds).",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_weather_p3_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p3_template", filename)

    @template.command(
        name="weather-p2-sprint",
        description="Set the weather phase 2 template filename for sprint rounds.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_weather_p2_sprint_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p2_sprint_template", filename)

    @template.command(
        name="weather-p3-sprint",
        description="Set the weather phase 3 template filename for sprint rounds.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_weather_p3_sprint_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p3_sprint_template", filename)

    @template.command(
        name="weather-mystery",
        description="Set the mystery round notice template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_weather_mystery_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_mystery_template", filename)

    @template.command(name="verdicts", description="Set the verdicts template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_verdicts_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "verdicts_template", filename)

    @template.command(
        name="verdict-banner",
        description="Set the verdict banner template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @league_manager_only
    async def config_verdict_banner_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "verdict_banner_template", filename)
