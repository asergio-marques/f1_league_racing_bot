"""ImageCog — /images commands.

Permission tiers (FR-041, FR-042):
  * ``@channel_guard`` + ``@server_admin_only``  — template and asset locations
  * ``@channel_guard`` + ``@admin_only``         — toggles, preferences, view, test

Every response is ephemeral (FR-044), and every command refuses to act while the module
is disabled (FR-005).
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from models.image_constants import (
    ASPECT_LABELS,
    ASPECTS,
    ASSET_LABELS,
    LIVE_POSTING_ASPECTS,
    PENDING_POSTING_ASPECTS,
    TEMPLATE_COLUMNS,
    TEMPLATE_COMMAND_NAMES,
    TEMPLATE_LABELS,
)
from models.image_module import STATE_DISABLED, STATE_ENABLED
from utils.channel_guard import admin_only, channel_guard, server_admin_only
from utils.paths import PathContainmentError, relative_to_root

log = logging.getLogger(__name__)

_STATE_ICONS = {
    STATE_ENABLED: "✅",
    STATE_DISABLED: "❌",
}
_INVALID_ICON = "⚠️"


def toggle_enabled_lines(aspect: str, label: str, blocking: list[str]) -> list[str]:
    """The reply confirming an aspect has been enabled.

    An aspect with no posting path yet records intent alone, and says so: a manager who
    enables one and sees no change in the next post would otherwise reasonably think it
    broken. An aspect that does post says nothing of the sort — the claim was once made
    of all eight and outlived the truth of it for seven.
    """
    lines = [f"✅ **{label}** image output **enabled**."]

    if aspect not in LIVE_POSTING_ASPECTS:
        lines.append(
            "⏳ **Not yet in effect** — posting for this aspect is wired in a later "
            "update. Use the matching `/images test` command to see what it will produce."
        )

    if blocking:
        lines.append("")
        lines.append("⚠️ It would not produce an image as configured:")
        lines += [f"  ↳ {reason}" for reason in blocking]

    return lines


class ImageCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    images = app_commands.Group(
        name="images",
        description="Image module commands.",
        default_permissions=None,
    )

    config = app_commands.Group(
        name="config",
        description="Configure image generation settings.",
        parent=images,
    )

    # The fifteen template filename setters live in their own group rather than under
    # `config`. Discord allows at most 25 subcommands per group and forbids a third
    # nesting level, and `config` would otherwise carry 29: 1 template directory +
    # 15 filenames + 7 asset directories + 4 preferences + toggle + view.
    template = app_commands.Group(
        name="template",
        description="Set which SVG file backs each kind of image.",
        parent=images,
    )

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _guard_module_enabled(self, interaction: discord.Interaction) -> bool:
        """Return True when the module is enabled; otherwise reply and return False."""
        if not await self.bot.module_service.is_images_enabled(interaction.guild_id):  # type: ignore[attr-defined]
            await interaction.response.send_message(
                "❌ The Image module is not enabled. "
                "Use `/module enable images` first.",
                ephemeral=True,
            )
            return False
        return True

    @property
    def _config_service(self):
        return self.bot.image_config_service  # type: ignore[attr-defined]

    @property
    def _validity_service(self):
        return self.bot.image_validity_service  # type: ignore[attr-defined]

    @staticmethod
    async def _reply(interaction: discord.Interaction, content: str) -> None:
        """Send an ephemeral response, following up when already deferred."""
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    async def _set_directory(
        self, interaction: discord.Interaction, column: str, value: str, label: str
    ) -> None:
        """Shared body for the template directory and the seven asset directories.

        A path that escapes the project root is rejected here, at the point of
        configuration, rather than surfacing as a render failure later (FR-011, FR-016).
        The stored value is left unchanged on rejection.
        """
        if not await self._guard_module_enabled(interaction):
            return

        from utils.paths import resolve_within_project_root

        try:
            resolved = resolve_within_project_root(value)
        except PathContainmentError as exc:
            await self._reply(
                interaction,
                f"❌ {exc}\nDirectories must sit inside the project root. "
                f"The stored value is unchanged.",
            )
            return
        except ValueError as exc:
            await self._reply(interaction, f"❌ {exc}")
            return

        stored = relative_to_root(resolved)
        await self._config_service.set_field(interaction.guild_id, column, stored)

        # Report the effect immediately, so the administrator does not need a second
        # command to learn whether the new location resolves.
        if resolved.is_dir():
            verdict = "✅ Resolves."
        elif resolved.exists():
            verdict = "⚠️ That path exists but is not a directory."
        else:
            verdict = "⚠️ Nothing is there yet — templates placed later will be picked up."

        await self._reply(
            interaction,
            f"✅ **{label}** set to `{stored}`.\n{verdict}\nSearched: `{resolved}`",
        )
        await self._log(interaction, f"{label} = {stored}")

    async def _set_template_filename(
        self, interaction: discord.Interaction, column: str, filename: str
    ) -> None:
        """Shared body for the fifteen template filename commands.

        Validate, **then** store (FR-005). A configuration that cannot be used is refused
        at the moment it is named — the one moment the manager is present, holding the
        file, and able to fix it. Nothing is written until every check passes, so a
        rejection leaves the stored value exactly as it stood.
        """
        from services.image_validity_service import check_template

        if not await self._guard_module_enabled(interaction):
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

        proposed = await self._config_service.candidate_config(
            interaction.guild_id, column, candidate
        )
        if proposed is None:
            await self._reject(
                interaction, label, "this server has no image configuration to amend."
            )
            return

        problem = check_template(proposed, column)
        if problem is not None:
            await self._reject(interaction, label, problem.message())
            return

        # No stand-in comparison is made, for any type. The lineup was the only one that
        # ever needed it — its fields were named after a league's own teams, so at this
        # moment they could be compared only against a *stand-in* for the division that
        # would be drawn, and XIV.9 made such a divergence a warning and not a refusal.
        # Every field of every template is now verifiable against the file alone, so
        # `check_template` above either passes or refuses and nothing is left to warn
        # about (047 FR-024).
        await self._config_service.set_field(interaction.guild_id, column, candidate)

        lines = [f"✅ **{label}** template set to `{candidate}`.", "✅ Valid."]

        await self._reply(interaction, "\n".join(lines))
        await self._log(interaction, f"{label} template = {candidate}")

    async def _reject(
        self, interaction: discord.Interaction, label: str, reason: str
    ) -> None:
        """Refuse a template command, naming the fault and leaving the config alone.

        Logged like any accepted change: a refused configuration is as much a part of the
        audit trail as a stored one (Principle V), and a manager who cannot get a template
        accepted leaves a record of what they tried.
        """
        await self._reply(
            interaction,
            f"❌ **{label}** template was **not** changed — {reason}\n"
            f"The previously configured filename is still in force.",
        )
        await self._log(interaction, f"{label} template REJECTED — {reason}")

    async def _log(self, interaction: discord.Interaction, detail: str) -> None:
        """Record a configuration mutation to the calculation log (Principle V)."""
        try:
            await self.bot.output_router.post_log(  # type: ignore[attr-defined]
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) "
                f"| /images config | {detail}",
            )
        except Exception as exc:  # logging must never break a configuration command
            log.error("image config log write failed: %s", exc)

    # ── /images config template-directory ─────────────────────────────────

    @config.command(
        name="template-directory",
        description="Set the folder searched for SVG template files.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @channel_guard
    @server_admin_only
    async def config_template_directory(
        self, interaction: discord.Interaction, directory: str
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        await self._set_directory(
            interaction, "template_directory", directory, "Template directory"
        )

    # ── The fifteen template filename commands ────────────────────────────
    #
    # Identical in shape; only the column differs. Each delegates to
    # `_set_template_filename`, which holds the whole body.

    @template.command(name="calendar", description="Set the calendar template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_calendar_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "calendar_template", filename)

    @template.command(name="lineup", description="Set the lineup template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_lineup_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "lineup_template", filename)

    @template.command(
        name="results-qualifying",
        description="Set the qualifying session results template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_results_qualifying_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "results_qualifying_template", filename)

    @template.command(
        name="results-race",
        description="Set the race session results template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_results_race_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "results_race_template", filename)

    @template.command(
        name="standings-drivers",
        description="Set the driver standings template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_standings_drivers_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "standings_drivers_template", filename)

    @template.command(
        name="standings-constructors",
        description="Set the constructor standings template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_standings_constructors_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "standings_constructors_template", filename)

    @template.command(name="attendance", description="Set the attendance sheet template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_attendance_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "attendance_template", filename)

    @template.command(name="rsvp", description="Set the check-in call template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_rsvp_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "rsvp_template", filename)

    @template.command(name="weather-p1", description="Set the weather phase 1 template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_weather_p1_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p1_template", filename)

    @template.command(
        name="weather-p2",
        description="Set the weather phase 2 template filename (non-sprint rounds).",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_weather_p2_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p2_template", filename)

    @template.command(
        name="weather-p3",
        description="Set the weather phase 3 template filename (non-sprint rounds).",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_weather_p3_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p3_template", filename)

    @template.command(
        name="weather-p2-sprint",
        description="Set the weather phase 2 template filename for sprint rounds.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_weather_p2_sprint_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p2_sprint_template", filename)

    @template.command(
        name="weather-p3-sprint",
        description="Set the weather phase 3 template filename for sprint rounds.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_weather_p3_sprint_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_p3_sprint_template", filename)

    @template.command(
        name="weather-mystery",
        description="Set the mystery round notice template filename.",
    )
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_weather_mystery_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "weather_mystery_template", filename)

    @template.command(name="verdicts", description="Set the verdicts template filename.")
    @app_commands.describe(filename="Filename inside the template directory.")
    @channel_guard
    @server_admin_only
    async def config_verdicts_template(self, interaction: discord.Interaction, filename: str) -> None:
        await self._set_template_filename(interaction, "verdicts_template", filename)

    # ── The seven asset directory commands ────────────────────────────────
    #
    # Identical in shape to `template-directory`; only the column differs. Each is
    # subject to the same project-root containment rejection (FR-016).

    @config.command(
        name="track-image-directory",
        description="Set the folder searched for circuit images.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @channel_guard
    @server_admin_only
    async def config_track_image_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "track_image_directory", directory, "Circuit images")

    @config.command(
        name="team-image-directory",
        description="Set the folder searched for team logos, badges and cars.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @channel_guard
    @server_admin_only
    async def config_team_image_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "team_image_directory", directory, "Team badges")

    @config.command(
        name="flag-directory",
        description="Set the folder searched for driver nationality flags.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @channel_guard
    @server_admin_only
    async def config_flag_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "flag_directory", directory, "Nationality flags")

    @config.command(
        name="driver-image-directory",
        description="Set the folder searched for driver portraits.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @channel_guard
    @server_admin_only
    async def config_driver_image_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "driver_image_directory", directory, "Driver portraits")

    @config.command(
        name="marker-directory",
        description="Set the folder searched for standings position-change markers.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @channel_guard
    @server_admin_only
    async def config_marker_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "marker_directory", directory, "Position-change markers")

    @config.command(
        name="weather-icon-directory",
        description="Set the folder searched for weather condition icons.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @channel_guard
    @server_admin_only
    async def config_weather_icon_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "weather_icon_directory", directory, "Weather icons")

    @config.command(
        name="tyre-directory",
        description="Set the folder searched for tyre compound icons.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @channel_guard
    @server_admin_only
    async def config_tyre_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "tyre_directory", directory, "Tyre compounds")

    # ── /images config toggle ─────────────────────────────────────────────

    @config.command(
        name="toggle",
        description="Switch one kind of output between a generated image and text.",
    )
    @app_commands.describe(aspect="Which kind of output to switch.")
    @app_commands.choices(
        aspect=[
            app_commands.Choice(name="Calendar", value="calendar"),
            app_commands.Choice(name="Lineup", value="lineup"),
            app_commands.Choice(name="Session results", value="results"),
            app_commands.Choice(name="Standings", value="standings"),
            app_commands.Choice(name="Attendance sheet", value="attendance"),
            app_commands.Choice(name="Check-in call", value="rsvp"),
            app_commands.Choice(name="Weather forecasts", value="weather"),
            app_commands.Choice(name="Verdicts", value="verdicts"),
        ]
    )
    @channel_guard
    @admin_only
    async def config_toggle(
        self, interaction: discord.Interaction, aspect: app_commands.Choice[str]
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return

        server_id = interaction.guild_id
        now_enabled = await self._config_service.toggle_aspect(server_id, aspect.value)
        label = ASPECT_LABELS[aspect.value]

        if not now_enabled:
            await self._reply(
                interaction, f"❌ **{label}** image output **disabled**. Posting stays as text."
            )
            await self._log(interaction, f"{label} image output disabled")
            return

        blocking = await self._aspect_blocking_reasons(server_id, aspect.value)
        lines = toggle_enabled_lines(aspect.value, label, blocking)

        await self._reply(interaction, "\n".join(lines))
        await self._log(interaction, f"{label} image output enabled")

    async def _aspect_blocking_reasons(self, server_id: int, aspect: str) -> list[str]:
        statuses = await self._validity_service.aspect_statuses(server_id)
        for status in statuses:
            if status.aspect == aspect:
                return status.blocking_reasons
        return []

    # ── Presentation preferences ──────────────────────────────────────────

    @config.command(
        name="fastest-lap-colour",
        description="Set the colour distinguishing the fastest lap of a race.",
    )
    @app_commands.describe(colour="A '#' followed by exactly six hex digits, e.g. #A020F0.")
    @channel_guard
    @admin_only
    async def config_fastest_lap_colour(
        self, interaction: discord.Interaction, colour: str
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return

        from utils.colour import CONTRAST_AA_NORMAL, InvalidColour, meets_aa_normal, normalise_hex

        # 1. Reject a malformed value, leaving the stored colour untouched (FR-025).
        try:
            canonical = normalise_hex(colour)
        except InvalidColour as exc:
            await self._reply(
                interaction, f"❌ {exc}\nThe stored colour is unchanged."
            )
            return

        # 2. Store it. Storing *before* measuring is deliberate: an unmeasurable
        #    contrast must never cost the manager their input (FR-026, FR-027).
        await self._config_service.set_field(
            interaction.guild_id, "fastest_lap_colour", canonical
        )
        lines = [f"✅ Fastest-lap colour set to `{canonical}`."]

        # 3. Measure and report the contrast against the template's own background.
        ratio, background, problem = await self._measure_fastest_lap_contrast(
            interaction.guild_id, canonical
        )

        if ratio is None:
            lines.append(f"ℹ️ Contrast could not be measured: {problem}")
        else:
            lines.append(
                f"Contrast against the template's plate (`{background}`): "
                f"**{ratio:.2f}:1**"
            )
            if not meets_aa_normal(ratio):
                lines.append(
                    f"⚠️ That is below {CONTRAST_AA_NORMAL}:1, the threshold at which text "
                    f"of this size stays legible. The colour is stored all the same — "
                    f"it is your league's to choose."
                )

        await self._reply(interaction, "\n".join(lines))
        await self._log(interaction, f"Fastest-lap colour = {canonical}")

    async def _measure_fastest_lap_contrast(
        self, server_id: int, colour: str
    ) -> tuple[float | None, str | None, str | None]:
        """Return (ratio, background, problem).

        The background is located by a single documented ``@id`` in the race results
        template (FR-026a). Layer 1 validity cannot establish that the element exists, so
        its absence is an unmeasurable contrast, not a template validity failure.
        """
        from models.image_constants import FASTEST_LAP_BACKGROUND_ID
        from utils.colour import coerce_css_colour, contrast_ratio
        from utils.svg_document import (
            FieldIndex,
            SvgError,
            computed_style,
            load_svg,
            stylesheet,
        )

        reports = await self._validity_service.template_reports(server_id)
        report = reports.get("results_race_template")

        if report is None:
            return None, None, "the race results template could not be read."
        if not report.valid:
            return None, None, f"the race results template is invalid — {report.reason}"

        try:
            root = load_svg(report.resolved_path)
        except SvgError as exc:
            return None, None, f"the race results template could not be parsed — {exc}"

        element = FieldIndex(root).resolve(FASTEST_LAP_BACKGROUND_ID)
        if element is None:
            return (
                None,
                None,
                f"the race results template declares no `{FASTEST_LAP_BACKGROUND_ID}` "
                f"element to measure against.",
            )

        declared = computed_style(element, stylesheet(root)).get("fill")
        background = coerce_css_colour(declared)
        if background is None:
            return (
                None,
                None,
                f"the `{FASTEST_LAP_BACKGROUND_ID}` element's fill (`{declared}`) is not "
                f"a plain colour.",
            )

        return contrast_ratio(colour, background), background, None

    @config.command(
        name="time-zone",
        description="Set the time zone times are displayed in on images.",
    )
    @app_commands.describe(zone="An IANA zone name, e.g. Europe/Lisbon.")
    @channel_guard
    @admin_only
    async def config_time_zone(self, interaction: discord.Interaction, zone: str) -> None:
        if not await self._guard_module_enabled(interaction):
            return

        from zoneinfo import available_timezones

        candidate = zone.strip()
        if candidate not in available_timezones():
            await self._reply(
                interaction,
                f"❌ `{candidate}` is not a recognised time zone. "
                f"Use an IANA name such as `Europe/Lisbon` or `UTC`.",
            )
            return

        await self._config_service.set_field(interaction.guild_id, "time_zone", candidate)
        await self._reply(
            interaction,
            f"✅ Time zone set to `{candidate}`.\n"
            f"Times are shown in the offset that zone carries **on the date displayed**, "
            f"so a season spanning a daylight-saving change stays correct.",
        )
        await self._log(interaction, f"Time zone = {candidate}")

    @config_time_zone.autocomplete("zone")
    async def _time_zone_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete over the IANA zones.

        There are several hundred — far past what a static choice list holds — which is
        why this is a free-text parameter with completion rather than `@choices`.
        """
        from zoneinfo import available_timezones

        needle = (current or "").strip().lower()
        zones = sorted(available_timezones())
        matches = [z for z in zones if needle in z.lower()] if needle else zones
        return [app_commands.Choice(name=z, value=z) for z in matches[:25]]

    @config.command(
        name="time-format",
        description="Choose between a 12-hour and a 24-hour clock on images.",
    )
    @app_commands.describe(clock="Which clock format to display times in.")
    @app_commands.choices(
        clock=[
            app_commands.Choice(name="24-hour (14:30)", value="24H"),
            app_commands.Choice(name="12-hour (2:30 PM)", value="12H"),
        ]
    )
    @channel_guard
    @admin_only
    async def config_time_format(
        self, interaction: discord.Interaction, clock: app_commands.Choice[str]
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        await self._config_service.set_field(
            interaction.guild_id, "time_format", clock.value
        )
        await self._reply(interaction, f"✅ Clock format set to **{clock.name}**.")
        await self._log(interaction, f"Clock format = {clock.value}")

    @config.command(
        name="date-format",
        description="Choose how dates are written on images.",
    )
    @app_commands.describe(style="Which date format to display.")
    @app_commands.choices(
        # Named by worked example rather than by token, so the manager picks by
        # appearance. The weekday-carrying format is first and is the default: a season
        # run on the same weekday every second week makes the weekday the part of a date
        # a driver reads for (FR-023).
        style=[
            app_commands.Choice(name="Sun 14 Jun 2026", value="DDD_DD_MON_YYYY"),
            app_commands.Choice(name="14 Jun 2026", value="DD_MON_YYYY"),
            app_commands.Choice(name="14/06/2026", value="DD_MM_YYYY"),
            app_commands.Choice(name="06/14/2026", value="MM_DD_YYYY"),
            app_commands.Choice(name="2026-06-14", value="YYYY_MM_DD"),
        ]
    )
    @channel_guard
    @admin_only
    async def config_date_format(
        self, interaction: discord.Interaction, style: app_commands.Choice[str]
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        await self._config_service.set_field(
            interaction.guild_id, "date_format", style.value
        )
        await self._reply(interaction, f"✅ Date format set to **{style.name}**.")
        await self._log(interaction, f"Date format = {style.value}")

    # ── /images config view ───────────────────────────────────────────────

    @config.command(
        name="view",
        description="Show the whole image configuration and whether it holds together.",
    )
    @channel_guard
    @admin_only
    async def config_view(self, interaction: discord.Interaction) -> None:
        if not await self._guard_module_enabled(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        text = await self.build_configuration_report(interaction.guild_id)
        for chunk in _chunk(text):
            await interaction.followup.send(chunk, ephemeral=True)

    async def build_configuration_report(self, server_id: int) -> str:
        """Render the configuration and its validity.

        `/season review` renders the aspect section from the same `AspectStatus` list, so
        the two surfaces cannot drift (FR-033).
        """
        from services.image_render_service import (
            CONVERTER_NAME,
            converter_absent_message,
            converter_available,
        )
        from services.image_validity_service import (
            ImageValidityService,
            plain_directory_reason,
            plain_reason,
        )

        config = await self._config_service.get_config(server_id)
        if config is None:
            return "❌ No image configuration exists for this server."

        template_reports = await self._validity_service.template_reports(server_id)
        directory_reports = await self._validity_service.directory_reports(server_id)

        lines: list[str] = ["**Image module configuration**", ""]

        if not converter_available():
            lines += [converter_absent_message(), ""]
        else:
            lines += [f"Rasteriser: ✅ {CONVERTER_NAME} found", ""]

        lines += [
            "**Templates**",
            f"  Directory: `{config.template_directory}`",
        ]
        for column in TEMPLATE_COLUMNS:
            report = template_reports.get(column)
            filename = getattr(config, column)
            if report is not None and report.valid:
                lines.append(f"  ✅ {TEMPLATE_LABELS[column]}: `{filename}`")
            else:
                reason = plain_reason(report) if report else "this has not been checked"
                lines.append(f"  ⚠️ {TEMPLATE_LABELS[column]}: `{filename}` — {reason}")

        # Invariant 3: never overstate what was checked (FR-028b).
        lines += ["", f"  _{ImageValidityService.depth_summary(template_reports)}_", ""]

        lines.append("**Asset directories**")
        for column, report in directory_reports.items():
            value = getattr(config, column)
            if report.valid:
                lines.append(f"  ✅ {ASSET_LABELS[column]}: `{value}`")
            else:
                lines.append(
                    f"  ⚠️ {ASSET_LABELS[column]}: `{value}` "
                    f"— {plain_directory_reason(report)}"
                )

        lines += [
            "",
            "**Presentation**",
            f"  Time zone: `{config.time_zone}`",
            f"  Clock: `{config.time_format}`",
            f"  Date format: `{config.date_format}`",
            f"  Fastest-lap colour: `{config.fastest_lap_colour}`",
            "",
        ]

        lines += await self.build_aspect_section(server_id)
        return "\n".join(lines)

    async def build_aspect_section(self, server_id: int) -> list[str]:
        """The eight aspects in their three states (FR-031, FR-032)."""
        statuses = await self._validity_service.aspect_statuses(server_id)

        lines = ["**Output aspects**"]
        for status in statuses:
            icon = _STATE_ICONS.get(status.state, _INVALID_ICON)
            lines.append(f"  {icon} {ASPECT_LABELS[status.aspect]}")
            for reason in status.reasons:
                lines.append(f"      ↳ {reason}")

        # Named individually rather than as a blanket claim over all eight, and gone
        # entirely once every aspect posts.
        if PENDING_POSTING_ASPECTS:
            pending = ", ".join(
                ASPECT_LABELS[aspect] for aspect in PENDING_POSTING_ASPECTS
            )
            lines += [
                "",
                f"_Recorded but not yet in effect: **{pending}** — posting for these is "
                "wired in a later update. Use the matching `/images test` command to see what they produce._",
            ]
        return lines


    # ── /images test ── the eleven previews ─────────────────────────

    # One command per image kind, each drawn against the league's own division and, where
    # the kind pertains to one, its own round. Discord allows a group of subcommands
    # beneath a top-level command and no further nesting, which is the depth `config` and
    # `template` already use.
    test = app_commands.Group(
        name="test",
        description="Preview an image against your own league's configuration.",
        parent=images,
    )

    async def _division_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The divisions of whichever season a preview draws (FR-003).

        The approved season where there is one, the season pending approval otherwise —
        so a league can complete on its divisions before `/season approve` has been run.
        A division of a completed or cancelled season is deliberately absent: a preview is
        a check on what the league is running or about to run.

        On a server holding no season this offers nothing, which is why the parameter is
        optional there: such a server draws a fabricated league and needs no name.
        """
        try:
            season = await self.bot.season_service.get_previewable_season(  # type: ignore[attr-defined]
                interaction.guild_id
            )
            if season is None:
                return []
            divisions = await self.bot.season_service.get_divisions(season.id)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — an autocomplete never breaks the command
            return []

        typed = (current or "").strip().casefold()
        return [
            app_commands.Choice(name=division.name, value=division.name)
            for division in divisions
            if typed in division.name.casefold()
        ][:25]

    async def _run_preview(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        kind: str,
        division: str | None,
        build,
        round_number: int | None = None,
    ) -> None:
        """The body every preview command shares.

        Guards, then resolves, then draws. The order matters: a fault of configuration is
        reported as one and never as a failure to render (FR-015), and the rasteriser is
        checked before anything is resolved because its absence defeats every kind alike.

        *kind* names the preview, and the three conditions 045 passed as separate flags —
        whether rounds are required, whether teams are, and what format the round must
        carry — are read from `PREVIEW_KINDS` off it. One table, read in one place, rather
        than three rules restated at eleven call sites.
        """
        from services.image_preview_service import PreviewRefused, resolve_context
        from services.image_render_service import (
            converter_absent_message,
            converter_available,
        )

        if not await self._guard_module_enabled(interaction):
            return

        # Defer first: several kinds draw more than one picture, and the rasteriser is a
        # subprocess, so the three-second acknowledgement cannot be met otherwise.
        await interaction.response.defer(ephemeral=True)

        if not converter_available(use_cache=False):
            await interaction.followup.send(converter_absent_message(), ephemeral=True)
            return

        try:
            context = await resolve_context(
                self.bot,
                interaction.guild_id,
                division,
                guild=interaction.guild,
                round_number=round_number,
                kind=kind,
            )
        except PreviewRefused as refusal:
            await interaction.followup.send(refusal.message, ephemeral=True)
            return

        try:
            requests = await build(context)
        except Exception as exc:  # noqa: BLE001 — reported, never raised at a manager
            log.exception("images test: could not assemble %s", title)
            await interaction.followup.send(
                f"⛔ The data for this preview could not be assembled — {exc}",
                ephemeral=True,
            )
            return

        outcomes = []
        for label, template_key, spec_builder in requests:
            outcome = await self.bot.image_render_service.render(  # type: ignore[attr-defined]
                interaction.guild_id, template_key, spec_builder
            )
            outcomes.append((label, template_key, outcome))

        await self._send_preview(interaction, title=title, context=context, outcomes=outcomes)

    # ── The two kinds that fabricate no outcome ──────────────────────

    @test.command(
        name="calendar",
        description="Preview the calendar image for one of your divisions.",
    )
    @app_commands.describe(
        division="The division whose calendar to draw. Omit where this server has no season."
    )
    @channel_guard
    @admin_only
    async def test_calendar(
        self, interaction: discord.Interaction, division: str | None = None
    ) -> None:
        from services.image_preview_service import build_calendar_preview

        async def _build(context):
            return await build_calendar_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Calendar",
            kind="calendar",
            division=division,
            build=_build,
        )

    @test.command(
        name="lineup",
        description="Preview the lineup image for one of your divisions.",
    )
    @app_commands.describe(
        division="The division whose lineup to draw. Omit where this server has no season."
    )
    @channel_guard
    @admin_only
    async def test_lineup(
        self, interaction: discord.Interaction, division: str | None = None
    ) -> None:
        from services.image_preview_service import build_lineup_preview

        async def _build(context):
            return await build_lineup_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Lineup",
            kind="lineup",
            division=division,
            build=_build,
        )

    @test.command(
        name="results",
        description="Preview the results image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @channel_guard
    @admin_only
    async def test_results(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        round: int | None = None,
    ) -> None:
        from services.image_preview_service import build_results_preview

        async def _build(context):
            return await build_results_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Results",
            kind="results",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="standings",
        description="Preview the standings image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @channel_guard
    @admin_only
    async def test_standings(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        round: int | None = None,
    ) -> None:
        from services.image_preview_service import build_standings_preview

        async def _build(context):
            return await build_standings_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Standings",
            kind="standings",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="attendance",
        description="Preview the attendance sheet image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @channel_guard
    @admin_only
    async def test_attendance(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        round: int | None = None,
    ) -> None:
        from services.image_preview_service import build_attendance_preview

        async def _build(context):
            return await build_attendance_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Attendance sheet",
            kind="attendance",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="rsvp",
        description="Preview the check-in call image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @channel_guard
    @admin_only
    async def test_rsvp(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        round: int | None = None,
    ) -> None:
        from services.image_preview_service import build_rsvp_preview

        async def _build(context):
            return await build_rsvp_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Check-in call",
            kind="rsvp",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="verdict",
        description="Preview the verdict image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @channel_guard
    @admin_only
    async def test_verdict(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        round: int | None = None,
    ) -> None:
        from services.image_preview_service import build_verdict_preview

        async def _build(context):
            return await build_verdict_preview(self.bot, context)

        await self._run_preview(
            interaction,
            title="Verdict",
            kind="verdict",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="weather-p1",
        description="Preview the weather — phase 1 image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @channel_guard
    @admin_only
    async def test_weather_p1(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        round: int | None = None,
    ) -> None:
        from services.image_preview_service import build_weather_preview

        async def _build(context):
            return await build_weather_preview(self.bot, context, phase=1)

        await self._run_preview(
            interaction,
            title="Weather — phase 1",
            kind="weather-p1",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="weather-p2",
        description="Preview the weather — phase 2 image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @channel_guard
    @admin_only
    async def test_weather_p2(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        round: int | None = None,
    ) -> None:
        from services.image_preview_service import build_weather_preview

        async def _build(context):
            return await build_weather_preview(self.bot, context, phase=2)

        await self._run_preview(
            interaction,
            title="Weather — phase 2",
            kind="weather-p2",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="weather-p3",
        description="Preview the weather — phase 3 image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @channel_guard
    @admin_only
    async def test_weather_p3(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        round: int | None = None,
    ) -> None:
        from services.image_preview_service import build_weather_preview

        async def _build(context):
            return await build_weather_preview(self.bot, context, phase=3)

        await self._run_preview(
            interaction,
            title="Weather — phase 3",
            kind="weather-p3",
            division=division,
            round_number=round,
            build=_build,
        )

    @test.command(
        name="weather-mystery",
        description="Preview the mystery notice image for one of your rounds.",
    )
    @app_commands.describe(
        division="The division to draw for. Omit where this server has no season.",
        round="The round number to draw for. Omit where this server has no season.",
    )
    @channel_guard
    @admin_only
    async def test_weather_mystery(
        self,
        interaction: discord.Interaction,
        division: str | None = None,
        round: int | None = None,
    ) -> None:
        from services.image_preview_service import build_weather_preview

        async def _build(context):
            return await build_weather_preview(self.bot, context, phase=0)

        await self._run_preview(
            interaction,
            title="Mystery notice",
            kind="weather-mystery",
            division=division,
            round_number=round,
            build=_build,
        )

    test_calendar.autocomplete("division")(_division_autocomplete)
    test_lineup.autocomplete("division")(_division_autocomplete)
    test_results.autocomplete("division")(_division_autocomplete)
    test_standings.autocomplete("division")(_division_autocomplete)
    test_attendance.autocomplete("division")(_division_autocomplete)
    test_rsvp.autocomplete("division")(_division_autocomplete)
    test_verdict.autocomplete("division")(_division_autocomplete)
    test_weather_p1.autocomplete("division")(_division_autocomplete)
    test_weather_p2.autocomplete("division")(_division_autocomplete)
    test_weather_p3.autocomplete("division")(_division_autocomplete)
    test_weather_mystery.autocomplete("division")(_division_autocomplete)

    async def _send_preview(
        self, interaction: discord.Interaction, *, title: str, context, outcomes
    ) -> None:
        """Return the pictures, and say plainly what the render had to make do with.

        Three things a manager needs and the withdrawn command gave none of: which
        pictures were produced, which assets fell back to a placeholder and why, and
        whether the drivers drawn were their own or invented.
        """
        from services.image_render_service import ImageRenderService

        files: list[discord.File] = []
        header = f"**Preview — {title}** for `{context.division_name}`"
        if context.round is not None:
            header += f", round {context.round.round_number}"
        # The season number is always named, so a manager can tell at a glance which
        # season was drawn rather than inferring it from the picture (FR-004).
        header += f" — season {context.season_number}"
        lines: list[str] = [header]

        if getattr(context, "season_pending_approval", False):
            lines.append(
                "_This season is still pending approval. It is drawn exactly as it will "
                "be once `/season approve` has run._"
            )

        # A manager must never mistake an invented league for their own (FR-024). Said
        # before the pictures rather than after them, because it governs how every line
        # below it should be read.
        if getattr(context, "fabricated_league", False):
            lines.append(
                "⚠️ **This server has no season, so the league drawn here is invented.** "
                "The team names are your own, taken from `/team add`; the division, the "
                "calendar, the round, the circuits and the driver names are all made up, "
                "and differ every time you run this. Nothing has been saved."
            )

        all_notices = []
        for label, template_key, outcome in outcomes:
            if outcome.problem:
                # A problem means no image at all: never a partial one (XIV.4).
                lines.append(f"❌ {label}: {outcome.problem}")
            else:
                lines.append(f"✅ {label}")
                for index, path in enumerate(outcome.png_paths):
                    suffix = f"_{index}" if index else ""
                    files.append(
                        discord.File(str(path), filename=f"{template_key}{suffix}.png")
                    )
            all_notices.extend(outcome.notices)

        # The drivers drawn are invented, and a manager must never mistake them for their
        # own roster (FR-018). Suppressed on a fabricated league, where the banner above
        # has already said so of the whole thing and this would only repeat it — and would
        # tell a manager to seat drivers in a division that does not exist.
        if getattr(context, "fabricated_drivers", False) and not getattr(
            context, "fabricated_league", False
        ):
            lines.append("")
            lines.append(
                "ℹ️ This division has no seated driver, so the names and nationalities "
                "drawn are invented. Seat your drivers to preview your own."
            )

        # Flags absent because the drivers record no nationality, not because the artwork
        # is missing (FR-028). Said plainly, so a maintainer previewing a test-mode roster
        # does not go looking for a fault in their flag directory.
        missing_nationality = getattr(context, "drivers_without_nationality", 0)
        if missing_nationality:
            subject = (
                "1 seated driver records"
                if missing_nationality == 1
                else f"{missing_nationality} seated drivers record"
            )
            lines.append("")
            lines.append(
                f"ℹ️ {subject} no nationality, so they are drawn without a flag — as a "
                f"real posting would draw them. A test-mode driver records one only where "
                f"`/test-mode roster add` was given one."
            )

        # A directory the league configured that could not be resolved, distinguished from
        # a class it never configured (FR-037, FR-038).
        if getattr(context, "directory_faults", None):
            lines.append("")
            lines.append("⚠️ **Asset directories** — these did not resolve as configured:")
            for fault in context.directory_faults:
                lines.append(
                    f"  ↳ `{fault.asset_class}`: `{fault.configured_value}` — {fault.reason}"
                )

        if all_notices:
            lines.append("")
            lines.append("**Notices** — the render survived these:")
            for notice in all_notices:
                where = f" `{notice.field_id}`" if notice.field_id else ""
                lines.append(f"  ⚠️ [{notice.notice_kind}]{where} {notice.detail}")

            await ImageRenderService.report_notices(
                self.bot, interaction.guild_id, all_notices
            )

        await interaction.followup.send(
            "\n".join(lines)[:1900], files=files, ephemeral=True
        )


def _chunk(content: str, limit: int = 1900) -> list[str]:
    """Split a report across Discord's message limit at line boundaries."""
    if len(content) <= limit:
        return [content]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in content.split("\n"):
        if size + len(line) + 1 > limit and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _verify_template_command_coverage() -> None:
    """Fail loudly at import if a template gains no command.

    The fifteen commands below are written out rather than generated, because
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
