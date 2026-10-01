"""WeatherCog — /weather command group.

Provides /weather config phase-1-deadline, phase-2-deadline, phase-3-deadline
for configuring the league's weather pipeline horizons, /weather config view
for reading them back, and /weather channel for setting the channel a division's
forecasts are posted to.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.services import audit_service
from leaguebot.core.services.channel_registry_service import channel_refusal
from leaguebot.core.utils.channel_guard import league_manager_only, changes_nothing
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.log_lines import refuse
from leaguebot.core.utils.member_names import interaction_member
from leaguebot.weather.models.weather_config import WeatherPipelineConfig

log = logging.getLogger(__name__)


def _command(interaction: discord.Interaction) -> str:
    """The slash command *interaction* ran, as a success line names it: "/weather config view"."""
    return describe(interaction).strip("`")


def _deadlines(config: WeatherPipelineConfig) -> tuple[str, str, str]:
    """The three deadlines in force, one detail apiece, as a success line lists them."""
    return (
        f"Phase 1: {config.phase_1_days} day(s)",
        f"Phase 2: {config.phase_2_days} day(s)",
        f"Phase 3: {config.phase_3_hours} hour(s)",
    )


async def _record(
    bot: LeagueBot,
    interaction: discord.Interaction,
    what: str,
    outcome: str,
    *details: str,
) -> None:
    """Write one line in the success form, "Name (<@id>) | *what* | *outcome*", *details* beneath.

    For a success and for nothing changed. The post is bare: a line reporting that nothing went
    wrong is no place for a catch-all, so a post that raises reaches the command's error
    handling. A refusal, a cancel, a lapse and a fault have lines of their own
    (`core/utils/log_lines.py`, `report_failure`).
    """
    await bot.output_router.post_log(
        f"{interaction_member(interaction)} | {what} | {outcome}"
        + "".join(f"\n  {detail}" for detail in details)
    )


async def _nothing_changed(
    bot: LeagueBot, interaction: discord.Interaction, phase: str, shown: str
) -> None:
    """Answer a deadline given the value it already holds, and record that nothing changed.

    *phase* is "Phase 1", and *shown* the value in its unit, "6 day(s)".
    """
    await _record(bot, interaction, _command(interaction), "Nothing changed", f"{phase}: {shown}")
    await interaction.followup.send(
        f"ℹ️ Nothing changed: the {phase} deadline is already **{shown}**.", ephemeral=True
    )


class WeatherCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /weather config group
    # ------------------------------------------------------------------

    weather = app_commands.Group(name="weather", description="Weather module commands")
    config_group = app_commands.Group(
        name="config", description="Configure weather pipeline settings", parent=weather
    )

    # ------------------------------------------------------------------
    # Shared pre-condition checks
    # ------------------------------------------------------------------

    async def _module_gate(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Return True if the weather module is enabled; otherwise answer and return False.

        The refusal is recorded in the log channel, as every refusal of a command that changes
        something is. A command that changes nothing (`record=False`) answers and records nothing.
        """
        if await self.bot.module_service.is_weather_enabled():
            return True
        reply = "❌ The Weather module is not enabled."
        if record:
            await refuse(interaction, reply, what=describe(interaction))
        else:
            await interaction.response.send_message(reply, ephemeral=True)
        return False

    async def _active_season_gate(self, interaction: discord.Interaction) -> bool:
        """Return True (and refuse, recording it) if a season's placements are confirmed."""
        season = await self.bot.season_service.get_confirmed_season()
        if season is not None:
            await refuse(
                interaction,
                "❌ Phase deadline configuration cannot be changed once a season's placements are confirmed.",
                what=describe(interaction),
            )
            return True
        return False

    # ------------------------------------------------------------------
    # /weather config phase-1-deadline
    # ------------------------------------------------------------------

    @config_group.command(
        name="phase-1-deadline",
        description="Set days before round to publish Phase 1 weather (default 5).",
    )
    @app_commands.describe(days="Number of days before the round (positive integer)")
    @league_manager_only
    async def phase_1_deadline(self, interaction: discord.Interaction, days: int) -> None:
        if not await self._module_gate(interaction):
            return
        if await self._active_season_gate(interaction):
            return
        if days < 1:
            await refuse(
                interaction, "❌ Phase 1 deadline must be at least 1 day.", what=describe(interaction)
            )
            return

        await interaction.response.defer(ephemeral=True)

        from leaguebot.weather.services.weather_config_service import set_phase_1_days
        result = await set_phase_1_days(self.bot.db_path, days)

        if isinstance(result, str):
            await refuse(interaction, f"❌ {result}", what=describe(interaction))
            return
        if result is None:
            await _nothing_changed(self.bot, interaction, "Phase 1", f"{days} day(s)")
            return

        await _record(self.bot, interaction, _command(interaction), "Success", *_deadlines(result))
        await interaction.followup.send(
            f"✅ Phase 1 deadline set to **{days} day(s)** before round. "
            f"(Phase 2: {result.phase_2_days}d, Phase 3: {result.phase_3_hours}h)",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /weather config phase-2-deadline
    # ------------------------------------------------------------------

    @config_group.command(
        name="phase-2-deadline",
        description="Set days before round to publish Phase 2 weather (default 2).",
    )
    @app_commands.describe(days="Number of days before the round (positive integer)")
    @league_manager_only
    async def phase_2_deadline(self, interaction: discord.Interaction, days: int) -> None:
        if not await self._module_gate(interaction):
            return
        if await self._active_season_gate(interaction):
            return
        if days < 1:
            await refuse(
                interaction, "❌ Phase 2 deadline must be at least 1 day.", what=describe(interaction)
            )
            return

        await interaction.response.defer(ephemeral=True)

        from leaguebot.weather.services.weather_config_service import set_phase_2_days
        result = await set_phase_2_days(self.bot.db_path, days)

        if isinstance(result, str):
            await refuse(interaction, f"❌ {result}", what=describe(interaction))
            return
        if result is None:
            await _nothing_changed(self.bot, interaction, "Phase 2", f"{days} day(s)")
            return

        await _record(self.bot, interaction, _command(interaction), "Success", *_deadlines(result))
        await interaction.followup.send(
            f"✅ Phase 2 deadline set to **{days} day(s)** before round. "
            f"(Phase 1: {result.phase_1_days}d, Phase 3: {result.phase_3_hours}h)",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /weather config phase-3-deadline
    # ------------------------------------------------------------------

    @config_group.command(
        name="phase-3-deadline",
        description="Set hours before round to publish Phase 3 weather (default 2).",
    )
    @app_commands.describe(hours="Number of hours before the round (positive integer)")
    @league_manager_only
    async def phase_3_deadline(self, interaction: discord.Interaction, hours: int) -> None:
        if not await self._module_gate(interaction):
            return
        if await self._active_season_gate(interaction):
            return
        if hours < 1:
            await refuse(
                interaction, "❌ Phase 3 deadline must be at least 1 hour.", what=describe(interaction)
            )
            return

        await interaction.response.defer(ephemeral=True)

        from leaguebot.weather.services.weather_config_service import set_phase_3_hours
        result = await set_phase_3_hours(self.bot.db_path, hours)

        if isinstance(result, str):
            await refuse(interaction, f"❌ {result}", what=describe(interaction))
            return
        if result is None:
            await _nothing_changed(self.bot, interaction, "Phase 3", f"{hours} hour(s)")
            return

        await _record(self.bot, interaction, _command(interaction), "Success", *_deadlines(result))
        await interaction.followup.send(
            f"✅ Phase 3 deadline set to **{hours} hour(s)** before round. "
            f"(Phase 1: {result.phase_1_days}d, Phase 2: {result.phase_2_days}d)",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /weather config view
    # ------------------------------------------------------------------

    @config_group.command(
        name="view",
        description="Show the three weather deadlines currently set.",
    )
    @league_manager_only
    @changes_nothing
    async def config_view(self, interaction: discord.Interaction) -> None:
        """Read the three deadlines back (issue #118).

        **Not gated on the season**, unlike the setters beside it. The deadlines belong to the
        server rather than to a season, and before this command they could be read only in the
        configuration and placements reviews, each tied to one stage of one season. While a
        season's placements are confirmed the setters are refused, so the values shown then
        are the ones the season runs on.
        """
        if not await self._module_gate(interaction, record=False):
            return

        await interaction.response.defer(ephemeral=True)

        from leaguebot.weather.services.weather_config_service import (
            describe_deadlines,
            get_weather_pipeline_config,
        )
        config = await get_weather_pipeline_config(self.bot.db_path)

        await interaction.followup.send(
            "\n".join(["**Weather deadlines**", *describe_deadlines(config)]),
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /weather channel
    # ------------------------------------------------------------------

    @weather.command(
        name="channel",
        description="Set the weather forecast channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Weather forecast channel")
    @league_manager_only
    async def channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel a division's forecasts are posted to (#462).

        Weather's own command, under weather's own group: it sat under core's `/division`
        until #462 moved it, and only its name changed. **Its module-off wording is its own**,
        not `_module_gate`'s, as it was worded before it moved.

        **The live season's division** (#220): a division's channels belong to the season being
        built or raced, and an archived one's no longer matter. Pending completion is live,
        so a channel lost before the season completes may be repaired.

        **A channel does one job** (`channel_refusal`), checked before the write, so a
        refusal leaves the configuration exactly as it stood — the value the setting already
        holds included. The change is recorded by core's `audit_service`, as it was when core
        wrote it: `DIVISION_CHANNEL_SET`, with its `channel_type`.
        """
        if not await self.bot.module_service.is_weather_enabled():
            await refuse(
                interaction,
                "\u274c The Weather module is not enabled.",
                what=describe(interaction),
            )
            return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(
                interaction,
                "\u274c No season is live. A division's channels belong to the season being built or raced \u2014 start one with `/season setup`.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division **{name}** not found in the current season.",
                what=describe(interaction),
            )
            return

        refused = await channel_refusal(
            self.bot.db_path, channel, "weather", division_name=div.name
        )
        if refused is not None:
            await refuse(interaction, refused, what=describe(interaction))
            return

        old_id = await self.bot.season_service.set_division_forecast_channel(div.id, channel.id)
        await audit_service.record_change(
            self.bot.db_path,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
            change_type="DIVISION_CHANNEL_SET",
            old_value={"channel_type": "weather", "channel_id": old_id},
            new_value={"channel_type": "weather", "channel_id": channel.id},
            now=datetime.now(timezone.utc),
            division_id=div.id,
        )

        # "Updated" says a channel was moved rather than assigned afresh (issue #212).
        verb = "set" if old_id is None else "updated"
        await interaction.response.send_message(
            f"\u2705 Weather forecast channel for **{name}** {verb} to {channel.mention}.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /weather channel | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )


async def setup(bot: LeagueBot) -> None:
    await bot.add_cog(WeatherCog(bot))
