"""TestModeCog — /test-mode command group.

Provides subcommands for system-level testing without waiting for the real
APScheduler triggers:

  /test-mode toggle            — enable or disable test mode (state persists), a change on
                                 the change queue
  /test-mode nationality       — toggle whether mock drivers carry a nationality
  /test-mode advance           — immediately execute the next pending phase
  /test-mode review            — show season/round/phase status summary (ephemeral)
  /test-mode set-former-driver — set or clear a driver's former-driver flag
  /test-mode roster …          — manage the fake driver roster of a division
  /test-mode backup …          — save the whole database, and put it back

A module's own test tool sits under that module's group, not here, and is test mode's all the
same: `/attendance test rsvp` sets the RSVP status of fake drivers.

Every command here is a league admin's — the core specification places the whole of test
mode at that tier, backup and restore no more than the rest — and is given in the
interaction channel.
Every command but toggle additionally requires test mode to be active. Toggle itself is
refused outside a season's Configuration, and to *enable* test mode while the server holds real
drivers.
"""

from __future__ import annotations

import functools
import logging

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.services.test_mode_settings import NOT_IN_TEST_MODE
from leaguebot.core.services.test_roster_changes import (
    NOT_PLACING,
    TEST_ROSTER_ADD,
    TEST_ROSTER_ADD_BULK,
    TEST_ROSTER_CLEAR,
    TEST_ROSTER_REMOVE,
    bulk_payload,
)
from leaguebot.core.services.test_mode_former_driver_change import TEST_MODE_FORMER_DRIVER
from leaguebot.core.services.test_mode_toggle_change import TEST_MODE_TOGGLE
from leaguebot.core.services.test_mode_service import (
    PhaseEntry,
    build_review_summary,
    AdvanceStatus,
    advance_next_step,
)
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services import backup_service
from leaguebot.core.utils.autocomplete import bounded_autocomplete, team_autocomplete
from leaguebot.core.utils.channel_guard import league_admin_only, changes_nothing
from leaguebot.core.utils.input_validator import parse_user_id
from leaguebot.core.utils.interaction_errors import describe, describe_form, report_failure
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.formatting import paginate_fenced
from leaguebot.core.utils.league_server import LeagueModal, LeagueView
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.messages import chunk_message
from leaguebot.core.utils.roster_import import format_roster_errors as _format_roster_errors
from leaguebot.core.utils.roster_import import parse_roster_csv

log = logging.getLogger(__name__)


class TestModeCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Command group
    # ------------------------------------------------------------------

    test_mode = app_commands.Group(
        name="test-mode",
        description="Test mode commands for system verification",
        guild_only=True,
        default_permissions=None,
    )

    # ------------------------------------------------------------------
    # /test-mode toggle
    # ------------------------------------------------------------------

    @test_mode.command(
        name="toggle",
        description="Enable or disable test mode. State persists across bot restarts.",
    )
    @league_admin_only
    async def toggle(self, interaction: discord.Interaction) -> None:
        """Ask the change that flips test mode for the season in configuration.

        The command reads test mode once, to say which way it goes, and the change carries that
        state, judging it as it is asked and again as it starts (`test_mode_toggle_change`).
        """
        config = await self.bot.config_service.get_server_config()
        entering = config is not None and not config.test_mode_active
        await self.bot.change_queue.ask(
            TEST_MODE_TOGGLE, {"on": entering}, interaction=interaction, what=describe(interaction)
        )

    # ------------------------------------------------------------------
    # /test-mode nationality
    # ------------------------------------------------------------------

    @test_mode.command(
        name="nationality",
        description="Toggle whether mock drivers carry a nationality.",
    )
    @league_admin_only
    async def nationality(self, interaction: discord.Interaction) -> None:
        """The test-mode counterpart of /signup nationality.

        While test mode is active this switch stands in for the signup one everywhere the
        image module asks whether the league collects nationality, so the graphics of a
        server under test may be seen with flags and without them without the setting real
        signups run on being touched.
        """
        config = await self.bot.config_service.get_server_config()
        if config is None or not config.test_mode_active:
            await refuse(interaction, NOT_IN_TEST_MODE, what=describe(interaction))
            return

        await self.bot.change_queue.ask(
            "core.setting.test_mode_nationality_required",
            {"value": not config.test_mode_nationality_required},
            interaction=interaction,
            what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /test-mode advance
    # ------------------------------------------------------------------

    @test_mode.command(
        name="advance",
        description="Run the next scheduled event now: a forecast, a check-in step, results or a cleanup.",
    )
    @league_admin_only
    async def advance(self, interaction: discord.Interaction) -> None:
        # Check test mode is active before doing any heavy work
        config = await self.bot.config_service.get_server_config(

        )
        if config is None or not config.test_mode_active:
            await refuse(
                interaction,
                "ℹ️ Test mode is not active. Use `/test-mode toggle` to enable it first.",
                what=describe(interaction),
            )
            return

        # Defer because phase execution posts to Discord channels (can take seconds)
        await interaction.response.defer(ephemeral=True)

        # One moment for the step offered and the steps passed over, so that a race time falling
        # between two readings cannot leave a step both offered and named, or neither.
        run = await advance_next_step(
            self.bot.db_path,
            self.bot.scheduler_service,
            now=self.bot.change_queue.now(),
            announce=functools.partial(self._announce_advance, interaction),
            pending_steps_read=self.bot.pending_steps_read,
            advance_steps=self.bot.advance_steps,
        )
        entry = run.entry

        if entry is None:
            # The weather steps of raced rounds passed over on the way, named before the step
            # advanced.
            passed_over = [_passed_over_line(step) for step in run.passed_over]
            if passed_over:
                said = (
                    "Nothing else is due: the weather steps above were passed over, their races "
                    "having been run. There is nothing left to advance."
                )
                # The log channel's line stands alone: nothing sits above it there.
                reason = (
                    "Nothing is due but the weather steps of rounds whose races have been run, "
                    "which were passed over. There is nothing left to advance."
                )
            else:
                said = reason = (
                    "All phases for all rounds and divisions have been executed. "
                    "There is nothing left to advance."
                )
            await refuse(
                interaction,
                "\n".join([*passed_over, f"ℹ️ {said}"])
                + "\nUse `/season complete` when all rounds are finalized to end the season.",
                what=describe(interaction),
                reason=reason,
            )
            return

        # A step that failed is reported here, to name the step and the round; any other fault
        # goes to the command tree's handler.
        if run.failure is not None:
            failure = run.failure
            await report_failure(
                interaction,
                failure.__cause__ or failure,
                what=self._advance_what(interaction, failure.step, entry, track=failure.track),
            )
            return
        outcome = run.outcome
        assert outcome is not None  # an entry picked ends in an outcome or a failure
        if outcome.status is AdvanceStatus.REFUSED:
            await refuse(interaction, outcome.reply, what=describe(interaction))
            return
        await interaction.followup.send(outcome.reply, ephemeral=True)
        if outcome.status is AdvanceStatus.ASKED:
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode advance | Success\n"
                f"{outcome.log}",
            )

    @staticmethod
    async def _announce_advance(
        interaction: discord.Interaction,
        entry: PhaseEntry,
        passed_over: list[PhaseEntry],
    ) -> None:
        """Name the steps passed over to the maintainer and note the step picked, before it
        runs."""
        lines = [_passed_over_line(step) for step in passed_over]
        for part in chunk_message("\n".join(lines)) if lines else ():
            await interaction.followup.send(part, ephemeral=True)

        phase_number = entry["phase_number"]
        log.info(
            "Test mode advance: Phase %s, round=%d (id=%d), division=%s, track=%s",
            "mystery-notice" if phase_number == 0 else phase_number,
            entry["round_number"],
            entry["round_id"],
            entry["division_name"],
            entry["track_name"],
        )

    @staticmethod
    def _advance_what(
        interaction: discord.Interaction,
        step: str,
        entry: PhaseEntry,
        *,
        track: str | None = None,
    ) -> str:
        """Name *step* of a round as the failure reply and the log line should read it:
        the command, the step, the division and the round (and the track of a weather phase)."""
        where = f"{entry['division_name']}, Round {entry['round_number']}"
        if track is not None:
            where += f" ({track})"
        return f"{describe(interaction)} at {step} for {where}"

    # ------------------------------------------------------------------
    # /test-mode review
    # ------------------------------------------------------------------

    @test_mode.command(
        name="review",
        description="Show season configuration and phase completion status.",
    )
    @league_admin_only
    @changes_nothing
    async def review(self, interaction: discord.Interaction) -> None:
        config = await self.bot.config_service.get_server_config(

        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "ℹ️ Test mode is not active. Use `/test-mode toggle` to enable it first.",
                ephemeral=True,
            )
            return

        summary = await build_review_summary(
            self.bot.db_path,
            self.bot.scheduler_service,
            now=self.bot.change_queue.now(),
            review_marks_read=self.bot.review_marks_read,
        )
        # Discord message limit is 2000 characters; chunk if needed.
        chunks = [summary[i:i + 2000] for i in range(0, len(summary), 2000)]
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    # ------------------------------------------------------------------
    # /test-mode set-former-driver
    # ------------------------------------------------------------------

    @test_mode.command(
        name="set-former-driver",
        description="Manually set the former_driver flag on a driver profile (test mode only).",
    )
    @app_commands.describe(
        user="The driver whose flag is being updated.",
        value="The new value for the former_driver flag.",
    )
    @league_admin_only
    async def set_former_driver(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        value: bool,
    ) -> None:
        """Ask the change that sets the flag of the driver holding the account picked.

        Test mode and the profile are judged by the change, as it is asked and as it starts
        (`test_mode_former_driver_change`).
        """
        await self.bot.change_queue.ask(
            TEST_MODE_FORMER_DRIVER,
            {"user_id": str(user.id), "name": user.display_name, "value": value},
            interaction=interaction,
            what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # /test-mode backup (subgroup)
    # ------------------------------------------------------------------
    #
    # Under `/test-mode` because that is the only circumstance in which they run. They
    # copy and replace the whole database, and the test-mode gate below is what stands
    # between that and a league's history — putting them anywhere else would suggest they
    # were an ordinary administrative tool.

    backup = app_commands.Group(
        name="backup",
        description="Save the database while testing, and put it back.",
        parent=test_mode,
        guild_only=True,
        default_permissions=None,
    )

    async def _refuse_roster_change_outside_placements(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Reply and return True where the season is not in Placements, having recorded the refusal
        in the log channel unless *record* is False.

        Fake drivers are seated, removed and cleared only while the season is in Placements,
        which is where real drivers are placed too: test mode replicates the live flow rather
        than inventing one. Listing the roster is left free.
        """
        from leaguebot.core.services.season_lifecycle_service import live_season_stage

        live = await live_season_stage(self.bot.db_path)
        if live is not None and live[1] is SeasonStage.PLACEMENTS:
            return False
        reply = NOT_PLACING
        if record:
            await refuse(interaction, reply, what=describe(interaction))
        else:
            await interaction.response.send_message(reply, ephemeral=True)
        return True

    async def _refuse_outside_test_mode(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Reply and return True where the server is not in test mode, having recorded the
        refusal in the log channel unless *record* is False.

        Read at the moment of the command rather than trusted from earlier: the flag can
        be turned off between one command and the next, and a restore is not something to
        run on the strength of a stale reading. `/test-mode backup status` is a view, and
        passes `record=False`: a view records nothing.
        """
        config = await self.bot.config_service.get_server_config(

        )
        if config is not None and config.test_mode_active:
            return False
        reply = (
            "⛔ The backup commands run only while the server is in **test mode**. They "
            "copy and replace the whole database, which is not something to do to a "
            "league that is running. Turn test mode on with `/test-mode toggle` first."
        )
        if record:
            await refuse(interaction, reply, what=describe(interaction))
        else:
            await interaction.followup.send(reply, ephemeral=True)
        return True

    # ── save ──────────────────────────────────────────────────────────────

    @backup.command(
        name="save",
        description="Save the current database and scheduler as a backup.",
    )
    @league_admin_only
    async def backup_save(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        db_path = self.bot.db_path
        scheduler = self.bot.scheduler_service

        # Paused around the copy, so no job fires while it is taken. That does not make the
        # copy whole: the copy is synchronous on the event-loop thread, which is what keeps
        # `add_job` from writing the job store mid-copy, and it goes through SQLite's backup API.
        try:
            with scheduler.paused():
                backup_service.save(db_path, scheduler.jobstore_path)
        except backup_service.BackupFault as exc:
            # A copy, write or check that failed: a fault in the bot, and one that left the
            # previous backup as it was. Any other error goes to the tree's failure handler,
            # which says the save may have been partly done.
            await report_failure(
                interaction,
                exc,
                what=describe(interaction),
                outcome="The previous backup is unchanged.",
            )
            return
        except backup_service.BackupError as exc:
            # Locked: something the maintainer can act on, so a refusal.
            await refuse(interaction, f"⛔ {exc}", what=describe(interaction))
            return

        state = backup_service.state(db_path)
        await interaction.followup.send(
            f"✅ Saved. The backup holds {state.size_bytes // 1024} KB and replaces "
            f"whatever was there before.\n"
            f"Lock it with `/test-mode backup lock` if you want to keep this one.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode backup save | Success\n"
            f"  size_kb: {state.size_bytes // 1024}",
        )
        log.info(
            "backup save: by %s", interaction.user
        )

    # ── lock ──────────────────────────────────────────────────────────────

    @backup.command(
        name="lock",
        description="Lock or unlock the saved backup, so a save cannot overwrite it.",
    )
    @league_admin_only
    async def backup_lock(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        db_path = self.bot.db_path
        if not backup_service.state(db_path).exists:
            await refuse(
                interaction,
                "⛔ There is no saved backup to lock. Take one with "
                "`/test-mode backup save`.",
                what=describe(interaction),
            )
            return

        locked = backup_service.set_lock(
            db_path, who=interaction.user.display_name, now=self.bot.change_queue.now()
        )
        await interaction.followup.send(
            "🔒 Locked. `/test-mode backup save` will refuse to overwrite it until you "
            "run this again."
            if locked
            else "🔓 Unlocked. `/test-mode backup save` will overwrite it from now on.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode backup lock | Success\n"
            f"  backup: {'locked' if locked else 'unlocked'}",
        )

    # ── status ────────────────────────────────────────────────────────────

    @backup.command(
        name="status",
        description="Show whether a backup exists, when it was taken, and if it is locked.",
    )
    @league_admin_only
    @changes_nothing
    async def backup_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(
            interaction, record=False
        ):
            return

        state = backup_service.state(self.bot.db_path)
        if not state.exists or state.taken_at is None:
            await interaction.followup.send(
                "📭 There is no saved backup. Take one with `/test-mode backup save`.",
                ephemeral=True,
            )
            return

        lines = [
            "📦 **Saved backup**",
            f"  Taken: {discord.utils.format_dt(state.taken_at, 'F')}",
            f"  Size: {state.size_bytes // 1024} KB",
            f"  Readable: {'yes' if state.readable else '**no — it cannot be restored**'}",
            f"  Locked: {state.locked_by if state.locked else 'no'}",
        ]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ── restore ───────────────────────────────────────────────────────────

    @backup.command(
        name="restore",
        description="Replace the database with the saved backup. Needs a restart.",
    )
    @league_admin_only
    async def backup_restore(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        state = backup_service.state(self.bot.db_path)
        if not state.exists or state.taken_at is None:
            await refuse(
                interaction,
                "⛔ There is no saved backup to restore. Take one with "
                "`/test-mode backup save`.",
                what=describe(interaction),
            )
            return
        if not state.readable:
            await refuse(
                interaction,
                "⛔ The saved backup is not a readable database, so it will not be "
                "restored. Take a fresh one with `/test-mode backup save`.",
                what=describe(interaction),
            )
            return

        # Confirmed rather than done: everything the bot currently holds is about to be
        # replaced by a snapshot of some earlier moment, and the command that does it is
        # one word from `/test-mode backup save`.
        view = _ConfirmRestoreView(self, interaction.user.id)
        await interaction.followup.send(
            f"⚠️ **Restore the backup taken "
            f"{discord.utils.format_dt(state.taken_at, 'R')}?**\n"
            f"Everything the bot holds now — seasons, rounds, drivers, results and every "
            f"scheduled job — is replaced by what that backup holds. A copy of the current "
            f"database is kept beside it first.\n"
            f"**The bot must be restarted afterwards** to pick it up.",
            view=view,
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /test-mode roster (subgroup)
    # ------------------------------------------------------------------

    roster = app_commands.Group(
        name="roster",
        description="Manage fake driver roster for test mode.",
        parent=test_mode,
        guild_only=True,
        default_permissions=None,
    )

    # /test-mode roster add ------------------------------------------------

    @roster.command(
        name="add",
        description="Add a fake driver to a team in a division.",
    )
    @app_commands.describe(
        driver_name="Display name for the fake driver.",
        team_name="The shorthand of the team the driver is seated in.",
        division="Name of the division.",
        nationality="Optional. A nationality (e.g. British), a country name (e.g. United Kingdom), or 'other'.",
    )
    @league_admin_only
    async def roster_add(
        self,
        interaction: discord.Interaction,
        driver_name: str,
        team_name: str,
        division: str,
        nationality: str | None = None,
    ) -> None:
        """Ask the change that seats the fake driver.

        Test mode, the season's stage, the nationality and the seat are judged by the change, as it
        is asked and as it starts (`test_roster_changes`).
        """
        await self.bot.change_queue.ask(
            TEST_ROSTER_ADD,
            {
                "driver_name": driver_name,
                "team_name": team_name,
                "division": division,
                "nationality": nationality,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    # /test-mode roster add-bulk -------------------------------------------

    @roster.command(
        name="add-bulk",
        description="Seat a whole roster from the generator's roster.csv.",
    )
    @league_admin_only
    async def roster_add_bulk(self, interaction: discord.Interaction) -> None:
        """Open the box a roster is pasted into.

        **Does not defer.** `send_modal` has to be the interaction's first response, so nothing
        before it may answer or defer but a refusal that ends the command. `roster_add` hands
        the interaction to the change queue, which answers it; this command asks nothing until
        the form is submitted. Do not "correct" it.
        """
        config = await self.bot.config_service.get_server_config(

        )
        if config is None or not config.test_mode_active:
            await refuse(
                interaction,
                NOT_IN_TEST_MODE,
                what=describe(interaction),
            )
            return
        if await self._refuse_roster_change_outside_placements(interaction):
            return

        await interaction.response.send_modal(_RosterImportModal(self))

    # /test-mode roster remove --------------------------------------------

    @roster.command(
        name="remove",
        description="Remove a single fake driver by their synthetic user ID.",
    )
    @app_commands.describe(
        user_id="Synthetic user ID of the fake driver (shown in /test-mode roster list or roster add).",
    )
    @league_admin_only
    async def roster_remove(
        self,
        interaction: discord.Interaction,
        user_id: str,
    ) -> None:
        """Ask the change that removes the fake driver, once the ID reads as a number.

        Test mode, the season's stage and the driver are judged by the change, as it is asked and
        as it starts (`test_roster_changes`).
        """
        discord_uid = parse_user_id(user_id)
        if discord_uid is None:
            await refuse(
                interaction,
                "❌ `user_id` must be a numeric Discord user ID.",
                what=describe(interaction),
            )
            return
        await self.bot.change_queue.ask(
            TEST_ROSTER_REMOVE,
            {"user_id": str(discord_uid)},
            interaction=interaction,
            what=describe(interaction),
        )

    # /test-mode roster list -----------------------------------------------

    @roster.command(
        name="list",
        description="Show all fake drivers in a division (cheat sheet for result submission).",
    )
    @app_commands.describe(division="Name of the division.")
    @league_admin_only
    @changes_nothing
    async def roster_list(
        self,
        interaction: discord.Interaction,
        division: str,
    ) -> None:
        # A division's roster outgrows one Discord message somewhere past twenty
        # drivers, and the listing is then sent as several — so the reply is deferred
        # and every page goes out through followup.
        await interaction.response.defer(ephemeral=True)

        config = await self.bot.config_service.get_server_config(

        )
        if config is None or not config.test_mode_active:
            await interaction.followup.send(
                NOT_IN_TEST_MODE,
                ephemeral=True,
            )
            return

        from leaguebot.core.services.test_roster_service import list_test_drivers

        result = await list_test_drivers(
            division_name=division,
            db_path=self.bot.db_path,
        )

        if isinstance(result, str):
            await interaction.followup.send(f"⛔ {result}", ephemeral=True)
            return

        if not result:
            await interaction.followup.send(
                f"ℹ️ No fake drivers in **{division}**. Use `/test-mode roster add` to create some.",
                ephemeral=True,
            )
            return

        body = [f"{'Name':<20} {'Mention':<30} {'Team':<20} Nationality", "-" * 86]
        for driver in result:
            mention = f"<@{driver['discord_user_id']}>"
            body.append(
                f"{driver['display_name']:<20} {mention:<30} "
                f"{driver['team_name']:<20} {driver['nationality'] or '—'}"
            )

        for page in paginate_fenced(
            header=f"**Fake Driver Roster — {division}**",
            body_lines=body,
            footer=(
                "Copy mention strings above when submitting results in the format:\n"
                "`Position, <@user_id>, <@&role_id>, ...`"
            ),
        ):
            await interaction.followup.send(page, ephemeral=True)

    # /test-mode roster clear ----------------------------------------------

    @roster.command(
        name="clear",
        description="Remove all fake drivers from a division.",
    )
    @app_commands.describe(division="Name of the division to clear.")
    @league_admin_only
    async def roster_clear(
        self,
        interaction: discord.Interaction,
        division: str,
    ) -> None:
        """Ask the change that clears the division of its fake drivers.

        Test mode, the season's stage and the division are judged by the change, as it is asked
        and as it starts (`test_roster_changes`).
        """
        await self.bot.change_queue.ask(
            TEST_ROSTER_CLEAR,
            {"division": division},
            interaction=interaction,
            what=describe(interaction),
        )

    # (submit-results removed — use /test-mode advance which now handles result submission)

    # ------------------------------------------------------------------
    # The team parameter's autocomplete
    # ------------------------------------------------------------------

    @roster_add.autocomplete("team_name")
    @bounded_autocomplete()
    async def _roster_team_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """A team is typed by its shorthand; offered under both its names, Reserve included."""
        return await team_autocomplete(self.bot, current, include_reserve=True)


class _RosterImportModal(LeagueModal, title="Import a test roster"):
    """The box a `roster.csv` is pasted into.

    **4000 characters is the whole of what Discord allows a text input**, which is roughly
    sixty drivers at the width the generator writes. A larger roster is imported in two
    passes — the import appends to what a season already holds, so a second paste naming
    only the divisions still empty lands cleanly. A paste naming a division that already
    holds drivers is refused whole, its empty divisions with it.
    """

    # The placeholder is capped at 100 characters by Discord, which is not room for an
    # example row; it names the file instead, and the label carries the shape.
    csv_text: discord.ui.TextInput = discord.ui.TextInput(
        label="Paste roster.csv, header and all",
        style=discord.TextStyle.paragraph,
        placeholder="ID,Driver name,Team,Division,Nationality",
        required=True,
        max_length=4000,
    )

    def __init__(self, cog: "TestModeCog") -> None:
        super().__init__()
        self._cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Ask the change that seats the roster, once the file reads on its own.

        What a file shows wrong by itself is refused here, before the ask. Test mode, the
        season's stage and everything the database must answer are judged by the change, as it is
        asked and as it starts (`test_roster_changes`).
        """
        await interaction.response.defer(ephemeral=True)

        drivers, errors = parse_roster_csv(str(self.csv_text.value))
        if errors:
            await refuse(
                interaction,
                _format_roster_errors(errors),
                what=describe_form(self),
            )
            return

        await self._cog.bot.change_queue.ask(
            TEST_ROSTER_ADD_BULK,
            bulk_payload(drivers),
            interaction=interaction,
            what=describe_form(self),
        )


def _passed_over_line(step: PhaseEntry) -> str:
    """The line naming a raced round's weather step `/test-mode advance` passed over, in the shape
    of its reply where the change queue drops a step as not due, marked ⏭️ as the review marks
    it: a mystery round's notice as advance names it, a phase by its number."""
    where = f"**{step['division_name']}** — **Round {step['round_number']}**"
    what = (
        "The **Mystery Round notice**" if step["phase_number"] == 0
        else f"**Phase {step['phase_number']}**"
    )
    return f"⏭️ {what} for {where} was not due: its race has been run."


class _ConfirmRestoreView(LeagueView):
    """The confirmation on a restore, and the staging behind it."""

    def __init__(self, cog: "TestModeCog", requester_id: int) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._requester_id = requester_id

    @discord.ui.button(label="♻️ Restore", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._requester_id:
            await refuse(
                interaction,
                "⛔ Only the person who ran the command can confirm it.",
                what=describe(interaction, button),
            )
            return

        # Stopped as the press is accepted, so a reply that fails later cannot leave a
        # view to lapse with "Nothing was restored." over a staged restore, nor let a
        # second press stage it again.
        self.stop()
        await interaction.response.defer(ephemeral=True)
        bot = self._cog.bot
        try:
            backup_service.stage_restore(bot.db_path, bot.scheduler_service.jobstore_path)
        except backup_service.BackupFault as exc:
            # A copy or write that failed. Staging undoes itself, so nothing stands.
            await report_failure(
                interaction,
                exc,
                what=describe(interaction, button),
                outcome="Nothing was restored.",
            )
            return
        except backup_service.BackupError as exc:
            # Something the maintainer can act on (no backup, an unreadable one).
            await refuse(interaction, f"⛔ {exc}", what=describe(interaction, button))
            return
        except Exception as exc:  # noqa: BLE001 — a file may have been staged before it stopped
            log.exception("backup restore: staging failed")
            await report_failure(interaction, exc, what=describe(interaction, button))
            return

        await interaction.followup.send(
            "✅ The backup is staged. **Restart the bot** and it will come back on the "
            "restored database — under a service it will restart itself; from a terminal, "
            "stop it and run it again.\n"
            "The database being replaced was copied to `bot.prerestore.db` first, so this "
            "can be walked back by hand if it was not what you wanted.",
            ephemeral=True,
        )
        await bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | "
            "/test-mode backup restore | Success\n"
            "  restore: staged; the bot must be restarted to come back on it",
        )
        log.info("backup restore: staged by %s", interaction.user)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Nothing has been changed.", ephemeral=True
        )
        self.stop()
        await record_abandoned(
            interaction.client,
            interaction.user,
            what="`/test-mode backup restore`",
            lapsed=False,
            detail="Nothing was restored. Run `/test-mode backup restore` again to restore.",
        )

    async def on_timeout(self) -> None:
        """Record that the confirmation lapsed unanswered, naming who started it."""
        await record_abandoned(
            self._cog.bot,
            self._requester_id,
            what="`/test-mode backup restore`",
            lapsed=True,
            detail="Nothing was restored. Run `/test-mode backup restore` again to restore.",
        )
