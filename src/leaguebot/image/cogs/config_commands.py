"""`ConfigCommands`: the `/images config` subgroup, and the forms it opens.

The folders, the toggles, the presentation preferences, the per-tier colours and the
configuration report. The sixteen template filenames are the `template` subgroup's, in
`template_commands.py`; the folder they are searched in is set here. The per-tier colours open
`TierPaletteModal` and `TierPaletteXmlModal`, which are here with them.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands

from leaguebot.core.models.refusal import Refused
from leaguebot.core.utils.autocomplete import bounded_autocomplete
from leaguebot.core.utils.channel_guard import league_manager_only, changes_nothing
from leaguebot.core.utils.interaction_errors import (
    describe as describe_command,
    describe_form,
)
from leaguebot.core.utils.league_server import LeagueModal
from leaguebot.core.utils.log_lines import refuse
from leaguebot.utils.messages import chunk_message
from leaguebot.core.utils.paths import (
    PathContainmentError,
    relative_to_root,
    resolve_within_project_root,
)
from leaguebot.utils.timezones import clear_zone_cache, zone_names
from leaguebot.image.cogs.image_group import CONFIG_GROUP, ImageSubgroup
from leaguebot.image.services.config.image_config_service import tier_name_refusal
from leaguebot.image.services.config.image_settings import (
    ASPECT,
    DIRECTORY,
    FASTEST_LAP_COLOUR,
    PER_TIER,
    PRESENTATION,
    TEMPLATE_DIRECTORY,
    TIER_COLOURS,
    TIER_IMPORT,
)
from leaguebot.image.services.preview.configuration_report import (
    build_aspect_section,
    build_configuration_report,
)

log = logging.getLogger(__name__)

#: The memoised zone list, which now lives in `utils/timezones.py` so that the round
#: importer can validate a zone without importing this cog — and Discord with it. Kept
#: under its old name here because the autocomplete below and its tests both reach for it,
#: and the move is not a change to either. `clear_zone_cache` is re-exported above for the
#: same reason.
_zone_names = zone_names


#: The largest palette file a league may attach. A palette is a few hundred bytes per
#: tier; anything past this is not one, and reading it costs the host memory before the
#: parser can say so.
MAX_PALETTE_IMPORT_BYTES = 100_000


class TierPaletteModal(LeagueModal, title="Set one tier's colours"):
    """Paste a whole palette for one division rather than running ten commands.

    The tool's annotated output pastes in whole — its commentary lines start with `#`,
    which `parse_palette_lines` skips.
    """

    block: discord.ui.TextInput = discord.ui.TextInput(
        label="One `slot colour` per line",
        style=discord.TextStyle.paragraph,
        placeholder="accent #A78BFA\nink #F7F6F8\npage #161517",
        required=True,
        max_length=4000,
    )

    def __init__(self, cog, division: str) -> None:
        super().__init__()
        self._cog = cog
        self._division = division

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await _module_still_on(self._cog, interaction, self):
            return
        await self._cog.apply_tier_block(
            interaction, self._division, self.block.value, form=self
        )


class TierPaletteXmlModal(LeagueModal, title="Import tier colours"):
    """The same, for several divisions at once, where no file was attached."""

    payload: discord.ui.TextInput = discord.ui.TextInput(
        label="XML payload",
        style=discord.TextStyle.paragraph,
        placeholder='<palettes><division name="Division 1">'
                    '<colour slot="accent">#3DD6F5</colour></division></palettes>',
        required=True,
        max_length=4000,
    )

    def __init__(self, cog) -> None:
        super().__init__()
        self._cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await _module_still_on(self._cog, interaction, self):
            return
        await self._cog.apply_tier_xml(interaction, self.payload.value, form=self)


async def _module_still_on(cog, interaction: discord.Interaction, form: discord.ui.Modal) -> bool:
    """Whether the Image module is still on as *form* is submitted; where it is not, refuse.

    A form can stay open on a manager's screen while `/module disable images` is run, and what
    it would store belongs to a module that is off. Nothing is written then: the submit is
    refused and recorded under the form's name.
    """
    if await cog.bot.module_service.is_images_enabled():
        return True
    await refuse(
        interaction,
        "❌ The Image module was switched off while this form was open. Nothing was stored.",
        what=describe_form(form),
    )
    return False


class ConfigCommands(ImageSubgroup):
    config = CONFIG_GROUP

    async def _set_directory(
        self, interaction: discord.Interaction, column: str, value: str
    ) -> None:
        """Shared body for the eight asset directories.

        A path that escapes the project root is rejected here, at once, at the point of
        configuration, rather than surfacing as a render failure later (FR-011, FR-016). The
        stored value is left unchanged on rejection. What passes is asked of the change queue
        (`image_settings`), which stores it and says whether the folder resolves.
        """
        if not await self._module_gate(interaction):
            return

        try:
            resolved = resolve_within_project_root(value)
        except PathContainmentError as exc:
            await refuse(
                interaction,
                f"❌ {exc}\nDirectories must sit inside the project root. "
                f"The stored value is unchanged.",
                what=describe_command(interaction),
            )
            return
        except Refused as exc:
            await refuse(interaction, f"❌ {exc}", what=describe_command(interaction))
            return

        await self.bot.change_queue.ask(
            DIRECTORY,
            {"column": column, "value": relative_to_root(resolved)},
            interaction=interaction,
            what=describe_command(interaction),
        )

    # ── /images config template-directory ─────────────────────────────────

    @config.command(
        name="template-directory",
        description="Set the folder searched for SVG template files.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @league_manager_only
    async def config_template_directory(
        self, interaction: discord.Interaction, directory: str
    ) -> None:
        await self._set_template_directory(interaction, directory)

    async def _set_template_directory(
        self, interaction: discord.Interaction, directory: str
    ) -> None:
        """Validate, **then** store (FR-005) — as the sixteen filename commands do.

        This is the one directory with no packaged second tier. Every asset class falls
        back to what the bot ships, so an empty artwork folder still draws; the template
        directory is the only place templates are searched, so a folder that does not hold
        all sixteen, valid, is a configuration that cannot produce a single graphic.

        Refusing it here refuses it at the one moment the manager is present, holding the
        files, and able to fix it — rather than letting it surface as a render failure at
        the next scheduled post, when nobody is looking.
        """
        if not await self._module_gate(interaction):
            return

        try:
            resolved = resolve_within_project_root(directory)
        except PathContainmentError as exc:
            await refuse(
                interaction,
                f"❌ {exc}\nDirectories must sit inside the project root. "
                f"The stored value is unchanged.",
                what=describe_command(interaction),
            )
            return
        except Refused as exc:
            await refuse(interaction, f"❌ {exc}", what=describe_command(interaction))
            return

        stored = relative_to_root(resolved)

        # Sixteen SVG parses will not reliably finish inside Discord's three seconds.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # The check at the ask is the survey, and the change runs it again as it comes up.
        await self.bot.change_queue.ask(
            TEMPLATE_DIRECTORY,
            {"value": stored},
            interaction=interaction,
            what=describe_command(interaction),
        )

    # ── The eight asset directory commands ───────────────────────────────
    #
    # Identical in shape to `template-directory`; only the column differs. Each is
    # subject to the same project-root containment rejection (FR-016).

    @config.command(
        name="track-image-directory",
        description="Set the folder searched for circuit images.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @league_manager_only
    async def config_track_image_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "track_image_directory", directory)

    @config.command(
        name="team-image-directory",
        description="Set the folder searched for team logos, badges and cars.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @league_manager_only
    async def config_team_image_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "team_image_directory", directory)

    @config.command(
        name="flag-directory",
        description="Set the folder searched for driver nationality flags.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @league_manager_only
    async def config_flag_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "flag_directory", directory)

    @config.command(
        name="driver-image-directory",
        description="Set the folder searched for driver portraits.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @league_manager_only
    async def config_driver_image_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "driver_image_directory", directory)

    @config.command(
        name="marker-directory",
        description="Set the folder searched for markers and result marks.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @league_manager_only
    async def config_marker_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "marker_directory", directory)

    @config.command(
        name="weather-icon-directory",
        description="Set the folder searched for weather condition icons.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @league_manager_only
    async def config_weather_icon_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "weather_icon_directory", directory)

    @config.command(
        name="tyre-directory",
        description="Set the folder searched for tyre compound icons.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @league_manager_only
    async def config_tyre_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "tyre_directory", directory)

    @config.command(
        name="division-logo-directory",
        description="Set the folder searched for division logos.",
    )
    @app_commands.describe(directory="Path relative to the project root.")
    @league_manager_only
    async def config_division_logo_directory(self, interaction: discord.Interaction, directory: str) -> None:
        await self._set_directory(interaction, "division_logo_directory", directory)

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
            app_commands.Choice(name="Verdict banner", value="verdict_banner"),
        ]
    )
    @league_manager_only
    async def config_toggle(
        self, interaction: discord.Interaction, aspect: app_commands.Choice[str]
    ) -> None:
        # Switching an output *on* reports what still blocks it, and answering that means
        # the setting's check (`aspect_blocking_reasons`) → `template_reports` → `evaluate_all_templates`,
        # which parses all sixteen template SVGs from disk on every call and caches
        # nothing. That is a third of a second on a development machine and several times
        # that on the Raspberry Pi's SD card, so the reply arrived after Discord had
        # already expired the token (404 Unknown interaction) with the toggle written.
        await interaction.response.defer(ephemeral=True)

        if not await self._module_gate(interaction):
            return

        # The flip is resolved here, from the state read now (image :180): the change asked is
        # the state opposite the one the member saw. Switching *off* is always allowed, the
        # output reverting to text. Switching *on* is refused while the drawings behind it are
        # unusable, the output left off; the setting's check makes that refusal at the ask and
        # again as the change starts.
        enabled = not await self._config_service.is_aspect_enabled(aspect.value)
        await self.bot.change_queue.ask(
            ASPECT,
            {"aspect": aspect.value, "enabled": enabled},
            interaction=interaction,
            what=describe_command(interaction),
        )

    # ── Presentation preferences ──────────────────────────────────────────

    @config.command(
        name="fastest-lap-colour",
        description="Set the colour distinguishing the fastest lap of a race.",
    )
    @app_commands.describe(colour="A '#' followed by exactly six hex digits, e.g. #A020F0.")
    @league_manager_only
    async def config_fastest_lap_colour(
        self, interaction: discord.Interaction, colour: str
    ) -> None:
        # Measuring the contrast reads the race results template through
        # `template_reports` → `evaluate_all_templates`, which parses all sixteen templates:
        # 1.7 to 2.1 seconds on the Pi (measured 2026-09-24) before this command's own
        # queries, against Discord's three. Answering late lands on an expired token with
        # the colour already stored and the contrast — the point of the reply — lost.
        await interaction.response.defer(ephemeral=True)

        if not await self._module_gate(interaction):
            return

        from leaguebot.image.utils.colour import InvalidColour, normalise_hex

        # 1. Reject a malformed value, leaving the stored colour untouched (FR-025).
        try:
            canonical = normalise_hex(colour)
        except InvalidColour as exc:
            await refuse(
                interaction,
                f"❌ {exc}\nThe stored colour is unchanged.",
                what=describe_command(interaction),
            )
            return

        # 2. Ask the change. The setting stores the colour *before* it measures it: an
        #    unmeasurable contrast must never cost the manager their input (FR-026, FR-027).
        await self.bot.change_queue.ask(
            FASTEST_LAP_COLOUR,
            {"value": canonical},
            interaction=interaction,
            what=describe_command(interaction),
        )

    # ── Per-tier colours (051) ────────────────────────────────────────────

    @config.command(
        name="per-tier-colour-toggle",
        description="Turn per-tier template colours on or off.",
    )
    @app_commands.describe(
        enable="Whether your templates should take each tier's own colours."
    )
    @league_manager_only
    async def config_per_tier_colour_toggle(
        self, interaction: discord.Interaction, enable: bool
    ) -> None:
        await self._set_per_tier_colours(interaction, enable)

    async def _set_per_tier_colours(self, interaction, enable: bool) -> None:
        """Shared body for the toggle, so it can be exercised without a gateway.

        The same split `_set_directory` uses and for the same reason: the command itself is
        wrapped by its tier guard and cannot be invoked in a test.
        """
        # Switching on reports the colour shortfall, which `colour_shortfall` reads off
        # `template_reports` → `evaluate_all_templates`: the sixteen-template sweep that
        # outran Discord's three seconds on the Pi for `config_toggle`. Deferred on both
        # settings rather than the one, so the body has a single way of answering.
        await interaction.response.defer(ephemeral=True)

        if not await self._module_gate(interaction):
            return

        # The colour shortfall switching on reports is the setting's advice, read after the
        # save; a setting found already as asked when it runs changes nothing and says so.
        await self.bot.change_queue.ask(
            PER_TIER,
            {"enabled": enable},
            interaction=interaction,
            what=describe_command(interaction),
        )

    @config.command(
        name="per-tier-set-colour",
        description="Set one colour slot for one tier.",
    )
    @app_commands.describe(
        division="The division the colour applies to.",
        slot="The slot id your template marks, for example `accent`.",
        colour="A '#' followed by exactly six hex digits, e.g. #A78BFA.",
    )
    @league_manager_only
    async def config_per_tier_set_colour(
        self, interaction: discord.Interaction, division: str, slot: str, colour: str
    ) -> None:
        await self._set_tier_colour(interaction, division, slot, colour)

    async def _set_tier_colour(
        self, interaction, division: str, slot: str, colour: str
    ) -> None:
        """Shared body for the setter. See `_set_per_tier_colours` for why it is split."""
        # Whether any template marks the slot is the setting's advice, read after the save from
        # `template_reports` and so the whole sixteen-template sweep. Deferred first, as
        # `config_toggle` is, so the refusals below answer on the followup too.
        await interaction.response.defer(ephemeral=True)

        if not await self._module_gate(interaction):
            return

        from leaguebot.image.utils.colour import InvalidColour, normalise_hex
        from leaguebot.image.utils.svg_palette import InvalidSlot, normalise_slot

        # Each rejection stores nothing and names the input that was wrong: a manager who
        # mistyped a slot and one who mistyped a colour are looking for different things.
        unstorable = tier_name_refusal(division)
        if unstorable is not None:
            await refuse(
                interaction,
                f"❌ {unstorable} Nothing was stored.",
                what=describe_command(interaction),
            )
            return
        try:
            canonical_slot = normalise_slot(slot)
        except InvalidSlot as exc:
            await refuse(
                interaction, f"❌ {exc}\nNothing was stored.", what=describe_command(interaction)
            )
            return
        try:
            canonical_colour = normalise_hex(colour)
        except InvalidColour as exc:
            await refuse(
                interaction, f"❌ {exc}\nNothing was stored.", what=describe_command(interaction)
            )
            return

        # What no template marks, and per-tier colours being off, are said after the save, once
        # the templates have been read: the slot is stored all the same.
        await self.bot.change_queue.ask(
            TIER_COLOURS,
            {
                "division": division,
                "colours": {canonical_slot: canonical_colour},
                "form": False,
            },
            interaction=interaction,
            what=describe_command(interaction),
        )

    @config.command(
        name="per-tier-bulk-colour",
        description="Set several of one tier's colour slots at once.",
    )
    @app_commands.describe(division="The division the colours apply to.")
    @league_manager_only
    async def config_per_tier_bulk_colour(
        self, interaction: discord.Interaction, division: str
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await interaction.response.send_modal(TierPaletteModal(self, division))

    async def apply_tier_block(
        self, interaction, division: str, text: str, *, form: discord.ui.Modal | None = None
    ) -> None:
        """Store a pasted palette for one tier, or refuse the whole of it.

        *form* is the form the palette was pasted into: a refusal is recorded under its name,
        and the success under the command that opened it.

        Nothing is written while any line is faulty. The division is the unit of
        atomicity, and a tier drawn in four of its ten colours looks deliberate — which is
        worse than one that is plainly unconfigured.
        """
        from leaguebot.image.utils.palette_import import parse_palette_lines

        unstorable = tier_name_refusal(division)
        if unstorable is not None:
            await refuse(
                interaction,
                f"❌ {unstorable} Nothing was stored.",
                what=describe_form(form) if form is not None else describe_command(interaction),
            )
            return

        colours, problems = parse_palette_lines(text)
        if problems:
            listed = "\n".join(f"  • {problem}" for problem in problems)
            header = f"❌ Nothing was stored — {len(problems)} line(s) could not be read:"
            await refuse(
                interaction,
                f"{header}\n{listed}",
                what=describe_form(form) if form is not None else describe_command(interaction),
                reason=f"{len(problems)} line(s) could not be read:\n{listed}",
            )
            return

        await self.bot.change_queue.ask(
            TIER_COLOURS,
            {"division": division, "colours": colours, "form": True},
            interaction=interaction,
            what=describe_form(form) if form is not None else describe_command(interaction),
        )

    @config.command(
        name="colour-xml-import",
        description="Import colours for several tiers at once (file attachment or modal).",
    )
    @app_commands.describe(file="Optional XML file. Omit it to paste into a modal instead.")
    @league_manager_only
    async def config_colour_xml_import(
        self, interaction: discord.Interaction, file: discord.Attachment | None = None
    ) -> None:
        if not await self._module_gate(interaction):
            return

        if file is None:
            await interaction.response.send_modal(TierPaletteXmlModal(self))
            return

        await interaction.response.defer(ephemeral=True)
        # The size Discord reports is checked before the file is read: an oversized file is
        # refused without being downloaded whole.
        if file.size > MAX_PALETTE_IMPORT_BYTES:
            await refuse(
                interaction,
                f"❌ File is too large (max {MAX_PALETTE_IMPORT_BYTES // 1000} KB).",
                what=describe_command(interaction),
            )
            return
        raw = await file.read()
        if not raw:
            await refuse(
                interaction, "❌ The attached file is empty.", what=describe_command(interaction)
            )
            return
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            await refuse(
                interaction,
                "❌ File could not be decoded as UTF-8.",
                what=describe_command(interaction),
            )
            return

        await self.apply_tier_xml(interaction, text)

    async def apply_tier_xml(
        self, interaction, text: str, *, form: discord.ui.Modal | None = None
    ) -> None:
        """Import many tiers, rejecting a block at a time (decided 2026-09-08).

        A document that cannot be parsed is refused whole, there being nothing to salvage from
        it, and so is one in which every block is rejected. Past that, each division stands or
        falls alone, so one mistyped tier does not cost a league the other four. *form* is the
        form the document was pasted into: a refusal is recorded under its name, and the
        success under the command that opened it.
        """
        from leaguebot.image.utils.palette_import import PaletteXmlError, parse_palette_xml

        refused = describe_form(form) if form is not None else describe_command(interaction)
        try:
            blocks, problems = parse_palette_xml(text)
        except PaletteXmlError as exc:
            listed = "\n".join(f"  • {error}" for error in exc.errors)
            await refuse(
                interaction,
                f"❌ Nothing was stored:\n{listed}",
                what=refused,
                reason=f"the document could not be read:\n{listed}",
            )
            return

        # A name nothing can be stored under is passed over with the rest that were rejected.
        storable = []
        for block in blocks:
            unstorable = tier_name_refusal(block.division)
            if unstorable is None:
                storable.append(block)
            else:
                problems.append(unstorable)
        blocks = storable

        if problems and not blocks:
            listed = "\n".join(f"  • {problem}" for problem in problems)
            await refuse(
                interaction,
                f"⚠️ {len(problems)} block(s) were not imported:\n{listed}",
                what=refused,
                reason=f"every block was rejected:\n{listed}",
            )
            return

        await self.bot.change_queue.ask(
            TIER_IMPORT,
            {
                "blocks": [
                    {"division": block.division, "colours": dict(block.colours)}
                    for block in blocks
                ],
                "passed_over": list(problems),
            },
            interaction=interaction,
            what=describe_form(form) if form is not None else describe_command(interaction),
        )

    @config.command(
        name="time-zone",
        description="Set the time zone times are displayed in on images.",
    )
    @app_commands.describe(zone="An IANA zone name, e.g. Europe/Lisbon.")
    @league_manager_only
    async def config_time_zone(self, interaction: discord.Interaction, zone: str) -> None:
        if not await self._module_gate(interaction):
            return

        # An unknown zone is refused by the setting's check, at the ask and again as the change
        # starts.
        candidate = zone.strip()
        await self.bot.change_queue.ask(
            PRESENTATION,
            {"column": "time_zone", "value": candidate, "shown": candidate},
            interaction=interaction,
            what=describe_command(interaction),
        )

    @config_time_zone.autocomplete("zone")
    @bounded_autocomplete()
    async def _time_zone_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete over the IANA zones.

        There are several hundred — far past what a static choice list holds — which is
        why this is a free-text parameter with completion rather than `@choices`.

        The list itself comes from `_zone_names`, which builds it once; see there for why.
        """
        needle = (current or "").strip().casefold()
        matches = [
            zone for zone, folded in _zone_names() if not needle or needle in folded
        ]
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
    @league_manager_only
    async def config_time_format(
        self, interaction: discord.Interaction, clock: app_commands.Choice[str]
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await self.bot.change_queue.ask(
            PRESENTATION,
            {"column": "time_format", "value": clock.value, "shown": clock.name},
            interaction=interaction,
            what=describe_command(interaction),
        )

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
            # Written out. A calendar is read rather than parsed, and a league that wants
            # it to look like a poster wants the month and the weekday spelled.
            app_commands.Choice(
                name="Sunday 14th June 2026", value="DDDD_ORD_MONTH_YYYY"
            ),
            app_commands.Choice(name="14th June 2026", value="ORD_MONTH_YYYY"),
            app_commands.Choice(
                name="Sunday 14 June 2026", value="DDDD_DD_MONTH_YYYY"
            ),
            app_commands.Choice(name="14 June 2026", value="DD_MONTH_YYYY"),
            app_commands.Choice(name="June 14, 2026", value="MONTH_DD_YYYY"),
            app_commands.Choice(
                name="Sunday, June 14th, 2026", value="DDDD_MONTH_ORD_YYYY"
            ),
        ]
    )
    @league_manager_only
    async def config_date_format(
        self, interaction: discord.Interaction, style: app_commands.Choice[str]
    ) -> None:
        if not await self._module_gate(interaction):
            return
        await self.bot.change_queue.ask(
            PRESENTATION,
            {"column": "date_format", "value": style.value, "shown": style.name},
            interaction=interaction,
            what=describe_command(interaction),
        )

    # ── /images config view ───────────────────────────────────────────────

    @config.command(
        name="view",
        description="Show the whole image configuration and whether it holds together.",
    )
    @league_manager_only
    @changes_nothing
    async def config_view(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction, record=False):
            return

        await interaction.response.defer(ephemeral=True)

        text = await self.build_configuration_report()
        for chunk in chunk_message(text):
            await interaction.followup.send(chunk, ephemeral=True)

    async def build_configuration_report(self) -> str:
        """The configuration and its validity, as `preview/configuration_report.py` renders it."""
        return await build_configuration_report(self._config_service, self._validity_service)

    async def build_aspect_section(self) -> list[str]:
        """The nine aspects in their three states, as the report renders them."""
        return await build_aspect_section(self._validity_service)

    _division_autocomplete = ImageSubgroup._division_autocomplete

    # The per-tier colour command takes a division too, and completes it the same way.
    config_per_tier_set_colour.autocomplete("division")(_division_autocomplete)
    config_per_tier_bulk_colour.autocomplete("division")(_division_autocomplete)
