"""SeasonCog — /season, /division, /round command groups.

Commands:
  /season setup    — start season configuration (admin only)
  /season placements-review   — view pending config with Approve/Amend actions
  (placements are confirmed from the button `/season placements-review` posts, and by no command)
  /season status   — read-only summary of active season
  /season abort    — delete a season whose placements were never confirmed (league admin only)
  /season cancel   — cancel the season being raced, archiving it (league admin only, irreversible)

  /division add       — add a division to pending setup
  /division duplicate — copy a division with datetime offset (setup only)
  /division delete    — remove a division from pending setup
  /division rename    — rename a division while the season is in placements
  /division amend     — amend a division's name, tier or role while the season is in placements
  /division cancel    — cancel a division in the active season

  /round add      — add a round to pending setup (auto-numbered by date)
  /round add-bulk — add many rounds to one division from a pasted list
  /round add-xml  — add rounds to one or more divisions from XML
  /round delete   — remove a round and renumber (setup only)
  /round cancel   — cancel a round in the active season
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.db.database import AUTOCOMPLETE_TIMEOUT_SECONDS, get_connection
from leaguebot.core.models.round import Round as RoundModel
from leaguebot.core.models.round import RoundFormat, RoundStatus
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services import approval_checks
from leaguebot.core.services.cancellation_changes import (
    DIVISION_CANCEL,
    ROUND_CANCEL,
    SEASON_CANCEL,
    cancellation_holding_amendment,
)
from leaguebot.core.services.division_settings import NO_SEASON_REFUSAL, division_not_found
from leaguebot.core.services.image_drawing import Held
from leaguebot.core.services.round_amend_change import ROUND_AMEND, payload_changes
from leaguebot.core.services.round_moments import read_round_windows
from leaguebot.core.services.confirmation_refusals import Confirmation, read_refusals
from leaguebot.core.services.review_sections import Review, read_review_sections
from leaguebot.core.services.division_add_change import KIND as DIVISION_ADD
from leaguebot.core.services.division_duplicate_change import KIND as DIVISION_DUPLICATE
from leaguebot.core.services.division_delete_change import DIVISION_DELETE
from leaguebot.core.services.division_edit_changes import DIVISION_AMEND, DIVISION_RENAME
from leaguebot.core.services.round_add_changes import (
    ADD as ROUND_ADD,
    IMPORT as ROUND_IMPORT,
    add_payload,
    import_payload,
)
from leaguebot.core.services.season_setup_change import KIND as SEASON_SETUP
from leaguebot.core.services.round_delete_change import ROUND_DELETE
from leaguebot.core.services.setup_round_amend_change import (
    ROUND_AMEND_SETUP,
    amend_payload as amend_setup_payload,
)
from leaguebot.core.services.season_review_changes import POST as SEASON_REVIEW_POST, REVIEW_WINDOW
from leaguebot.core.services.season_end_changes import (
    ABORT_ONLY_BEFORE,
    PRE_CONFIRMATION,
    SEASON_ABORT,
    SEASON_COMPLETE,
    season_end_in_hand,
    season_end_refusal,
)
from leaguebot.core.services.amendment_rules_service import amendment_changes_nothing
import leaguebot.core.services.track_service as track_service
from leaguebot.core.services.season_service import (
    SeasonCapacityCounts,
    SeasonImmutableError,
)
from leaguebot.core.utils.autocomplete import bounded_autocomplete
from leaguebot.core.utils.input_validator import parse_datetime
from leaguebot.core.utils.channel_guard import (
    league_admin_only,
    league_manager_only,
    changes_nothing,
)
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.formatting import discord_ts, format_roster_block
from leaguebot.core.utils.interaction_errors import describe, describe_form, report_failure
from leaguebot.core.utils.league_server import LeagueModal, LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.messages import chunk_message
from leaguebot.core.utils.round_import import (
    ParsedDivisionRounds,
    format_import_errors as _format_import_errors,
    parse_bulk_round_lines,
    parse_round_xml,
)

log = logging.getLogger(__name__)

#: The review's five minutes, as its question says them.
REVIEW_MINUTES = REVIEW_WINDOW // timedelta(minutes=1)

# ---------------------------------------------------------------------------
# Bulk round import
# ---------------------------------------------------------------------------

class BulkRoundModal(LeagueModal, title="Add rounds in bulk"):
    """The pasted calendar of one division, a round per line.

    Times are stated in UTC, which is what a round is stored in — so unlike the XML
    import this format cannot meet a daylight-saving gap, and needs no zone.
    """

    entries: discord.ui.TextInput = discord.ui.TextInput(
        label="datetime UTC, format, track — one per line",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "2026-06-14T18:00, Normal, 14\n"
            "2026-06-21T18:00, Sprint, Hungaroring\n"
            "2026-06-28T18:00, Mystery"
        ),
        required=True,
        max_length=2000,
    )

    def __init__(self, division_name: str) -> None:
        super().__init__()
        self._division_name = division_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        rounds, errors = parse_bulk_round_lines(self.entries.value)
        if errors:
            await refuse(
                interaction,
                _format_import_errors(errors),
                what=describe_form(self),
                reason="\n".join(errors),
            )
            return
        if not rounds:
            await refuse(
                interaction,
                "❌ Nothing to add — no rounds were given.",
                what=describe_form(self),
            )
            return

        await _run_round_import(
            interaction,
            [ParsedDivisionRounds(division_name=self._division_name, rounds=rounds)],
            source="/round add-bulk",
            what=describe_form(self),
        )


class XmlRoundModal(LeagueModal, title="Add rounds from XML"):
    """A calendar of one or more divisions, each round stated in its local time."""

    payload: discord.ui.TextInput = discord.ui.TextInput(
        label="XML payload",
        style=discord.TextStyle.paragraph,
        # A placeholder is capped at 100 characters, which the full schema does not fit
        # in — Discord refuses the whole modal with a 400 rather than truncating it. So
        # this names the elements rather than laying them out; the shape is in the
        # README and in `docs/how-to/configuring-the-core-bot.md`.
        placeholder=(
            '<config><division name="Pro"><round>'
            "<datetime><timezone><format><track>"
        ),
        required=True,
        max_length=4000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        divisions, errors = parse_round_xml(self.payload.value)
        if errors:
            await refuse(
                interaction,
                _format_import_errors(errors),
                what=describe_form(self),
                reason="\n".join(errors),
            )
            return
        if not any(entry.rounds for entry in divisions):
            await refuse(
                interaction,
                "❌ Nothing to add — no rounds were given.",
                what=describe_form(self),
            )
            return

        await _run_round_import(
            interaction, divisions, source="/round add-xml", what=describe_form(self)
        )


async def _run_round_import(
    interaction: discord.Interaction,
    parsed: list[ParsedDivisionRounds],
    *,
    source: str,
    what: str,
) -> None:
    """Ask the change queue for a parsed calendar. Shared by both import modals.

    The interaction is already deferred. The queue refuses what the import has always been refused
    for, at the ask and again as the change starts, and adds every round across its divisions in
    one save (`core/services/round_add_changes.py`). *what* names the form as the log channel
    should read a refusal of it, which the form passes in (`describe_form`), this function holding
    no form.
    """
    await bot_of(interaction).change_queue.ask(
        ROUND_IMPORT, import_payload(source, parsed), interaction=interaction, what=what,
    )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class _ReviewPoster:
    """Sends a season review's private messages: the faults a manager has to put right.

    A review's public messages are not sent from the command at all: the change queue posts them
    (`season.review.post`, #439), so a refused post stops the queue and the review comes down by
    its take-down. What the command still says itself is said to the reviewer alone, an ephemeral
    followup that cannot be deleted by id, and the fault reports among them are the reason a
    manager knows what to fix. Every one is handed this rather than the interaction, so the type
    check sees every send (#228); `tests/core/test_season_review_images.py` holds that nothing in
    a review sends around it.
    """

    def __init__(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction

    async def reply(self, content: str, *, ephemeral: Literal[True]) -> None:
        """Tell the reviewer *content*, privately."""
        await self.interaction.followup.send(content, ephemeral=True)


class SeasonCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /season group
    # ------------------------------------------------------------------

    season = app_commands.Group(
        name="season",
        description="Season management commands",
        guild_only=True,
        default_permissions=None,
    )

    @season.command(
        name="setup",
        description="Start season configuration (admin only).",
    )
    @app_commands.describe(
        game_edition="Game edition year (e.g. 25 for F1 25). Required.",
    )
    @league_manager_only
    async def season_setup(
        self,
        interaction: discord.Interaction,
        game_edition: app_commands.Range[int, 1, 9999],
    ) -> None:
        """Begin season setup: ask the change queue for the season, which refuses a setup in
        progress or a season being raced at the ask and again as it starts
        (`core/services/season_setup_change.py`)."""
        await self.bot.change_queue.ask(
            SEASON_SETUP, {"game_edition": game_edition}, interaction=interaction,
            what=describe(interaction),
        )

    async def _review_picture_gates(self) -> tuple[bool, bool]:
        """Whether the league conveys its calendar, and its lineup, as pictures: the gates the
        review draws by, read once for the whole review. Both gates are stated exceptions of
        their own (`ImageDrawing.calendar_wanted`, `ImageDrawing.lineup_wanted`;
        architecture.md, "A catch-all error handler"), so a fault in either already answers
        False there and needs no catch-all here."""
        drawing = self.bot.image_drawing
        return await drawing.calendar_wanted(), await drawing.lineup_wanted()

    async def _ask_review(
        self,
        interaction: discord.Interaction,
        command: str,
        season_id: int,
        report: list[str],
        pictures: list[dict[str, Any]],
        *,
        offered: bool,
        view: str,
        question: str | None = None,
    ) -> None:
        """Ask the change queue to post the review the command has computed (`season.review.post`).

        The command posts nothing public itself: the queue posts the pictures after a notice
        that they are being drawn, then the *report*'s sections, then the question where the
        review *offered* it, in the channel the command was run in. The reply is the queue's
        acknowledgement, updated with the outcome, and a stopped queue holds the review whole.

        *question* is the text of the question (the placements review's by default); *view*
        names the button's family, as the builder's factory knows it. The evidence the report
        was computed from is taken here, now that the report is complete, and kept with the
        question for the press to compare the season against.
        """
        from leaguebot.core.services.season_fingerprint_service import take_fingerprint

        evidence = None
        if offered:
            evidence = (
                await take_fingerprint(
                    self.bot.db_path, season_id, drawing=self.bot.image_drawing
                )
            ).areas
            if question is None:
                question = (
                    f"\U0001f4cb **Do you accept this season configuration?**\n"
                    f"Reviewed by <@{interaction.user.id}>. Approval is open to them or to a "
                    f"server administrator for the next {REVIEW_MINUTES} minutes, "
                    f"after which this review expires and must be run again."
                )
        await self.bot.change_queue.ask(
            SEASON_REVIEW_POST,
            {
                "channel_id": interaction.channel_id,
                "season_id": season_id,
                "reviewer_id": interaction.user.id,
                "review": command,
                "pictures": pictures,
                "report": report,
                "question": question if offered else None,
                "evidence": evidence,
                "view": view,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    async def _calendar_capacity_warning(
        self, season_id: int
    ) -> list[str]:
        """Compare the calendar template against the season's most demanding division.

        A **warning**, never a refusal (Constitution XIV.9). Season review can only compare
        against the greatest round count any division holds; which division is actually
        drawn is decided later, and the same divergence is fatal at that moment. Reporting
        it here as a failure would refuse a season that may well draw perfectly.

        Counts in a core service and asks the drawing on the bot. A check that cannot run —
        its own count, or the drawing — lets the season through (`approval_checks.ask_drawing`'s stated
        exception, architecture.md, "A catch-all error handler").
        """
        async def longest() -> Held | None:
            counts = await self.bot.season_service.capacity_counts(season_id)
            return counts.longest_calendar

        async def warning(longest: Held) -> list[str]:
            problem = await self.bot.image_drawing.calendar_review_problem(longest)
            if problem is None:
                return []
            return [f"  ⚠️ {problem.problem}. {problem.remedy}", ""]

        return await approval_checks.ask_drawing(
            "season review: calendar", longest, warning, nothing=[]
        )

    async def _attendance_capacity_warning(
        self, season_id: int
    ) -> list[str]:
        """Compare the attendance sheet, the standings and the check-in call against the
        season's most demanding data.

        Warnings, never refusals (Constitution XIV.9): the sheet's rounds against the
        division holding the most, the standings against the division seating the most
        drivers and the one fielding the most race teams, and the check-in call's sessions
        against the largest number any round of the season is run over. Which division and
        which round are actually drawn is decided later, and the same divergence is fatal
        at that moment.

        The sheet's **rows** are not compared here. They are guarded at the command that would
        overflow them — a driver assignment — which is the earlier moment XIV.12 requires, and
        where the change can still be left unapplied.

        One check, as `_calendar_capacity_warning` is: a fault in the season's own counts or
        in asking the drawing drops the sheet, the standings and the check-in call together
        and lets the review through (`approval_checks.ask_drawing`'s stated exception, architecture.md, "A
        catch-all error handler").
        """
        async def counted() -> SeasonCapacityCounts:
            return await self.bot.season_service.capacity_counts(season_id)

        async def warnings(counts: SeasonCapacityCounts) -> list[str]:
            lines: list[str] = []

            if counts.longest_calendar is not None:
                problem = await self.bot.image_drawing.sheet_review_problem(
                    counts.longest_calendar
                )
                if problem is not None:
                    lines.append(f"  ⚠️ {problem.problem}. {problem.remedy}")

            if (
                counts.most_drivers is not None
                and counts.most_teams is not None
                and counts.longest_calendar is not None
            ):
                lines += await self._standings_capacity_lines(
                    counts.most_drivers, counts.most_teams, counts.longest_calendar
                )

            call_problem = await self.bot.image_drawing.checkin_review_problem(
                counts.longest_sessions
            )
            if call_problem is not None:
                lines.append(f"  ⚠️ {call_problem.problem}. {call_problem.remedy}")

            if lines:
                lines.append("")
            return lines

        return await approval_checks.ask_drawing(
            "season review: attendance", counted, warnings, nothing=[]
        )

    async def _standings_capacity_lines(
        self, drivers: Held, teams: Held, longest: Held
    ) -> list[str]:
        """What the two standings templates could not draw for this season (FR-043), worded
        around the image module's answer.

        *drivers* is the division seating the most, *teams* the one fielding the most race
        teams, and *longest* the one holding the most rounds — each `Held`, from
        `SeasonService.capacity_counts`. The drivers **row** ceiling is also guarded at a
        driver assignment, which is the earlier moment XIV.12 asks for. This is the
        season-wide view of the same rule, and the only place the constructors ceiling can
        be caught at all: seating a driver adds no team.
        """
        problems = await self.bot.image_drawing.standings_review_problems(
            drivers, teams, longest
        )
        return [f"  ⚠️ {problem.problem}. {problem.remedy}" for problem in problems]

    async def _build_image_review_section(self) -> list[str]:
        """The image module's addendum to `/season placements-review` (FR-033), beneath core's
        own heading: the drawing's own words (`ImageDrawing.review_section`,
        core/services/image_drawing.py), which the image module builds from the same
        `AspectStatus` list `/images config view` renders, so a divergence between the two
        surfaces is impossible by construction.

        A stated exception (architecture.md, "A catch-all error handler"): a review must never
        fail because of this section, so a fault reading the configuration is stated as such
        and the review goes on.
        """
        try:
            lines = await self.bot.image_drawing.review_section()
        except Exception as exc:  # noqa: BLE001 — a stated exception, see the docstring
            log.error("season review: image section failed: %s", exc, exc_info=True)
            return ["**Image output**", "  ⚠️ Could not be read.", ""]
        return ["**Image output**", *lines, ""]

    async def _approval_windows(self):
        """The lead times the enabled modules configure, read once for a whole season.

        Returns ``(attendance, weather)``, either of them None where that module is off.

        Read once rather than per division: the date faults are judged a division at a time,
        and a league with eight of them would otherwise pay eight times over for two configs
        that cannot have changed in between. They are read through the bot's hook
        (`LeagueBot.round_windows_read`, signed up to by the modules in the builder), so that the
        change queue's check of the approval reads the same windows without a cog (#439).
        """
        return await read_round_windows(self.bot.round_windows_read)

    def _calendar_fault_lines(self, fault) -> list[str]:
        """One division's date faults, as the error lines posted with its calendar.

        At most two, and they are two different things to fix: a round already run is a
        calendar to move wholesale, while a round inside a window is one date to push out or
        one window to shorten. Both name the **latest** round of their kind, which is the one
        that bounds the remedy — clear that round and every earlier one is cleared with it.

        Takes the verdict rather than the rounds, because `/season placements-review` and the approval
        reach it by different routes and must not be able to reach different answers.
        """
        if fault is None:
            return []

        lines: list[str] = []
        if fault.latest_past is not None:
            rnd = fault.latest_past
            lines.append(
                f"⛔ **Round {rnd.round_number} has already run** "
                f"({discord_ts(rnd.scheduled_at)}), and so has every round before it. A "
                f"round in the past never opens its result submission, so it could never "
                f"take results at all. Move the calendar forward with `/round amend`."
            )
        if fault.latest_window is not None:
            window = fault.latest_window
            lines.append(
                f"⛔ **Round {window.round_number} is already inside its "
                f"{window.label.lower()}**, which fell due {discord_ts(window.fire_at)} "
                f"({window.lead}). Move the round out with `/round amend`, or shorten the "
                f"window."
            )
        return lines

    @season.command(
        name="placements-review",
        description="Review the season's divisions, calendar and lineups before confirming placements.",
    )
    # A league manager's, deliberately (2026-09-07). Reading what a season is configured to
    # be is not an administrative act: the report is what the review is for. Approving it is
    # the narrower right, and `ApproveView` is where that is enforced.
    @league_manager_only
    async def season_review(self, interaction: discord.Interaction) -> None:
        confirmed = await self.bot.season_service.get_confirmed_season()
        if confirmed is not None:
            # Mid-season, the review covers the drivers of the window just closed (#220).
            if confirmed.stage is SeasonStage.ONGOING_PLACEMENTS:
                await self._review_mid_season_placements(interaction, confirmed)
            else:
                await refuse(
                    interaction,
                    "\u26d4 Placements can only be reviewed while the season is in placements, "
                    "or mid-season while the drivers of a closed signup window are placed.",
                    what=describe(interaction),
                )
            return

        season = await self.bot.season_service.get_setup_season()
        if season is None:
            await refuse(
                interaction,
                "\u274c No pending season setup. Run `/season setup` first.",
                what=describe(interaction),
            )
            return

        # Placements are reviewed only once the season is in Placements (issue #220).
        if (
            await self.bot.season_service.get_stage(season.id)
        ) is not SeasonStage.PLACEMENTS:
            await refuse(
                interaction,
                "\u26d4 Placements can only be reviewed while the season is in placements. "
                "A season in configuration is reviewed with `/season config-review`.",
                what=describe(interaction),
            )
            return

        await interaction.response.defer(ephemeral=True)

        # The public review is not posted here: its sections are collected, in the order a
        # manager reads them, and asked of the change queue with the pictures that stand in for
        # some of them (`season.review.post`, decision 5). Only the private faults are replies.
        poster = _ReviewPoster(interaction)
        report: list[str] = []
        pictures: list[dict[str, Any]] = []

        season_num = f" (Season #{season.season_number} — F1 {season.game_edition})" if season.season_number > 0 else (f" (F1 {season.game_edition})" if season.game_edition > 0 else "")
        # The review is sent as one message per subsection rather than as one block.
        # It outgrew Discord's 2000-character limit once every not-OK image output began
        # stating its cause and its remedy, and an over-long send loses the whole review
        # rather than its tail. Splitting by subject also means a manager can scroll back
        # to the part they are working on instead of hunting through one wall of text.
        header_lines = [
            f"**Season Review{season_num}**",
            "",
        ]
        image_lines: list[str] = []

        # Why the approve button must be withheld, if it must. A list rather than a flag
        # because there is more than one cause and they read differently: a graphic that
        # could not be drawn is a template or an asset to fix, while an invalid portrait
        # configuration is a setting to change. A single flag reported the second as the
        # first, and sent a manager looking for artwork that was not the problem.
        #
        # Raised by every fault of the image configuration, and by any division whose lineup
        # or calendar graphic was wanted and could not be drawn. The section's text still
        # stands in, so the review is a complete picture of the season, but the approve
        # button is withheld: a season approved now would post that fault to the league's
        # own channels, and the manager reading this is the one person able to fix it.
        approval_blockers: list[str] = []

        # The divisions the review shows, read before the modules are asked for their sections,
        # which hand lines for each of them.
        db_divisions = await self.bot.season_service.get_divisions_with_results_config(season.id)
        sections = await read_review_sections(
            self.bot.review_sections_read,
            Review.PLACEMENTS,
            season_id=season.id,
            division_ids=[division.id for division in db_divisions if division.name],
        )

        # ── Modules ───────────────────────────────────────────────
        weather_on = await self.bot.module_service.is_weather_enabled()
        signup_on = await self.bot.module_service.is_signup_enabled()
        results_on = await self.bot.module_service.is_results_enabled()
        attendance_on = await self.bot.module_service.is_attendance_enabled()
        on = "✅ Enabled"
        off = "❌ Disabled"
        if signup_on:
            signup_note = next(
                (section.modules_note for section in sections if section.module == "signup"),
                None,
            )
            signup_line = f"  Signup: {on} | {signup_note}" if signup_note else f"  Signup: {on}"
        else:
            signup_line = f"  Signup: {off}"
        images_on = await self.bot.module_service.is_images_enabled()
        header_lines += [
            "**Modules**",
            f"  Weather: {on if weather_on else off}",
            signup_line,
            f"  Results: {on if results_on else off}",
            f"  Attendance: {on if attendance_on else off}",
            f"  Images: {on if images_on else off}",
            "",
        ]

        # ── Image module (FR-033, FR-034) ─────────────────────────
        # When disabled, report that and omit the detail. When enabled, render the
        # aspect section from the same AspectStatus list `/images config view`
        # uses, so the two surfaces cannot drift.
        if images_on:
            image_lines += await self._build_image_review_section()

            # Every fault of the image configuration the section above names as blocking
            # — the rasteriser, a template a switched-on output draws, a per-tier colour,
            # the portrait settings — withholds the button, read through the helper the
            # confirmation refuses on (#396). The section draws only the lineup and the
            # calendar, so a broken results or weather template, or a missing rasteriser
            # with both of those off, was named here as blocking and then approved.
            approval_blockers += await approval_checks.image_configuration_faults(
                self.bot.image_drawing
            )
            image_lines += await self._calendar_capacity_warning(
                season.id
            )
            image_lines += await self._attendance_capacity_warning(
                season.id
            )
            lineup_problems = await approval_checks.lineup_problems(
                season.id,
                season_service=self.bot.season_service,
                team_service=self.bot.team_service,
                image_drawing=self.bot.image_drawing,
            )
            if lineup_problems:
                image_lines.append(
                    "❌ **Lineup template** — these block approval:"
                )
                image_lines += [f"  • {problem}" for problem in lineup_problems]
                image_lines.append("")
                # And they do (#396): the confirmation refuses on the same helper.
                approval_blockers += [
                    f"Lineup template: {problem}" for problem in lineup_problems
                ]

        # ── Team names (038, FR-013) ──────────────────────────────
        # Outside the `images_on` branch deliberately: a team name must address a
        # lineup field whether or not the league draws one today (FR-012).
        name_problems = await approval_checks.team_name_problems(self.bot.team_service, season.id)
        if name_problems:
            header_lines.append(
                "❌ **Team names** — these cannot become lineup template fields, "
                "and block approval:"
            )
            header_lines += [f"  • {problem}" for problem in name_problems]
            header_lines.append("")

        # ── What the modules refuse the confirmation on ──────────
        # Read through the hook the approval and the configuration's faults read it by, so the
        # report and the refusal cannot drift. Each is a private reply, sent below.
        module_refusals = await read_refusals(
            self.bot.confirmation_refused, season.id, Confirmation.REVIEW
        )

        # Pre-fetch role configs so we can warn about teams missing a role
        teams_with_roles = await self.bot.team_service.get_teams_with_roles(

        )
        roleless = {t["name"] for t in teams_with_roles if not t["role_id"] and not t["is_reserve"]}
        reserve_has_role = any(t["is_reserve"] and t["role_id"] for t in teams_with_roles)
        if not reserve_has_role:
            header_lines.append(
                "⚠️ **Reserve team has no role assigned** — use `/team reserve-role` "
                "before approving. Drivers on the reserve team will fail result validation."
            )
            header_lines.append("")

        # ── Send one message per subsection ─────────────────────
        #
        # In the order a manager reads them: the season and what is switched on,
        # then each module's own configuration, then the image outputs. A subsection
        # holding nothing is not sent at all — a module that is off has no
        # configuration to review, and an empty heading is only noise.
        #
        # Still chunked underneath. The image subsection alone can carry eight
        # outputs, each with its cause and its remedy, and pass the 2000-character
        # limit on its own; an over-long send loses the message, not merely its tail.
        for block in (
            header_lines,
            *[section.lines for section in sections],
            image_lines,
        ):
            body = "\n".join(block).strip()
            if not body:
                continue
            report.extend(chunk_message(body))

        # ── Per-division blocks (4 messages each) ────────────────
        # The rounds are read once here, and the review's calendars are made from them.
        rounds_by_division = {}
        for _div in db_divisions:
            if _div.name:
                rounds_by_division[_div.id] = (
                    await self.bot.season_service.get_division_rounds(_div.id)
                )
        # The pictures the league will see in place of a section's text are asked of the
        # queue, which draws and posts them ahead of the report; the section that stands
        # in for each is carried with it (posted only where Discord refuses the file).
        draws_calendar, draws_lineup = await self._review_picture_gates()

        # The lead times both date faults are judged against, read once for the season
        # and reused by every division's calendar below.
        _att_windows, _wx_windows = await self._approval_windows()
        # Raised by any division whose calendar holds a round already run, or one already
        # inside a configured window. Collected rather than acted on in the loop, because
        # the Approve button is decided once for the whole season after it.
        calendar_faults_found: list[str] = []

        for div in db_divisions:
            if not div.name:
                continue
            tier_tag = f" (Tier {div.tier})" if div.tier > 0 else ""

            # ── Message 0: division banner ─────────────────────────
            sep = "\u2500" * 35
            report.append(f"{sep}\n# {div.name.upper()}{tier_tag}\n{sep}")

            # ── Message 1: config (role + channels) ──────────────
            cal_chan = f"<#{div.calendar_channel_id}>" if div.calendar_channel_id else "*(not set)*"
            lineup_chan = f"<#{div.lineup_channel_id}>" if div.lineup_channel_id else "*(not set)*"
            results_chan = f"<#{div.results_channel_id}>" if div.results_channel_id else "*(none)*"
            standings_chan = f"<#{div.standings_channel_id}>" if div.standings_channel_id else "*(none)*"
            verdicts_chan = f"<#{div.penalty_channel_id}>" if div.penalty_channel_id else "*(not configured)*"
            weather_chan = f"<#{div.forecast_channel_id}>" if div.forecast_channel_id else "*(none)*"
            cfg_lines: list[str] = [
                "\U0001f4c2 **Configuration**",
                f"  Role: <@&{div.mention_role_id}>",
                f"  Calendar channel: {cal_chan}",
                f"  Lineup channel: {lineup_chan}",
                f"  Results channel: {results_chan}",
                f"  Standings channel: {standings_chan}",
                f"  Verdicts channel: {verdicts_chan}",
            ]
            for section in sections:
                cfg_lines += section.division_lines.get(div.id, [])
            cfg_lines.append(f"  Weather channel: {weather_chan}")
            report.append("\n".join(cfg_lines))

            # ── Message 2: calendar — the graphic, or the text ────
            #
            # Whichever form the league actually conveys. The review is what a
            # manager judges the season by, so showing them text a league will
            # never see would be showing them the wrong thing.
            rounds_db = rounds_by_division.get(div.id, [])
            # The division's date faults, which the graphic cannot show \u2014 a calendar
            # drawn from the same rounds says nothing about which of them have gone
            # by. Posted either way, on the roleless-team warning's reasoning below:
            # it is a review *finding*, and the form the calendar took does not
            # change whether the manager needs it.
            from leaguebot.core.services.approval_window_service import calendar_faults

            fault_lines = self._calendar_fault_lines(
                calendar_faults(
                    rounds_db,
                    now=datetime.now(timezone.utc),
                    attendance=_att_windows,
                    weather=_wx_windows,
                )
            )
            calendar_faults_found.extend(fault_lines)

            cal_lines: list[str] = ["\U0001f4c5 **Calendar**"]
            for r in rounds_db:
                cal_lines.append(
                    f"  Round {r.round_number}: {r.format.value} "
                    f"@ {r.track_name or 'Mystery'} \u2014 {discord_ts(r.scheduled_at)}"
                )
            if draws_calendar:
                pictures.append({
                    "name": f"{div.name} calendar", "division_id": div.id,
                    "kind": "calendar", "text": "\n".join(cal_lines),
                    "season_number": season.season_number or None,
                })
                if fault_lines:
                    report.append("\n".join(fault_lines))
            else:
                cal_lines.extend(fault_lines)
                report.append("\n".join(cal_lines))

            # ── Message 3: lineup — the graphic, or the text ──────
            #
            # As with the calendar above: whichever form the league conveys. The
            # roleless-team warning is a review *finding* the graphic cannot show,
            # so it is posted either way.
            teams = await self.bot.team_service.get_division_teams(div.id)
            missing_roles = (
                [t["full_name"] for t in teams if t["name"] in roleless] if teams else []
            )
            role_warning = (
                "  \u26a0\ufe0f **No role assigned:** "
                + ", ".join(f'"{n}"' for n in missing_roles)
                + " \u2014 result submission will reject drivers in these teams."
                if missing_roles
                else None
            )

            lineup_lines: list[str] = ["\U0001f3ce\ufe0f **Lineup**"]
            if teams:
                lineup_lines.append(
                    "  **Teams:** " + ", ".join(t["full_name"] for t in teams)
                )
                if role_warning is not None:
                    lineup_lines.append(role_warning)
            async with get_connection(self.bot.db_path) as _db:
                _cur = await _db.execute(
                    """
                    SELECT dp.discord_user_id, dp.is_test_driver, dp.test_display_name,
                           ti.full_name AS team_name, ti.is_reserve
                    FROM driver_season_assignments dsa
                    JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
                    JOIN team_seats ts ON ts.id = dsa.team_seat_id
                    JOIN team_instances ti ON ti.id = ts.team_instance_id
                    WHERE dsa.division_id = ? AND dp.current_state = 'ASSIGNED'
                    ORDER BY ti.full_name, dp.discord_user_id
                    """,
                    (div.id,),
                )
                assignment_rows = await _cur.fetchall()
            if assignment_rows:
                regular_teams: dict[str, list[str]] = {}
                reserve_teams: dict[str, list[str]] = {}
                for _row in assignment_rows:
                    if _row["is_test_driver"] and _row["test_display_name"]:
                        label = f"<@{_row['discord_user_id']}> ({_row['test_display_name']})"
                    else:
                        label = f"<@{_row['discord_user_id']}>"
                    target = reserve_teams if _row["is_reserve"] else regular_teams
                    target.setdefault(_row["team_name"], []).append(label)
                for t_name, mentions in regular_teams.items():
                    lineup_lines.append(f"  **{t_name}**: {', '.join(mentions)}")
                if reserve_teams:
                    lineup_lines.append("  ---")
                    for t_name, mentions in reserve_teams.items():
                        lineup_lines.append(f"  **{t_name}**: {', '.join(mentions)}")
            else:
                lineup_lines.append("  *(no drivers assigned)*")
            if draws_lineup:
                if role_warning is not None:
                    report.append(role_warning.strip())
                pictures.append({
                    "name": f"{div.name} lineup", "division_id": div.id,
                    "kind": "lineup", "text": "\n".join(lineup_lines),
                })
            else:
                report.append("\n".join(lineup_lines))

        # ── Server-level unsettled signups (issue #220) ──────────
        # Named in the public report, every one of them, so that whoever reads the review
        # sees who is still to be placed or turned down — not merely how many.
        unsettled, channel_faults = await approval_checks.placement_confirmation_faults(
            self.bot.module_service, self.bot.db_path, season.id, interaction.guild
        )
        if unsettled:
            report.extend(chunk_message(
                "\u26a0\ufe0f **Unsettled signups** — each is to be placed with "
                "`/driver assign`, turned down with `/driver reject`, or reviewed:\n"
                + "\n".join(f"\u2022 {line}" for line in unsettled)
            ))
        # The season-wide report of rounds already inside a configured window is
        # withdrawn (#181, decided 2026-09-14). It named every round against every
        # window it had missed, capped at eight rounds — sixty lines where two would do,
        # and past Discord's 2000-character limit for a season built wholly in the past,
        # which lost the whole message rather than its tail. Each division's calendar
        # now carries its own two findings, above, where the manager is already looking
        # at the dates they would have to change.
        if calendar_faults_found:
            # Said once more at the end, briefly, because the per-division notes are
            # scattered through a review running to a dozen messages and the reason the
            # button is missing has to be findable from the bottom of it.
            await poster.reply(
                "⛔ **This season's calendar holds dates that have already gone by.**"
                "\nEach division's calendar above names the round. The season is "
                "**not** offered for approval while that stands — put the dates right "
                "with `/round amend`, then run `/season placements-review` again.",
                ephemeral=True,
            )
        if approval_blockers:
            # De-duplicated: five divisions failing to draw is one thing to fix, and
            # saying it five times buries anything else in the list. Chunked, because
            # sixteen templates each with its reason can pass Discord's limit.
            reasons = list(dict.fromkeys(approval_blockers))
            body = "\n".join(f"• {reason}" for reason in reasons)
            for chunk in chunk_message(
                "\u26d4 **The image module is not correctly configured.**\n"
                f"{body}\n"
                "The season is **not** offered for approval while that stands. "
                "Put it right, then run `/season placements-review` again."
            ):
                await poster.reply(chunk, ephemeral=True)
        for refusal in module_refusals:
            await poster.reply(refusal.message, ephemeral=True)
        if name_problems:
            # Named in full at the head of the review. No command renames a team once
            # the configuration is confirmed, which fixes the team list (see
            # `TeamCog._team_list_lock`), so abandoning the season is the one remedy there
            # is — and the configuration review refuses these names, so a season reaching
            # here is rare.
            await poster.reply(
                "\u26d4 **Some team names cannot be used as artwork filenames.** Each is "
                "named at the head of the review. The season is **not** offered for "
                "approval while that stands. The team list is fixed once the "
                "configuration is confirmed, so the season must be aborted with "
                "`/season abort` and configured again.",
                ephemeral=True,
            )
        if unsettled:
            await poster.reply(
                "\u26d4 **Every signup must be settled before placements are confirmed.** "
                f"{len(unsettled)} signup(s) are not, each named in the review above. "
                "Place each driver with `/driver assign`, turn them down with "
                "`/driver reject`, or finish reviewing their signup — then run "
                "`/season placements-review` again.",
                ephemeral=True,
            )
        if channel_faults:
            await self._send_channel_faults(poster, channel_faults)
        no_divisions = not await approval_checks.season_has_divisions(
            season.id, season_service=self.bot.season_service
        )
        if no_divisions:
            await poster.reply(
                approval_checks.NO_DIVISIONS_REFUSAL + " Then run `/season placements-review` again.",
                ephemeral=True,
            )
        offered = (
            not approval_blockers
            and not calendar_faults_found
            and not module_refusals
            and not name_problems
            and not unsettled
            and not channel_faults
            and not no_divisions
        )
        await self._ask_review(
            interaction, "/season placements-review", season.id, report, pictures,
            offered=offered, view="approve",
        )

    @staticmethod
    async def _send_channel_faults(poster: _ReviewPoster, channel_faults: list[str]) -> None:
        """Tell the reviewer, privately, which division channels withhold the button.

        One message for both reviews, each fault naming the command that puts it right.
        """
        body = "\n".join(f"• {line}" for line in channel_faults)
        for chunk in chunk_message(
            "⛔ **Every division needs every channel it posts to.**\n"
            f"{body}\n"
            "Set each with the command named, then run `/season placements-review` again."
        ):
            await poster.reply(chunk, ephemeral=True)

    # ------------------------------------------------------------------
    # /season placements-review mid-season — Ongoing, placements (issue #220)
    # ------------------------------------------------------------------

    async def _review_mid_season_placements(self, interaction: discord.Interaction, season) -> None:
        """Report the lineups as they will stand, and offer to confirm the new placements.

        Mid-season placements only affect lineups — no division or round can be added — so
        the review reports the drivers of the window just closed, each division's lineup with
        them in it, and every signup still unsettled.

        **It withholds its button on what would stop the confirmation** (#374), as the first
        review does: an unsettled signup, a channel a division posts to that is not set or no
        longer on the server, a fault of the image configuration, and a lineup confirming
        would post that will not draw. The lineup of each division holding a new driver is
        drawn with them in it and shown in place of its text, so what is judged here is what
        the league will receive; the confirmation does not draw it again, the fingerprint
        proving nothing it is drawn from has changed since.
        """
        await interaction.response.defer(ephemeral=True)
        poster = _ReviewPoster(interaction)
        report: list[str] = []
        pictures: list[dict[str, Any]] = []
        placements = await self.bot.placement_service.uncommitted_placements(season.id)
        lines = [
            f"**Placements Review (Season #{season.season_number}) — mid-season**",
            "",
        ]
        if placements:
            lines.append("**Drivers to confirm**")
            for p in placements:
                who = p["test_display_name"] or f"<@{p['discord_user_id']}>"
                lines.append(
                    f"  {who} → **{p['team_full_name'] or p['team_name']}** "
                    f"in **{p['division_name']}**"
                )
        else:
            lines.append("*No new placement to confirm.*")
        lines.append("")
        report.extend(chunk_message("\n".join(lines)))

        divisions = [
            division
            for division in await self.bot.season_service.get_divisions(season.id)
            if division.status != "CANCELLED"
        ]
        # The divisions whose lineup confirming posts: those holding a new driver. Where the
        # league conveys its lineup as a picture, theirs is asked of the queue, drawn with the
        # new drivers in it, the section's text carried to stand in for it.
        posted = {p["division_id"] for p in placements}
        _, draws_lineup = await self._review_picture_gates()
        for division in divisions:
            teams = await self.bot.team_service.get_division_teams(division.id)
            block = [f"\U0001f4c2 **{division.name}** — lineup once confirmed"]
            for team in teams:
                seated = [
                    f"<@{seat['discord_user_id']}>"
                    for seat in team["seats"]
                    if seat["discord_user_id"] is not None
                ]
                block.append(f"  **{team['name']}**: {', '.join(seated) or '*(empty)*'}")
            if draws_lineup and division.id in posted:
                pictures.append({
                    "name": f"{division.name} lineup", "division_id": division.id,
                    "kind": "lineup", "text": "\n".join(block), "include_uncommitted": True,
                })
            else:
                report.extend(chunk_message("\n".join(block)))

        unsettled, channel_faults = await approval_checks.placement_confirmation_faults(
            self.bot.module_service, self.bot.db_path, season.id, interaction.guild
        )
        configuration_faults = await approval_checks.mid_season_configuration_faults(
            self.bot.module_service, self.bot.image_drawing
        )
        if unsettled:
            report.extend(chunk_message(
                "⚠️ **Unsettled signups** — each is to be placed with "
                "`/driver assign`, turned down with `/driver reject`, or reviewed:\n"
                + "\n".join(f"• {line}" for line in unsettled)
            ))
            await poster.reply(
                "⛔ **Every signup must be settled before placements are confirmed.** "
                f"{len(unsettled)} signup(s) are not, each named in the review above. "
                "Settle them, then run `/season placements-review` again.",
                ephemeral=True,
            )
        if channel_faults:
            await self._send_channel_faults(poster, channel_faults)
        if configuration_faults:
            body = "\n".join(f"• {fault}" for fault in configuration_faults)
            for chunk in chunk_message(
                "⛔ **The image module is not correctly configured.**\n"
                f"{body}\n"
                "The placements are **not** offered for confirmation while that stands. "
                "Put it right, then run `/season placements-review` again."
            ):
                await poster.reply(chunk, ephemeral=True)
        await self._ask_review(
            interaction, "/season placements-review", season.id, report, pictures,
            offered=not (unsettled or channel_faults or configuration_faults),
            view="mid_season",
            question=(
                "\U0001f4cb **Do you confirm these placements?**\n"
                f"Reviewed by <@{interaction.user.id}>. Confirmation is open to them or to a "
                f"league admin for the next {REVIEW_MINUTES} minutes. Confirming "
                "grants the new drivers their roles and posts each affected lineup."
            ),
        )

    # ------------------------------------------------------------------
    # /season config-review — confirming the configuration (issue #220)
    # ------------------------------------------------------------------

    async def _league_roles_review_lines(self) -> list[str]:
        """The league's base role and driver role, as the configuration review reports them.

        Shown whatever modules are enabled, the roles being the league's rather than the
        signup module's (issue #276). Whether their absence is a fault is the signup
        module's to say; see `approval_checks.configuration_faults`.

        **The configuration review alone.** Confirming the configuration fixes both roles
        until the season ends, so the placements review has nothing to say of them that the
        confirmed configuration did not.
        """
        server_cfg = await self.bot.config_service.get_server_config()

        def _role(role_id: int | None) -> str:
            return f"<@&{role_id}>" if role_id else "*(not configured)*"

        return [
            "**Roles**",
            f"  Base role: {_role(server_cfg.base_role_id if server_cfg else None)}",
            f"  Driver role: {_role(server_cfg.driver_role_id if server_cfg else None)}",
            "",
        ]

    @season.command(
        name="config-review",
        description="Review the season's configuration and confirm it.",
    )
    @league_manager_only
    async def season_config_review(self, interaction: discord.Interaction) -> None:
        """Report the configuration of the season in Configuration, and offer to confirm it.

        The report is public and ends with a confirm button, governed exactly as the
        placements review's approve button is: the reviewer or a league admin may press it,
        it stands for five minutes, and it refuses where the season changed since the report.
        """
        season = await self.bot.season_service.get_setup_season()
        stage = (
            await self.bot.season_service.get_stage(season.id) if season is not None else None
        )
        if season is None or stage is not SeasonStage.CONFIGURATION:
            await refuse(
                interaction,
                "⛔ There is no season in configuration. `/season setup` begins one; a "
                "season whose configuration is confirmed is reviewed with `/season placements-review`.",
                what=describe(interaction),
            )
            return

        await interaction.response.defer(ephemeral=True)
        module = self.bot.module_service
        on, off = "✅ Enabled", "❌ Disabled"
        server_config = await self.bot.config_service.get_server_config()
        test_mode = bool(server_config is not None and server_config.test_mode_active)
        lines = [
            f"**Configuration Review (Season #{season.season_number} — F1 {season.game_edition})**",
            "",
            f"  Test mode: {'✅ On' if test_mode else '❌ Off'}",
            "",
            "**Modules**",
            f"  Signup: {on if await module.is_signup_enabled() else off}",
            f"  Results: {on if await module.is_results_enabled() else off}",
            f"  Attendance: {on if await module.is_attendance_enabled() else off}",
            f"  Weather: {on if await module.is_weather_enabled() else off}",
            f"  Images: {on if await module.is_images_enabled() else off}",
            "",
        ]
        lines += await self._league_roles_review_lines()
        teams = await self.bot.team_service.get_teams_with_roles()
        lines.append("**Teams**")
        for team in teams:
            role = f"<@&{team['role_id']}>" if team["role_id"] else "no role"
            lines.append(f"  {team['name']} → {role}")
        lines.append("")
        if not any(team["is_reserve"] and team["role_id"] for team in teams):
            lines += [
                "⚠️ **Reserve team has no role assigned** — use `/team reserve-role`. "
                "Drivers on the reserve team will fail result validation.",
                "",
            ]

        # Every module's configuration, subsection by subsection, in the words and the
        # order of the placements review — save the divisions, which do not exist yet.
        sections = await read_review_sections(
            self.bot.review_sections_read,
            Review.CONFIGURATION,
            season_id=season.id,
            division_ids=[],
        )
        section_lines: list[list[str]] = [lines, *[section.lines for section in sections]]
        if await module.is_images_enabled():
            section_lines.append(await self._build_image_review_section())
        report: list[str] = []
        for block in section_lines:
            body = "\n".join(block).strip()
            if not body:
                continue
            report.extend(chunk_message(body))

        faults = await approval_checks.configuration_faults(
            season.id,
            interaction.guild,
            module_service=self.bot.module_service,
            signup_module_service=self.bot.signup_module_service,
            config_service=self.bot.config_service,
            team_service=self.bot.team_service,
            confirmation_refused=self.bot.confirmation_refused,
            image_drawing=self.bot.image_drawing,
        )
        if faults:
            body = "\n".join(f"• {fault}" for fault in faults)
            report.extend(chunk_message(
                "⛔ **The configuration cannot be confirmed yet.**\n"
                f"{body}\n"
                "Put these right, then run `/season config-review` again."
            ))
        await self._ask_review(
            interaction, "/season config-review", season.id, report, [],
            offered=not faults, view="configuration",
            question=(
                "📋 **Do you confirm this season's configuration?**\n"
                f"Reviewed by <@{interaction.user.id}>. Confirmation is open to them or to a "
                f"league admin for the next {REVIEW_MINUTES} minutes. Once "
                "confirmed, the team list, the game edition, test mode and the signup module "
                "are fixed for this season."
            ),
        )

    # No command confirms placements (decided 2026-09-07). They are confirmed from the button
    # `/season placements-review` posts and from nowhere else: the review is the evidence the
    # confirmation rests on, and a command that could be run without one let a manager commit
    # a season they had not looked at. The button carries its own five-minute window, so
    # what is confirmed is always a season someone has just read.

    @season.command(
        name="status",
        description="View a summary of the season being raced.",
    )
    @league_manager_only
    @changes_nothing
    async def season_status(self, interaction: discord.Interaction) -> None:
        season = await self.bot.season_service.get_confirmed_season()
        if season is None:
            await interaction.response.send_message(
                "\u2139\ufe0f No season is being raced on this server.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        lines = [
            f"**Active Season** (ID: {season.id})",
            f"Start date: {season.start_date}",
            f"Divisions: {len(divisions)}",
            "",
        ]
        for div in divisions:
            rounds = await self.bot.season_service.get_division_rounds(div.id)
            active_rounds = [r for r in rounds if r.status != RoundStatus.CANCELLED.value]
            next_round = next(
                (
                    r for r in active_rounds
                    if r.format != RoundFormat.MYSTERY
                    and not (r.phase1_done and r.phase2_done and r.phase3_done)
                ),
                None,
            )
            div_tag = " ~~[CANCELLED]~~" if div.status == "CANCELLED" else ""
            lines.append(
                f"\U0001f4c2 **{div.name}**{div_tag} \u2014 "
                "Next round: "
                + (
                    f"R{next_round.round_number} @ {next_round.track_name or 'Mystery'} "
                    f"{discord_ts(next_round.scheduled_at)}"
                    if next_round
                    else "None remaining"
                )
            )

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @season.command(
        name="cancel",
        description="Cancel the season being raced (league admin only, irreversible).",
    )
    @app_commands.describe(confirm='Type "CONFIRM" to proceed with season cancellation.')
    @league_admin_only
    async def season_cancel(
        self,
        interaction: discord.Interaction,
        confirm: str,
    ) -> None:
        if confirm != "CONFIRM":
            await refuse(
                interaction,
                "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                what=describe(interaction),
            )
            return

        season = await self.bot.season_service.get_confirmed_season()
        if season is None:
            await refuse(
                interaction,
                "\u274c No season is being raced, so there is none to cancel. A season whose placements are yet to be "
                "confirmed is abandoned with `/season abort`.",
                what=describe(interaction),
            )
            return

        # Every other gate (a season pending completion, a round being amended, a submission
        # holding accepted results) is the change type's check, made now and again as the
        # cancellation comes up to run, in the words this command used. The queue's
        # acknowledgement is this command's response, so nothing is deferred.
        await self.bot.change_queue.ask(
            SEASON_CANCEL,
            {"season_id": season.id, "season_number": season.season_number},
            interaction=interaction,
            what=describe(interaction),
        )

    @season.command(
        name="abort",
        description="Abandon a season whose placements were never confirmed, leaving nothing of it.",
    )
    @app_commands.describe(confirm='Type "CONFIRM" to abort the season.')
    @league_admin_only
    async def season_abort(self, interaction: discord.Interaction, confirm: str) -> None:
        """Abort the season being set up, before its placements are first confirmed (issue #220).

        The season is deleted with every record of it, its signups included, and takes no
        number. Its drivers go through the driver pass — no history is written, the season
        having none — its signup window is closed and test mode is switched off. The way out
        of a test-mode rehearsal, a wrong signup setting, or a season postponed for want of
        signups. The word and the season are checked here; the rest is the change queue's
        (`season_end_changes.season_abort_change`), which acknowledges at once and updates the
        reply, so nothing is deferred.
        """
        if confirm != "CONFIRM":
            await refuse(
                interaction,
                "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                what=describe(interaction),
            )
            return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(interaction, ABORT_ONLY_BEFORE, what=describe(interaction))
            return

        # Another end of the season in hand is named before the stage is judged, whatever stage
        # the season is in (owner, 2026-10-09, answer B): a cancellation stopped over an ongoing
        # season is told so, not that an ongoing season is cancelled.
        hand = await season_end_in_hand(self.bot.db_path, season.id)
        if hand is not None:
            await refuse(
                interaction,
                season_end_refusal(SEASON_ABORT, hand, season.season_number),
                what=describe(interaction),
            )
            return
        if season.stage not in PRE_CONFIRMATION:
            await refuse(interaction, ABORT_ONLY_BEFORE, what=describe(interaction))
            return

        await self.bot.change_queue.ask(
            SEASON_ABORT,
            {"season_id": season.id},
            interaction=interaction,
            what=describe(interaction),
        )

    @season.command(
        name="complete",
        description="Mark the current season as complete (every round must be finalised).",
    )
    @league_admin_only
    async def season_complete(
        self,
        interaction: discord.Interaction,
    ) -> None:
        season = await self.bot.season_service.get_confirmed_season()
        if season is None:
            await refuse(
                interaction,
                "\u274c No season is being raced, so there is none to complete.",
                what=describe(interaction),
            )
            return

        # Every other gate (a round being amended, rounds outstanding, a division unfinished) is
        # the change type's check, made now and again as the completion comes up to run. The
        # queue's acknowledgement is this command's response, so nothing is deferred.
        await self.bot.change_queue.ask(
            SEASON_COMPLETE,
            {"season_id": season.id, "season_number": season.season_number},
            interaction=interaction,
            what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /division group
    # ------------------------------------------------------------------

    division = app_commands.Group(
        name="division",
        description="Division management commands",
        guild_only=True,
        default_permissions=None,
    )

    @division.command(
        name="add",
        description="Add a division to the pending season setup.",
    )
    @app_commands.describe(
        name="Division name",
        role="The Discord role to mention for this division",
        tier="Tier number for this division (1 = top tier, must be sequential and unique)",
    )
    @league_manager_only
    async def division_add(
        self,
        interaction: discord.Interaction,
        name: str,
        role: discord.Role,
        tier: int,
    ) -> None:
        """Add a division: ask the change queue for it, which refuses what the command has always
        refused at the ask and again as the change starts, and adds the division with its teams
        in one save (`core/services/division_add_change.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_ADD, {"name": name, "role_id": role.id, "tier": tier},
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="duplicate",
        description="Copy a division's rounds with a datetime offset while the season is in placements.",
    )
    @app_commands.describe(
        source_name="Name of the division to copy from",
        new_name="Name for the new division",
        role="The Discord role to mention for the new division",
        tier="Tier number for the new division (must be unique within this season)",
        day_offset="Days to shift all round datetimes (can be negative)",
        hour_offset="Hours to shift all round datetimes (can be negative, decimals OK)",
    )
    @league_manager_only
    async def division_duplicate(
        self,
        interaction: discord.Interaction,
        source_name: str,
        new_name: str,
        role: discord.Role,
        tier: int,
        day_offset: int = 0,
        hour_offset: float = 0.0,
    ) -> None:
        """Copy a division: ask the change queue for it, which refuses what the command has always
        refused at the ask and again as the change starts, and saves the copy with its rounds, its
        teams and its audit entry in one save (`core/services/division_duplicate_change.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_DUPLICATE,
            {
                "source_name": source_name, "new_name": new_name, "role_id": role.id,
                "tier": tier, "day_offset": day_offset, "hour_offset": hour_offset,
            },
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="delete",
        description="Remove a division and all its rounds from pending setup.",
    )
    @app_commands.describe(name="Name of the division to delete")
    @league_admin_only
    async def division_delete(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        """Delete a division: ask the change queue for it, which refuses what the command has
        always refused at the ask and again as the change starts, and deletes the division, its
        rounds and every module's rows of them with its audit entry in one save
        (`core/services/division_delete_change.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_DELETE, {"name": name},
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="rename",
        description="Rename a division while the season is in placements.",
    )
    @app_commands.describe(
        current_name="Current name of the division",
        new_name="New name for the division",
    )
    @league_manager_only
    async def division_rename(
        self,
        interaction: discord.Interaction,
        current_name: str,
        new_name: str,
    ) -> None:
        """Rename a division: ask the change queue for it, which refuses what the command has
        always refused at the ask and again as the change starts, and renames the division with
        its audit entry in one save (`core/services/division_edit_changes.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_RENAME, {"current_name": current_name, "new_name": new_name},
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="amend",
        description="Amend a division's name, tier, or role while the season is in placements.",
    )
    @app_commands.describe(
        name="Current name of the division",
        new_name="New name for the division (optional)",
        tier="New tier number (optional, must be unique within this season)",
        role="New Discord role (optional)",
    )
    @league_manager_only
    async def division_amend(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: str | None = None,
        tier: int | None = None,
        role: discord.Role | None = None,
    ) -> None:
        """Amend a division: ask the change queue for it, which refuses what the command has
        always refused at the ask and again as the change starts, and changes what was given of
        the division with its audit entry in one save
        (`core/services/division_edit_changes.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_AMEND,
            {
                "name": name, "new_name": new_name, "tier": tier,
                "role_id": role.id if role is not None else None,
                "role_name": role.name if role is not None else None,
            },
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="cancel",
        description="Cancel a division of the season being raced (irreversible).",
    )
    @app_commands.describe(
        name="Name of the division to cancel",
        confirm='Type "CONFIRM" to proceed.',
    )
    @league_admin_only
    async def division_cancel(
        self,
        interaction: discord.Interaction,
        name: str,
        confirm: str,
    ) -> None:
        if confirm != "CONFIRM":
            await refuse(
                interaction,
                "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                what=describe(interaction),
            )
            return

        season = await self.bot.season_service.get_confirmed_season()
        from leaguebot.core.models.season import ONGOING_STAGES

        # Not while the season's own end is in hand: refused at once, naming the job, ahead of
        # the stage gate (slice 5, owner, 2026-10-09, answer B).
        if season is not None:
            hand = await season_end_in_hand(self.bot.db_path, season.id)
            if hand is not None:
                await refuse(
                    interaction,
                    season_end_refusal(None, hand, season.season_number),
                    what=describe(interaction),
                )
                return

        # Available only while the season is ongoing (issue #220).
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "\u274c `/division cancel` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)
        except SeasonImmutableError:
            await refuse(
                interaction,
                "\u274c This season is archived (COMPLETED) and cannot be modified.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division `{name}` not found.",
                what=describe(interaction),
            )
            return

        # Whether the division is already cancelled is the change type's check, made now and again
        # as the cancellation comes up to run. The queue's acknowledgement is this command's
        # response, so nothing is deferred.
        await self.bot.change_queue.ask(
            DIVISION_CANCEL,
            {
                "division_id": div.id,
                "division_name": div.name,
                "season_number": season.season_number,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # Division channel assignment
    # ------------------------------------------------------------------

    async def _ask_division_channel(
        self,
        interaction: discord.Interaction,
        kind: str,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        """Ask the change queue to give the division *name* of the live season *channel* as the
        channel *kind* (`division_settings`) names.

        The season and the division are found here, since the change is for the division by its
        id; the channel's use is the setting's own `check`, made as the queue is asked and again
        as the change starts. The queue's acknowledgement is the command's response, so nothing
        is deferred.
        """
        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(interaction, NO_SEASON_REFUSAL, what=describe(interaction))
            return
        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(interaction, division_not_found(name), what=describe(interaction))
            return
        await self.bot.change_queue.ask(
            kind,
            {
                "division_id": div.id,
                "division_name": name,
                "value": channel.id,
                "channel_name": channel.name,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    @division.command(
        name="lineup-channel",
        description="Set the lineup posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Lineup channel")
    @league_manager_only
    async def division_lineup_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._ask_division_channel(
            interaction, "core.setting.lineup_channel_id", name, channel
        )

    @division.command(
        name="calendar-channel",
        description="Set the calendar posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Calendar channel")
    @league_manager_only
    async def division_calendar_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._ask_division_channel(
            interaction, "core.setting.calendar_channel_id", name, channel
        )

    @division.command(
        name="calendar-sync",
        description="Redraw a division's calendar and replace the posted message.",
    )
    @app_commands.describe(name="Division name")
    @league_manager_only
    async def division_calendar_sync(
        self, interaction: discord.Interaction, name: str
    ) -> None:
        """Post a division's calendar anew, as a change on the change queue.

        Gated on **no** module: it refreshes whichever form the server's configuration
        calls for — the graphic where the images module and the `calendar` aspect are both
        on, the traditional textual calendar otherwise.

        The command keeps its gates and answers them at once in its own words; the posting is
        the queue's (`calendar_post_service.calendar_sync_change`), acknowledged and the
        acknowledgement updated with the outcome, the old calendar deleted by a job after the
        new one stands. A **commanded** posting, so a fatal render rejects the change and posts
        nothing rather than quietly substituting text (Constitution XIV.7): the person at the
        keyboard is the one able to fix the template, and the outcome says that nothing was
        deleted and the previous calendar still stands.

        **The season's number is passed bare, and a falsy one is drawn rather than hidden**
        (issue #213, decided 2026-09-20). `seasons.season_number` is `NOT NULL` with no
        default and numbering starts at one, so a season reaching this command always
        carries a real number; a `0` can only come from a malformed row and is a bug. The
        obvious reflex, `season.season_number or None`, would empty the template's field
        and silently drop the whole "SEASON n" line where the template groups it — the same
        quiet failure this issue was raised for. Drawing "SEASON 0" is visibly wrong and
        gets reported, so the value goes through untouched and the job logs the fault beside
        it. Pinned by `test_a_season_with_no_number_still_draws_and_is_logged`.
        """
        from leaguebot.core.services import calendar_post_service as _calendar
        from leaguebot.core.utils.season_gate import season_for_command

        await interaction.response.defer(ephemeral=True)

        # Refused once every division is done (issue #224): the calendar describes rounds
        # somebody will drive, and a season pending completion has none left. It is no part
        # of the end-of-season output either — completing posts the final classification and
        # the final attendance sheet, and neither is a calendar.
        season = await season_for_command(
            interaction, self.bot.season_service, "division calendar-sync"
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"❌ Division **{name}** not found in the current season.",
                what=describe(interaction),
            )
            return

        if not div.calendar_channel_id:
            await refuse(
                interaction,
                f"❌ **{name}** has no calendar channel configured. "
                f"Set one with `/division calendar-channel` first.",
                what=describe(interaction),
            )
            return

        await self.bot.change_queue.ask(
            _calendar.CALENDAR_SYNC,
            {
                "division_id": div.id,
                "division_name": div.name,
                "season_id": season.id,
                "season_number": season.season_number,
            },
            interaction=interaction,
            what=f"posting the calendar of **{div.name}** again",
            refusal_what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /round group
    # ------------------------------------------------------------------

    round = app_commands.Group(
        name="round",
        description="Round management commands",
        guild_only=True,
        default_permissions=None,
    )

    @round.command(
        name="add",
        description="Add a round to a division. Round number is auto-derived from scheduled date.",
    )
    @app_commands.describe(
        division_name="Name of the division this round belongs to",
        format="Round format (NORMAL, SPRINT, MYSTERY, ENDURANCE)",
        scheduled_at="Race date/time in ISO format (YYYY-MM-DDTHH:MM:SS UTC)",
        track="Track ID or name (e.g. 27 or United Kingdom). Leave blank for Mystery rounds.",
    )
    @league_manager_only
    async def round_add(
        self,
        interaction: discord.Interaction,
        division_name: str,
        format: str,
        scheduled_at: str,
        track: str = "",
    ) -> None:
        """Add a round: ask the change queue for it, which refuses what the command has always
        refused at the ask and again as the change starts, and adds the round, renumbers its
        division and records it in one save (`core/services/round_add_changes.py`).

        Deferred before anything else: the check loads and parses the calendar template, which
        can outlast Discord's three-second window, and the queue's acknowledgement then lands on
        an expired token (404 Unknown interaction).
        """
        await interaction.response.defer(ephemeral=True)
        await self.bot.change_queue.ask(
            ROUND_ADD, add_payload(division_name, format, scheduled_at, track),
            interaction=interaction, what=describe(interaction),
        )

    # ── /round add-bulk and /round add-xml ────────────────────────────────
    #
    # Flat siblings of `add` rather than `add bulk` and `add xml`: `/round add` is itself a
    # command, and Discord lets a name be a command or a group of them, never both. The
    # hyphenated shape follows `/results config bulk-session`.

    @round.command(
        name="add-bulk",
        description="Add many rounds to one division from a pasted list (setup only).",
    )
    @app_commands.describe(division_name="Division these rounds belong to")
    @league_manager_only
    async def round_add_bulk(
        self, interaction: discord.Interaction, division_name: str
    ) -> None:
        """Open the paste-a-calendar modal for one division.

        **Deliberately does not defer**, unlike every other command of this flow:
        `send_modal` must be the interaction's first response, and a deferred interaction
        cannot open one. The work — and the deferral — happens in the modal's `on_submit`.
        """
        await interaction.response.send_modal(BulkRoundModal(division_name))

    @round.command(
        name="add-xml",
        description="Add rounds to one or more divisions from XML (setup only).",
    )
    @league_manager_only
    async def round_add_xml(self, interaction: discord.Interaction) -> None:
        """Open the XML import modal.

        Does not defer, for the reason given on `round_add_bulk`.
        """
        await interaction.response.send_modal(XmlRoundModal())

    @bounded_autocomplete()
    async def _track_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """The circuits, offered to both `/round add` and `/round amend`.

        One callback registered against both parameters \u2014 it was two byte-identical copies,
        which is one more place for the two to drift apart than is useful. Registered
        imperatively below, the way the `/images test` division autocomplete already is.

        The shorter lock wait matters here: an autocomplete has three seconds and cannot be
        deferred, so giving up early and offering nothing beats answering into an expired
        interaction token.
        """
        results: list[app_commands.Choice[str]] = []
        async with get_connection(
            self.bot.db_path, timeout=AUTOCOMPLETE_TIMEOUT_SECONDS
        ) as _tdb:
            _tracks = await track_service.get_all_tracks(_tdb)
        for r in _tracks:
            label = f"{r['id']:02d} \u2013 {r['name']}"
            if current.lower() in label.lower():
                results.append(app_commands.Choice(name=label, value=r["name"]))
        return results[:25]

    async def _record_round_unchanged(
        self,
        interaction: discord.Interaction,
        command: str,
        division_name: str,
        round_number: int,
    ) -> None:
        """Answer and record a `/round amend` given only the values the round already holds.

        It changes nothing, writes no audit entry and offers no confirmation, and records that
        nothing was changed (the core specification's "The record of what changed").
        """
        await interaction.followup.send(
            f"\u2139\ufe0f Round {round_number} in **{division_name}** already holds those values. "
            "Nothing was changed.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | {command} | "
            "Nothing changed\n"
            f"  division: {division_name}\n"
            f"  round: {round_number}\n"
            "  reason: the values given are the ones it holds",
        )

    @round.command(
        name="amend",
        description="Amend a round's track, moment or format. Says what it costs before it does it.",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="The round number to amend",
        track="New track ID or name (leave blank to keep current)",
        scheduled_at="New race datetime in ISO format YYYY-MM-DDTHH:MM:SS (leave blank to keep current)",
        format="New format: NORMAL, SPRINT, MYSTERY, or ENDURANCE (leave blank to keep current)",
    )
    @league_manager_only
    async def round_amend(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        track: str = "",
        scheduled_at: str = "",
        format: str = "",
    ) -> None:
        # Deferred before any work: the reads below can outlast Discord's three-second window,
        # and the reply then lands on an expired token (404 Unknown interaction).
        await interaction.response.defer(ephemeral=True)

        if not any([track, scheduled_at, format]):
            await refuse(
                interaction,
                "\u274c Provide at least one field to amend: `track`, `scheduled_at`, or `format`.",
                what=describe(interaction),
            )
            return

        # A season being set up: the change queue judges and amends its round, at the ask and
        # again as the change starts (`core/services/setup_round_amend_change.py`).
        if await self.bot.season_service.get_setup_season() is not None:
            await self.bot.change_queue.ask(
                ROUND_AMEND_SETUP,
                amend_setup_payload(division_name, round_number, track, scheduled_at, format),
                interaction=interaction, what=describe(interaction),
            )
            return

        # Active-season DB path
        season = await self.bot.season_service.get_confirmed_season()
        if season is None:
            await refuse(
                interaction,
                "\u274c No season is being raced.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division `{division_name}` not found.",
                what=describe(interaction),
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await refuse(
                interaction,
                f"\u274c Round {round_number} not found in division `{division_name}`.",
                what=describe(interaction),
            )
            return

        amendments: list[tuple[str, object]] = []

        if track:
            async with get_connection(self.bot.db_path) as _tdb:
                _resolved = await track_service.resolve_track_name(_tdb, track)
            if _resolved is None:
                await refuse(
                    interaction,
                    f"\u274c Unknown track `{track}`. Use autocomplete to pick a valid track.",
                    what=describe(interaction),
                )
                return
            amendments.append(("track_name", _resolved))

        if scheduled_at:
            new_dt = parse_datetime(scheduled_at)
            if new_dt is None:
                await refuse(
                    interaction,
                    "\u274c Invalid datetime. Use `YYYY-MM-DDTHH:MM:SS`.",
                    what=describe(interaction),
                )
                return
            amendments.append(("scheduled_at", new_dt))

        if format:
            try:
                new_fmt = RoundFormat(format.upper())
            except ValueError:
                await refuse(
                    interaction,
                    f"\u274c Invalid format `{format}`. Use NORMAL, SPRINT, MYSTERY, or ENDURANCE.",
                    what=describe(interaction),
                )
                return
            amendments.append(("format", new_fmt))

        # Judged before anything is offered, and judged again when it is confirmed. An
        # amendment the rules refuse never reaches a confirmation at all.
        _verdict = await _judge_round_amendment(
            self.bot, rnd, amendments,
            now=datetime.now(timezone.utc),
        )
        if not _verdict.allowed:
            await refuse(
                interaction,
                f"\u26d4 **Round {rnd.round_number}** in **{div.name}** cannot be amended:\n"
                + "\n".join(f"\u2022 {reason}" for reason in _verdict.refusals)
                + "\n\n**Nothing has been changed.**",
                what=_round_amend_named(rnd.round_number, div.name),
                reason="\n".join(_verdict.refusals),
            )
            return

        _held = await cancellation_holding_amendment(self.bot.db_path, rnd)
        if _held is not None:
            await refuse(
                interaction, _held, what=_round_amend_named(rnd.round_number, div.name),
            )
            return

        # The values that stand are no amendment: nothing is offered, and nothing changes. Asked
        # after the rules, so a round that cannot be amended at all is refused as such, whatever
        # was given (owner, 2026-09-30).
        standing = {
            "track_name": rnd.track_name,
            "scheduled_at": rnd.scheduled_at,
            "format": rnd.format,
        }
        if amendment_changes_nothing(standing, dict(amendments)):
            await self._record_round_unchanged(
                interaction, "/round amend", div.name, rnd.round_number
            )
            return

        summary_lines = [f"**Amend Round {rnd.round_number}** in division **{div.name}**:"]
        for f_name, f_val in amendments:
            summary_lines.append(f"  \u2022 `{f_name}` \u2192 `{f_val}`")

        # Name the forecasts this actually throws away rather than claiming all of them: a
        # phase that would still have run under the round's new moment is kept.
        _withdrawn = sorted(n for n, phase in _verdict.phases.items() if phase.rearm)
        if _withdrawn:
            _named = ", ".join(f"Phase {n}" for n in _withdrawn)
            summary_lines.append(f"\n\u26a0\ufe0f This will withdraw and redraw {_named} for this round.")
        else:
            summary_lines.append("\n\u26a0\ufe0f The forecasts already posted for this round will stand.")

        for _warning in _verdict.warnings:
            summary_lines.append(f"\u26a0\ufe0f {_warning}")

        view = _ConfirmView(
            cog=self,
            interaction_user_id=interaction.user.id,
            round_id=rnd.id,
            amendments=amendments,
            round_number=rnd.round_number,
        )
        await interaction.followup.send("\n".join(summary_lines), view=view, ephemeral=True)

    # One callback, both parameters. Registered here rather than by decorator because
    # `round_amend` is only defined further up the class body than `_track_autocomplete`.
    round_add.autocomplete("track")(_track_autocomplete)
    round_amend.autocomplete("track")(_track_autocomplete)

    @round.command(
        name="delete",
        description="Remove a round from pending setup and renumber siblings.",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="Round number to delete",
    )
    @league_admin_only
    async def round_delete(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
    ) -> None:
        """Delete a round: ask the change queue for it, which refuses what the command has
        always refused at the ask and again as the change starts, and deletes the round, renumbers
        its division and writes its audit entry in one save
        (`core/services/round_delete_change.py`)."""
        await self.bot.change_queue.ask(
            ROUND_DELETE, {"division_name": division_name, "round_number": round_number},
            interaction=interaction, what=describe(interaction),
        )

    @round.command(
        name="cancel",
        description="Cancel a round of the season being raced (irreversible).",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="The round number to cancel",
        confirm='Type "CONFIRM" to proceed.',
    )
    @league_admin_only
    async def round_cancel(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        confirm: str,
    ) -> None:
        if confirm != "CONFIRM":
            await refuse(
                interaction,
                "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                what=describe(interaction),
            )
            return

        season = await self.bot.season_service.get_confirmed_season()
        from leaguebot.core.models.season import ONGOING_STAGES

        # Not while the season's own end is in hand: refused at once, naming the job, ahead of
        # the stage gate (slice 5, owner, 2026-10-09, answer B).
        if season is not None:
            hand = await season_end_in_hand(self.bot.db_path, season.id)
            if hand is not None:
                await refuse(
                    interaction,
                    season_end_refusal(None, hand, season.season_number),
                    what=describe(interaction),
                )
                return

        # Available only while the season is ongoing (issue #220).
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "\u274c `/round cancel` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)
        except SeasonImmutableError:
            await refuse(
                interaction,
                "\u274c This season is archived (COMPLETED) and cannot be modified.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division `{division_name}` not found.",
                what=describe(interaction),
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await refuse(
                interaction,
                f"\u274c Round {round_number} not found in division `{div.name}`.",
                what=describe(interaction),
            )
            return

        # The rest of the gates (already cancelled, results entered, a submission open) are the
        # change type's check, made now and again as the cancellation comes up to run. The
        # queue's acknowledgement is this command's response, so nothing is deferred.
        await self.bot.change_queue.ask(
            ROUND_CANCEL,
            {
                "round_id": rnd.id,
                "round_number": round_number,
                "track_name": rnd.track_name,
                "division_id": div.id,
                "division_name": div.name,
                "season_number": season.season_number,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # Shared instance methods
    # ------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Round amendment confirm view
# ---------------------------------------------------------------------------


async def _judge_round_amendment(
    bot: Any,
    rnd: RoundModel,
    amendments: list[tuple[str, object]],
    *,
    now: datetime,
):
    """Judge *amendments* against *rnd*, reading the league's own windows.

    Shared by `/round amend` and its confirmation so the two cannot disagree about a round, and
    judged afresh by each: a window can pass while the confirmation stands, and an amendment
    allowed on the strength of a window that has since closed is the silent loss the rules exist
    to prevent.

    A module that is switched off passes no windows and so has nothing refused on its account —
    a league without attendance has no check-in to lose.
    """
    from leaguebot.core.services.amendment_rules_service import judge_amendment

    # The windows are read through the bot's hook (`LeagueBot.round_windows_read`), as the change
    # on the queue reads them; weather's are read whatever the module's state, as a forecast
    # posted while weather was on is still posted.
    attendance, weather = await read_round_windows(bot.round_windows_read, amending=True)

    return judge_amendment(
        rnd, dict(amendments), now=now, attendance=attendance, weather=weather
    )


def _round_amend_named(round_number: int | None, division_name: str | None = None) -> str:
    """`/round amend` as its log lines name it: with the round, where it is known."""
    if round_number is None:
        return "`/round amend`"
    where = f" in {division_name}" if division_name else ""
    return f"`/round amend` of round {round_number}{where}"


class _ConfirmView(LeagueView):
    """The confirmation `/round amend` offers, for two minutes, before it amends anything.

    Every outcome is recorded in the log channel: a confirmation the rules or the round refuse,
    a Cancel, a confirmation left to lapse, a failure, and the success with each field it
    changed from its old value to its new one.
    """

    def __init__(
        self,
        cog: SeasonCog,
        interaction_user_id: int,
        round_id: int,
        amendments: list[tuple[str, object]],
        round_number: int | None = None,
    ) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._user_id = interaction_user_id
        self._round_id = round_id
        self._amendments = amendments
        self._round_number = round_number

    async def on_timeout(self) -> None:
        """Nobody confirmed within the two minutes: nothing was amended, and that is recorded."""
        await record_abandoned(
            self._cog.bot,
            self._user_id,
            what=_round_amend_named(self._round_number),
            lapsed=True,
            detail="Nothing was changed. Run `/round amend` again to start over.",
        )

    @discord.ui.button(label="\u2705 Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        what = _round_amend_named(self._round_number)
        if interaction.user.id != self._user_id:
            await refuse(interaction, "\u26d4 Not your action.", what=what)
            return

        # A fault anywhere in the press, the defer included, is reported here rather than left to
        # the view's failure path, so that the reply and the line name the round (see
        # `report_failure`). Every way out stops the buttons, a fault included: left running, the
        # view would lapse two minutes later and record that nothing was changed, beside the
        # failure already recorded for the same press, even where the round had been amended
        # before the fault.
        try:
            await interaction.response.defer(ephemeral=True)
            # The round is read for its number and its division, and the rest is the change
            # type's: the amendment is judged as it is asked for, and again as it comes up to
            # run, since this view stands for two minutes and the queue may hold it longer, and a
            # window can pass in either. The queue's acknowledgement is this press's answer, and
            # the outcome updates it.
            _rnd_now = await self._cog.bot.season_service.get_round(self._round_id)
            if _rnd_now is None:
                await refuse(
                    interaction,
                    "\u26d4 That round no longer exists. **Nothing has been changed.**",
                    what=what,
                )
                return
            what = _round_amend_named(_rnd_now.round_number)
            _division = await self._cog.bot.season_service.get_division(_rnd_now.division_id)
            # One change carrying every field, not one per field. Amending a round's track and
            # its date used to run the whole amendment twice: two invalidation notices,
            # two cancels, two re-arms, two re-runs of every overdue phase (#115).
            await self._cog.bot.change_queue.ask(
                ROUND_AMEND,
                {
                    "round_id": self._round_id,
                    "round_number": _rnd_now.round_number,
                    "division_id": _rnd_now.division_id,
                    "division_name": _division.name if _division is not None else "",
                    "changes": payload_changes(self._amendments),
                },
                interaction=interaction,
                what=what,
            )
        except Exception as exc:  # noqa: BLE001 — reported here, naming the round
            await report_failure(interaction, exc, what=what)
        finally:
            self.stop()

    @discord.ui.button(label="\u274c Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # Stopped first, so that a reply that cannot be sent is not followed by a lapse too.
        self.stop()
        await interaction.response.send_message("Amendment cancelled.", ephemeral=True)
        await record_abandoned(
            self._cog.bot,
            interaction.user,
            what=_round_amend_named(self._round_number),
            lapsed=False,
            detail="Nothing was changed. Run `/round amend` again to start over.",
        )
