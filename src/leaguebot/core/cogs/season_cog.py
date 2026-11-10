"""SeasonCog — the /season command group.

Commands:
  /season setup    — start season configuration (admin only)
  /season placements-review   — view pending config with Approve/Amend actions
  (placements are confirmed from the button `/season placements-review` posts, and by no command)
  /season status   — read-only summary of active season
  /season abort    — delete a season whose placements were never confirmed (league admin only)
  /season cancel   — cancel the season being raced, archiving it (league admin only, irreversible)
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import RoundFormat, RoundStatus
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services import approval_checks
from leaguebot.core.services.cancellation_changes import SEASON_CANCEL
from leaguebot.core.services.image_drawing import Held
from leaguebot.core.services.module_service import SwitchNotSignedUp
from leaguebot.core.services.round_moments import read_phases_drawn, read_round_windows
from leaguebot.core.services.confirmation_refusals import Confirmation, read_refusals
from leaguebot.core.services.review_sections import (
    Review,
    read_division_lines,
    read_review_sections,
)
from leaguebot.core.services.season_setup_change import KIND as SEASON_SETUP
from leaguebot.core.services.season_review_changes import POST as SEASON_REVIEW_POST, REVIEW_WINDOW
from leaguebot.core.services.season_end_changes import (
    ABORT_ONLY_BEFORE,
    PRE_CONFIRMATION,
    SEASON_ABORT,
    SEASON_COMPLETE,
    season_end_in_hand,
    season_end_refusal,
)
from leaguebot.core.services.season_service import SeasonCapacityCounts
from leaguebot.core.utils.channel_guard import (
    league_admin_only,
    league_manager_only,
    changes_nothing,
)
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.formatting import discord_ts
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.log_lines import refuse
from leaguebot.core.utils.messages import chunk_message

log = logging.getLogger(__name__)


#: The review's five minutes, as its question says them.
REVIEW_MINUTES = REVIEW_WINDOW // timedelta(minutes=1)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class _ReviewPoster:
    """Sends a season review's private messages: the faults a manager has to put right.

    A review's public messages are not sent from the command at all: the change queue posts them
    (`season.review.post`), so a refused post stops the queue and the review comes down by
    its take-down. What the command still says itself is said to the reviewer alone, an ephemeral
    followup that cannot be deleted by id, and the fault reports among them are the reason a
    manager knows what to fix. Every one is handed this rather than the interaction, so the type
    check sees every send; `tests/core/test_season_review_images.py` holds that nothing in
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
                    self.bot.db_path,
                    season_id,
                    drawing=self.bot.image_drawing,
                    module_service=self.bot.module_service,
                    fingerprint_areas_read=self.bot.fingerprint_areas_read,
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
        except SwitchNotSignedUp:
            raise  # a module that never signed up is a fault in the bot's assembly, not a reading
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
        change queue's check of the approval reads the same windows without a cog.
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
    # A league manager's, deliberately. Reading what a season is configured to
    # be is not an administrative act: the report is what the review is for. Approving it is
    # the narrower right, and `ApproveView` is where that is enforced.
    @league_manager_only
    async def season_review(self, interaction: discord.Interaction) -> None:
        confirmed = await self.bot.season_service.get_confirmed_season()
        if confirmed is not None:
            # Mid-season, the review covers the drivers of the window just closed.
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

        # Placements are reviewed only once the season is in Placements.
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
        # some of them (`season.review.post`). Only the private faults are replies.
        poster = _ReviewPoster(interaction)
        report: list[str] = []
        pictures: list[dict[str, Any]] = []

        season_num = f" (Season #{season.season_number} — F1 {season.game_edition})" if season.season_number > 0 else (f" (F1 {season.game_edition})" if season.game_edition > 0 else "")
        # The review is sent as one message per subsection rather than as one block. With
        # every not-OK image output stating its cause and its remedy it outgrows Discord's
        # 2000-character limit, and an over-long send loses the whole review rather than its
        # tail. Splitting by subject also means a manager can scroll back to the part they
        # are working on instead of hunting through one wall of text.
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

        # The divisions the review shows, read before the modules are asked for their sections
        # and for their lines of each division's configuration, which they hand for each of them.
        db_divisions = await self.bot.season_service.get_divisions(season.id)
        shown_ids = [division.id for division in db_divisions if division.name]
        sections = await read_review_sections(
            self.bot.review_sections_read,
            Review.PLACEMENTS,
            season_id=season.id,
            division_ids=shown_ids,
        )
        module_division_lines = await read_division_lines(
            self.bot.division_lines_read, season_id=season.id, division_ids=shown_ids
        )

        # ── Modules ───────────────────────────────────────────────
        # Each module's line, with the note its section hands to show beside it where it is on.
        weather_on = await self.bot.module_service.is_weather_enabled()
        results_on = await self.bot.module_service.is_results_enabled()
        images_on = await self.bot.module_service.is_images_enabled()
        notes = {section.module: section.modules_note for section in sections}
        on = "✅ Enabled"
        off = "❌ Disabled"
        header_lines.append("**Modules**")
        for name, enabled in (
            ("Weather", weather_on),
            ("Signup", await self.bot.module_service.is_signup_enabled()),
            ("Results", results_on),
            ("Attendance", await self.bot.module_service.is_attendance_enabled()),
            ("Images", images_on),
        ):
            note = notes.get(name.lower()) if enabled else None
            state = on if enabled else off
            header_lines.append(f"  {name}: {state} | {note}" if note else f"  {name}: {state}")
        header_lines.append("")

        # ── Image module (FR-033, FR-034) ─────────────────────────
        # When disabled, report that and omit the detail. When enabled, render the
        # aspect section from the same AspectStatus list `/images config view`
        # uses, so the two surfaces cannot drift.
        if images_on:
            image_lines += await self._build_image_review_section()

            # Every fault of the image configuration the section above names as blocking
            # — the rasteriser, a template a switched-on output draws, a per-tier colour,
            # the portrait settings — withholds the button, read through the helper the
            # confirmation refuses on, not read from the section, which draws only the lineup
            # and the calendar: a broken results or weather template, or a missing rasteriser
            # with both of those off, would otherwise be named here as blocking and then
            # approved.
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
                # And they do: the confirmation refuses on the same helper.
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
            cfg_lines: list[str] = [
                "\U0001f4c2 **Configuration**",
                f"  Role: <@&{div.mention_role_id}>",
                f"  Calendar channel: {cal_chan}",
                f"  Lineup channel: {lineup_chan}",
            ]
            for lines_of_module in module_division_lines:
                cfg_lines += lines_of_module.get(div.id, [])
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
                    now=self.bot.change_queue.now(),
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

        # ── Server-level unsettled signups ──────────
        # Named in the public report, every one of them, so that whoever reads the review sees who
        # is still to be placed or turned down — not merely how many.
        unsettled, channel_faults = await approval_checks.placement_confirmation_faults(
            self.bot.db_path, season.id, interaction.guild,
            driver_names_read=self.bot.driver_names_read,
            division_channel_faults_read=self.bot.division_channel_faults_read,
        )
        if unsettled:
            report.extend(chunk_message(
                "\u26a0\ufe0f **Unsettled signups** — each is to be placed with "
                "`/driver assign`, turned down with `/driver reject`, or reviewed:\n"
                + "\n".join(f"\u2022 {line}" for line in unsettled)
            ))
        # No season-wide report of rounds already inside a configured window: naming every
        # round against every window it had missed runs to sixty lines where two would do,
        # and past Discord's 2000-character limit for a season built wholly in the past,
        # which loses the whole message rather than its tail. Each division's calendar
        # carries its own two findings, above, where the manager is already looking at the
        # dates they would have to change.
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
    # /season placements-review mid-season — Ongoing, placements
    # ------------------------------------------------------------------

    async def _review_mid_season_placements(self, interaction: discord.Interaction, season) -> None:
        """Report the lineups as they will stand, and offer to confirm the new placements.

        Mid-season placements only affect lineups — no division or round can be added — so
        the review reports the drivers of the window just closed, each division's lineup with
        them in it, and every signup still unsettled.

        **It withholds its button on what would stop the confirmation**, as the first
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
            self.bot.db_path, season.id, interaction.guild,
            driver_names_read=self.bot.driver_names_read,
            division_channel_faults_read=self.bot.division_channel_faults_read,
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
    # /season config-review — confirming the configuration
    # ------------------------------------------------------------------

    async def _league_roles_review_lines(self) -> list[str]:
        """The league's base role and driver role, as the configuration review reports them.

        Shown whatever modules are enabled, the roles being the league's rather than the
        signup module's. Whether their absence is a fault is the signup module's to say; see
        `approval_checks.configuration_faults`.

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

    # No command confirms placements. They are confirmed from the button `/season placements-review`
    # posts and from nowhere else: the review is the evidence the confirmation rests on, and a
    # command that could be run without one would let a manager commit a season they had not looked
    # at. The button carries its own five-minute window, so what is confirmed is always a season
    # someone has just read.

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
            # Which phases a round has had drawn is weather's record, asked of it.
            drawn = await read_phases_drawn(
                self.bot.phases_drawn_read, [r.id for r in active_rounds]
            )
            next_round = next(
                (
                    r for r in active_rounds
                    if r.format != RoundFormat.MYSTERY
                    and not all(drawn.get(r.id, {}).get(phase, False) for phase in (1, 2, 3))
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
        """Abort the season being set up, before its placements are first confirmed.

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
        # the season is in: a cancellation stopped over an ongoing season is told so, not that an
        # ongoing season is cancelled.
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
