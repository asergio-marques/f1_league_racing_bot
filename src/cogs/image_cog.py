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
    TEMPLATE_COLUMNS,
    TEMPLATE_COMMAND_NAMES,
    TEMPLATE_LABELS,
)
from models.image_module import STATE_DISABLED, STATE_ENABLED
from services.image_sample_data import SAMPLE_VERDICT_CASES
from utils.channel_guard import admin_only, channel_guard, server_admin_only
from utils.paths import PathContainmentError, relative_to_root

log = logging.getLogger(__name__)

_STATE_ICONS = {
    STATE_ENABLED: "✅",
    STATE_DISABLED: "❌",
}
_INVALID_ICON = "⚠️"


#: Template key -> the sample variants ``/images test`` draws for it, in order. Two types draw
#: more than one image: the attendance sheet, which must show its point-limit blocks both
#: configured and removed, and the check-in call, whose five rounds exercise four sessions, two
#: sessions, a concealed track, a track with no image file, and a deadline standing at the
#: round's own start (wip-spec section "Test data"). Every other type draws one.
_SAMPLE_VARIANTS: dict[str, tuple] = {
    "attendance_template": ("limits", "no_limits"),
    "rsvp_template": ("sprint", "normal", "mystery", "no_image", "no_deadline"),
    # Six images from one template: the three kinds of verdict, both signs of a time
    # penalty, and free text at five lengths (043).
    "verdicts_template": SAMPLE_VERDICT_CASES,
}


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

        # A stand-in comparison, made *after* validity has passed. Constitution XIV.9
        # (v4.3.0): where a moment can compare the template only against a stand-in for
        # the data that will actually be drawn, a divergence is a **warning** and never a
        # refusal. The filename is stored and the command succeeds.
        warnings = await self._stand_in_warnings(interaction.guild_id, column, proposed)

        await self._config_service.set_field(interaction.guild_id, column, candidate)

        lines = [f"✅ **{label}** template set to `{candidate}`.", "✅ Valid."]
        if warnings:
            lines.append("")
            lines.append(
                "⚠️ **Checked against the teams configured today**, there being no "
                "division to check against yet. These are **not** refusals — the season "
                "you are about to build may well be right — but they will fail "
                "`/season review` if they still stand:"
            )
            lines += [f"  • {warning}" for warning in warnings[:8]]
            if len(warnings) > 8:
                lines.append(f"  • …and {len(warnings) - 8} more")

        await self._reply(interaction, "\n".join(lines))
        await self._log(interaction, f"{label} template = {candidate}")

    async def _stand_in_warnings(
        self, server_id: int, column: str, proposed
    ) -> list[str]:
        """Divergences found against a stand-in for the division (research R5).

        The lineup alone has team-keyed fields, so it is the only type with anything to
        compare here. The stand-in is the season under setup's teams, or the server's team
        configuration where there is no season; where neither exists, no comparison is
        made and nothing is reported.

        Never raises for its own reasons: a fault in this reader must not refuse a
        template that validity has already passed.
        """
        if column != "lineup_template":
            return []

        try:
            from types import SimpleNamespace

            from services.image_lineup_service import binding_from_teams, divergences
            from utils.svg_document import load_svg

            teams = None
            season = await self.bot.season_service.get_setup_season(server_id)  # type: ignore[attr-defined]
            if season is not None:
                divisions = await self.bot.season_service.get_divisions(season.id)  # type: ignore[attr-defined]
                for division in divisions:
                    found = await self.bot.team_service.get_division_teams(division.id)  # type: ignore[attr-defined]
                    if found:
                        # `get_division_teams` answers with dicts; the binding reads
                        # attributes, as it does for the DefaultTeam records below.
                        teams = [SimpleNamespace(**entry) for entry in found]
                        break
            if teams is None:
                teams = await self.bot.team_service.get_default_teams(server_id)  # type: ignore[attr-defined]
            if not teams:
                return []

            from services.image_validity_service import TemplateContext

            path = TemplateContext(
                config=proposed, template_key=column, root=None
            ).resolve()
            return divergences(load_svg(path), binding_from_teams(teams))
        except Exception as exc:  # noqa: BLE001
            log.debug("lineup stand-in comparison could not run: %s", exc)
            return []

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

        lines = [f"✅ **{label}** image output **enabled**."]

        # The toggle records intent; it changes no posted output in this increment
        # (FR-017a). Saying so plainly matters: a manager who enables an aspect and sees
        # no change in the next post would otherwise reasonably think it broken.
        lines.append(
            "⏳ **Not yet in effect** — image posting is wired in a later update. "
            f"Use `/images test` to see what it will produce."
        )

        blocking = await self._aspect_blocking_reasons(server_id, aspect.value)
        if blocking:
            lines.append("")
            lines.append("⚠️ It would not produce an image as configured:")
            lines += [f"  ↳ {reason}" for reason in blocking]

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
        from services.image_validity_service import ImageValidityService

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
                reason = report.reason if report else "not checked"
                lines.append(f"  ⚠️ {TEMPLATE_LABELS[column]}: `{filename}` — {reason}")

        # Invariant 3: never overstate what was checked (FR-028b).
        lines += ["", f"  _{ImageValidityService.depth_summary(template_reports)}_", ""]

        lines.append("**Asset directories**")
        for column, report in directory_reports.items():
            value = getattr(config, column)
            if report.valid:
                lines.append(f"  ✅ {ASSET_LABELS[column]}: `{value}`")
            else:
                lines.append(f"  ⚠️ {ASSET_LABELS[column]}: `{value}` — {report.reason}")

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
            for reason in status.blocking_reasons:
                lines.append(f"      ↳ {reason}")

        lines += [
            "",
            "_Aspects are recorded but not yet in effect — image posting is wired in a "
            "later update. Use `/images test` to see what each will produce._",
        ]
        return lines


    # ── /images test ──────────────────────────────────────────────────────

    @images.command(
        name="test",
        description="Render one kind of image from sample data, to see what it produces.",
    )
    @app_commands.describe(kind="Which kind of image to render.")
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="Calendar", value="calendar"),
            app_commands.Choice(name="Lineup", value="lineup"),
            app_commands.Choice(name="Session results", value="results"),
            app_commands.Choice(name="Standings", value="standings"),
            app_commands.Choice(name="Attendance sheet", value="attendance"),
            app_commands.Choice(name="Check-in call", value="rsvp"),
            app_commands.Choice(name="Weather — phase 1", value="weather-p1"),
            app_commands.Choice(name="Weather — phase 2", value="weather-p2"),
            app_commands.Choice(name="Weather — phase 3", value="weather-p3"),
            app_commands.Choice(name="Weather — mystery notice", value="weather-mystery"),
            app_commands.Choice(name="Verdicts", value="verdicts"),
        ]
    )
    @channel_guard
    @admin_only
    async def test(
        self, interaction: discord.Interaction, kind: app_commands.Choice[str]
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return

        from models.image_constants import TEST_KIND_TEMPLATES
        from services.image_render_service import (
            converter_absent_message,
            converter_available,
        )

        # Defer first: a multi-variant render will not meet the 3-second acknowledgement
        # rule, and the rasteriser is a subprocess.
        await interaction.response.defer(ephemeral=True)

        # Reject at once when the converter is absent, attempting no render (FR-009).
        if not converter_available(use_cache=False):
            await interaction.followup.send(converter_absent_message(), ephemeral=True)
            return

        templates = TEST_KIND_TEMPLATES[kind.value]

        # Three kinds draw against the league's **own** team configuration rather than against
        # invented teams: the lineup, whose fields are keyed by team name, and the results and
        # standings, whose rows carry each team's name and badge. All are rejected outright
        # where there is no team to draw — a generic render failure would not say so
        # (FR-029, FR-047, and FR-063 for the standings).
        teams = None
        needs_teams = {
            "lineup_template",
            "results_qualifying_template",
            "results_race_template",
            "standings_drivers_template",
            "standings_constructors_template",
            "attendance_template",
        } & set(templates)
        if needs_teams:
            teams = await self.bot.team_service.get_default_teams(  # type: ignore[attr-defined]
                interaction.guild_id
            )
            if not [t for t in teams if not getattr(t, "is_reserve", False)]:
                if "lineup_template" in needs_teams:
                    drawn = "lineup"
                elif "attendance_template" in needs_teams:
                    drawn = "attendance sheet"
                else:
                    drawn = "classification"
                await interaction.followup.send(
                    f"⛔ This server holds no team beyond the Reserve team, so there is no "
                    f"{drawn} to draw. Add one with `/team add` first.",
                    ephemeral=True,
                )
                return

        # The attendance sheet, the check-in call and every weather graphic are drawn against
        # a round, and a round is drawn against a track. With no track list there is no sheet
        # to draw, no round for a call to pertain to and no forecast to be made, and a generic
        # render failure would not say so (FR-068, FR-071; 042 FR-058).
        #
        # The notice of a mystery round is the one exception: such a round conceals its track
        # and records none, so the notice needs no track list to be drawn against.
        needs_tracks = {
            key
            for key in templates
            if key in ("attendance_template", "rsvp_template", "verdicts_template")
            or (key.startswith("weather_") and key != "weather_mystery_template")
        }
        if needs_tracks:
            from db.database import get_connection
            from services.track_service import get_all_tracks

            async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
                tracks = await get_all_tracks(db)
            if not tracks:
                if "attendance_template" in needs_tracks:
                    subject = "attendance sheet"
                elif "rsvp_template" in needs_tracks:
                    subject = "check-in call"
                elif "verdicts_template" in needs_tracks:
                    subject = "verdict"
                else:
                    subject = "weather forecast"
                await interaction.followup.send(
                    f"⛔ This server's track list is empty, so there is no round for a "
                    f"{subject} to be drawn against. Add tracks first.",
                    ephemeral=True,
                )
                return

        outcomes = []
        for template_key in templates:
            for variant in _SAMPLE_VARIANTS.get(template_key, (None,)):
                outcomes.append(
                    (
                        template_key,
                        await self._render_sample(
                            interaction.guild_id,
                            template_key,
                            teams=teams,
                            variant=variant,
                        ),
                    )
                )

        await self._send_test_results(interaction, kind, outcomes)

    async def _render_sample(
        self, server_id: int, template_key: str, *, teams=None, variant=None
    ):
        """Render one template from sample data. Reads no live season data (FR-036).

        *teams* is the server's team configuration, needed by the lineup, the results, the
        standings and the attendance sheet, and None for every other kind.

        *variant* selects which of a type's sample cases to draw, for the two types the
        wip-spec asks for more than one image of: the attendance sheet with both point limits
        configured and with both switched off, and the check-in call over its five rounds.
        """
        from services.image_sample_data import build_spec

        service = self.bot.image_render_service  # type: ignore[attr-defined]
        return await service.render(
            server_id,
            template_key,
            lambda root: build_spec(template_key, root, teams=teams, variant=variant),
        )

    async def _send_test_results(
        self, interaction: discord.Interaction, kind, outcomes
    ) -> None:
        """Attach every variant produced, and list every notice alongside (FR-038/40)."""
        from services.image_render_service import ImageRenderService

        files: list[discord.File] = []
        lines: list[str] = [f"**Test render — {kind.name}**"]
        all_notices = []

        for template_key, outcome in outcomes:
            label = TEMPLATE_LABELS[template_key]
            if outcome.problem:
                # A problem means no image at all: never a partial one (XIV.4).
                lines.append(f"❌ {label}: {outcome.problem}")
            else:
                lines.append(f"✅ {label}")
                for path in outcome.png_paths:
                    files.append(discord.File(str(path), filename=f"{template_key}.png"))
            all_notices.extend(outcome.notices)

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
    for group in (ImageCog.images, ImageCog.config, ImageCog.template):
        count = len(group.commands)
        if count > 25:
            raise RuntimeError(
                f"/{group.name} has {count} subcommands; Discord allows at most 25."
            )


_verify_template_command_coverage()
_verify_discord_group_limits()
