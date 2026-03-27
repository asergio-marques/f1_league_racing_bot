"""Entry point for the F1 League Weather Randomizer Bot."""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN: str = os.environ["BOT_TOKEN"]
DB_PATH: str = os.getenv("DB_PATH", "bot.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    intents.message_content = True  # required for signup wizard on_message dispatch

    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    bot.db_path = DB_PATH  # type: ignore[attr-defined]

    return bot


async def main() -> None:
    from db.database import run_migrations
    from services.config_service import ConfigService
    from services.season_service import SeasonService
    from services.amendment_service import AmendmentService
    from services.scheduler_service import SchedulerService
    from utils.output_router import OutputRouter

    bot = create_bot()

    # Services are attached to bot for cog access
    from services.driver_service import DriverService
    from services.team_service import TeamService

    bot.config_service = ConfigService(DB_PATH)      # type: ignore[attr-defined]
    bot.season_service = SeasonService(DB_PATH)      # type: ignore[attr-defined]
    bot.amendment_service = AmendmentService(DB_PATH)  # type: ignore[attr-defined]
    bot.scheduler_service = SchedulerService(DB_PATH)  # type: ignore[attr-defined]
    bot.output_router = OutputRouter(bot, retry_db_path=DB_PATH)  # type: ignore[attr-defined]
    bot.driver_service = DriverService(DB_PATH)      # type: ignore[attr-defined]
    bot.team_service = TeamService(DB_PATH)          # type: ignore[attr-defined]

    from services.placement_service import PlacementService
    bot.placement_service = PlacementService(DB_PATH)  # type: ignore[attr-defined]

    from services.module_service import ModuleService
    from services.signup_module_service import SignupModuleService
    from services.wizard_service import WizardService
    from utils.output_router import OutputRouter as _OutputRouter  # already imported above

    bot.module_service = ModuleService(DB_PATH)          # type: ignore[attr-defined]
    bot.signup_module_service = SignupModuleService(DB_PATH)  # type: ignore[attr-defined]
    bot.wizard_service = WizardService(  # type: ignore[attr-defined]
        DB_PATH,
        bot.scheduler_service,  # type: ignore[attr-defined]
        bot.output_router,  # type: ignore[attr-defined]
    )

    @bot.event
    async def on_ready() -> None:
        log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)

        # Run DB migrations on startup
        await run_migrations(DB_PATH)

        # Start the persistent APScheduler
        bot.scheduler_service.start()

        # Wire phase service callbacks into scheduler
        from services.phase1_service import run_phase1
        from services.phase2_service import run_phase2
        from services.phase3_service import run_phase3

        async def _p1(round_id: int) -> None:
            await run_phase1(round_id, bot)

        async def _p2(round_id: int) -> None:
            await run_phase2(round_id, bot)

        async def _p3(round_id: int) -> None:
            await run_phase3(round_id, bot)

        bot.scheduler_service.register_callbacks(_p1, _p2, _p3)

        # Register mystery round notice callback
        from services.mystery_notice_service import run_mystery_notice

        async def _mystery_notice_cb(round_id: int) -> None:
            await run_mystery_notice(round_id, bot)

        bot.scheduler_service.register_mystery_notice_callback(_mystery_notice_cb)

        # Register post-race forecast cleanup callback
        from services.forecast_cleanup_service import run_post_race_cleanup

        async def _forecast_cleanup_cb(round_id: int) -> None:
            await run_post_race_cleanup(round_id, bot)

        bot.scheduler_service.register_forecast_cleanup_callback(_forecast_cleanup_cb)

        # Register result submission callback
        from services.result_submission_service import run_result_submission_job

        async def _result_submission_cb(round_id: int) -> None:
            await run_result_submission_job(round_id, bot)

        bot.scheduler_service.register_result_submission_callback(_result_submission_cb)

        # Register season-end callback (stored in _GLOBAL_SERVICE so the
        # module-level _season_end_job can reach it without pickling a closure)
        from services.season_end_service import execute_season_end as _execute_season_end

        async def _season_end_cb(server_id: int, season_id: int) -> None:
            await _execute_season_end(server_id, season_id, bot)

        bot.scheduler_service.register_season_end_callback(_season_end_cb)

        # Close any submission channels that were left open by a previous run.
        # Their wait_for loops died with the process, so we reset them here
        # so /test-mode advance can re-trigger submission.
        await _recover_orphaned_submission_channels(bot)

        # Close any results-amend channels left open by a previous run.
        await _recover_orphaned_amend_channels(bot)

        # Recover any missed phases from before bot restart
        await _recover_missed_phases(bot)

        # Recover any season-end jobs that were lost during a restart
        await _recover_season_end_jobs(bot)

        # Wire wizard service bot reference (needed for guild/service access)
        bot.wizard_service.set_bot(bot)  # type: ignore[attr-defined]

        # Re-arm inactivity APScheduler jobs for any non-UNENGAGED wizard sessions
        # that were active before the last restart.
        try:
            await bot.wizard_service.recover_wizards()  # type: ignore[attr-defined]
        except NotImplementedError:
            pass  # stub until T030 is implemented

        # Restore in-memory pending setups from DB SETUP seasons
        await _recover_pending_setups(bot)

        # Sync slash commands globally (may take up to 1 hour to propagate)
        try:
            synced = await bot.tree.sync()
            log.info("Synced %d slash command(s)", len(synced))
        except discord.HTTPException as exc:
            log.error("Failed to sync slash commands: %s", exc)

    @bot.event
    async def on_disconnect() -> None:
        log.warning("Bot disconnected from Discord")

    # --- Load Cogs ---
    from cogs.init_cog import InitCog
    from cogs.season_cog import SeasonCog
    from cogs.amendment_cog import AmendmentCog
    from cogs.test_mode_cog import TestModeCog
    from cogs.reset_cog import ResetCog
    from cogs.track_cog import TrackCog
    from cogs.driver_cog import DriverCog
    from cogs.team_cog import TeamCog
    from cogs.module_cog import ModuleCog
    from cogs.signup_cog import SignupCog
    from cogs.admin_review_cog import AdminReviewCog
    from cogs.retry_cog import RetryCog
    from cogs.results_cog import ResultsCog

    await bot.add_cog(InitCog(bot))
    await bot.add_cog(SeasonCog(bot))
    await bot.add_cog(AmendmentCog(bot))
    await bot.add_cog(TestModeCog(bot))
    await bot.add_cog(ResetCog(bot))
    await bot.add_cog(TrackCog(bot))
    await bot.add_cog(DriverCog(bot))
    await bot.add_cog(TeamCog(bot))
    await bot.add_cog(ModuleCog(bot))
    await bot.add_cog(SignupCog(bot))
    await bot.add_cog(AdminReviewCog(bot))
    await bot.add_cog(RetryCog(bot))
    await bot.add_cog(ResultsCog(bot))

    # Register ALL persistent views so button interactions survive bot restarts.
    # Views with optional __init__ params resolve driver context from channel at
    # interaction time, so a single registration handles all active wizard sessions.
    from cogs.signup_cog import (
        SignupButtonView,
        WithdrawButtonView,
        NoNotesButtonView,
        PlatformButtonView,
        DriverTypeButtonView,
        PreferredTeamsButtonView,
        NoPreferenceTeammateView,
    )
    from cogs.admin_review_cog import AdminReviewView, CorrectionParameterView
    from services.penalty_wizard import PenaltyReviewView, ApprovalView

    for _view in (
        SignupButtonView(),
        WithdrawButtonView(),
        NoNotesButtonView(),
        PlatformButtonView(),
        DriverTypeButtonView(),
        PreferredTeamsButtonView(),   # registers pteam_0..pteam_19 stubs + nopref + cancel
        NoPreferenceTeammateView(),
        AdminReviewView(),
        CorrectionParameterView(),
        PenaltyReviewView(),
        ApprovalView(),
    ):
        bot.add_view(_view)

    log.info("All cogs loaded. Starting bot...")
    async with bot:
        await bot.start(TOKEN)


async def _recover_missed_phases(bot: commands.Bot) -> None:
    """Re-fire any weather phases whose horizon has passed but were not executed."""
    from db.database import get_connection
    from services.phase1_service import run_phase1
    from services.phase2_service import run_phase2
    from services.phase3_service import run_phase3
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    async with get_connection(bot.db_path) as db:  # type: ignore[attr-defined]
        cursor = await db.execute(
            """
            SELECT r.id, r.scheduled_at,
                   r.phase1_done, r.phase2_done, r.phase3_done,
                   r.format, s.server_id
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE s.status = 'ACTIVE'
              AND r.format != 'MYSTERY'
            """
        )
        rows = await cursor.fetchall()

    for row in rows:
        round_id, scheduled_at_str, p1, p2, p3, fmt, server_id = row
        scheduled_at = datetime.fromisoformat(scheduled_at_str)
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        # Only recover phases for servers with weather module active
        if not await bot.module_service.is_weather_enabled(server_id):  # type: ignore[attr-defined]
            continue

        phase1_horizon = scheduled_at - __import__("datetime").timedelta(days=5)
        phase2_horizon = scheduled_at - __import__("datetime").timedelta(days=2)
        phase3_horizon = scheduled_at - __import__("datetime").timedelta(hours=2)

        if not p1 and now >= phase1_horizon:
            log.info("Recovery: firing Phase 1 for round %s", round_id)
            await run_phase1(round_id, bot)
        if not p2 and now >= phase2_horizon:
            log.info("Recovery: firing Phase 2 for round %s", round_id)
            await run_phase2(round_id, bot)
        if not p3 and now >= phase3_horizon:
            log.info("Recovery: firing Phase 3 for round %s", round_id)
            await run_phase3(round_id, bot)


async def _recover_season_end_jobs(bot: commands.Bot) -> None:
    """No-op: season end is now triggered only via /season complete.

    Previously this re-registered APScheduler season-end jobs on restart and
    could auto-fire execute_season_end for past-due seasons. That behaviour has
    been removed — league managers must explicitly run /season complete.
    """


async def _recover_orphaned_submission_channels(bot: commands.Bot) -> None:
    """Close any submission channels left open by a previous bot process.

    When the bot restarts, any in-progress wait_for submission loops are
    killed mid-flight.  The DB row remains closed=0 and the Discord channel
    still exists, but nothing is listening.  We clean them up here.

    Mid-submission orphans (in_penalty_review=0):
      - Delete the session_results rows for the round (so the round is not
        mistakenly treated as complete by get_next_pending_phase).
      - Delete the orphaned round_submission_channels row and Discord channel.
      - Re-trigger run_result_submission_job so the wizard opens immediately
        (production path — test mode uses /test-mode advance instead).

    Penalty-review orphans (in_penalty_review=1):
      - Re-post the penalty review prompt.  skip_results_post is set based on
        the results_posted column so we never re-post interim results that were
        already sent before the crash.
      - If staged_penalties is set the penalties were already committed to the
        DB before the previous crash; a warning is posted in the channel so the
        LM knows not to re-add them before approving.
    """
    from db.database import get_connection

    async with get_connection(bot.db_path) as db:  # type: ignore[attr-defined]
        cursor = await db.execute(
            """
            SELECT rsc.round_id, rsc.channel_id, rsc.in_penalty_review,
                   rsc.results_posted, rsc.staged_penalties,
                   r.division_id, s.server_id
            FROM round_submission_channels rsc
            JOIN rounds r    ON r.id  = rsc.round_id
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons s   ON s.id  = d.season_id
            WHERE rsc.closed = 0
            """
        )
        orphans = await cursor.fetchall()

    for row in orphans:
        round_id: int = row["round_id"]
        channel_id: int = row["channel_id"]
        in_penalty_review: int = row["in_penalty_review"]
        results_posted: int = row["results_posted"]
        staged_penalties_json: str | None = row["staged_penalties"]
        division_id: int = row["division_id"]
        server_id: int = row["server_id"]

        guild = bot.get_guild(server_id)  # type: ignore[attr-defined]

        if in_penalty_review:
            # The bot restarted while a round was awaiting penalty review.
            # Re-post the penalty review prompt instead of deleting the channel.
            if guild is None:
                log.warning(
                    "Recovery: guild %s not in cache, cannot restore penalty review for round %s",
                    server_id, round_id,
                )
                continue
            channel = guild.get_channel(channel_id)
            if channel is None:
                log.warning(
                    "Recovery: channel %s not found, cannot restore penalty review for round %s",
                    channel_id, round_id,
                )
                continue
            try:
                # If staged_penalties is set, penalties were already written to
                # driver_session_results before the crash.  Warn the LM before
                # re-posting the prompt with an empty staged list so they know
                # not to re-add those penalties before approving.
                if staged_penalties_json:
                    import json as _json
                    try:
                        entries = _json.loads(staged_penalties_json)
                        lines = [
                            "⚠️ **The bot restarted mid-finalization.** "
                            "The penalties listed below were **already applied to the results** "
                            "before the crash. Do **not** re-add them — just approve as-is to finalize.",
                            "",
                        ]
                        for e in entries:
                            stype = e.get("session_type", "?").replace("_", " ").title()
                            ptype = e.get("penalty_type", "?")
                            psecs = e.get("penalty_seconds")
                            uid = e.get("driver_user_id", "?")
                            label = f"+{psecs}s" if ptype == "TIME" and psecs is not None else ptype
                            lines.append(f"• <@{uid}> | {stype} | **{label}**")
                        await channel.send("\n".join(lines))
                    except Exception:
                        log.exception(
                            "Recovery: failed to post staged_penalties warning for round %s", round_id
                        )

                from services.result_submission_service import enter_penalty_state

                await enter_penalty_state(
                    bot, guild, round_id, division_id, channel,
                    skip_results_post=bool(results_posted),
                )
                log.info(
                    "Recovery: restored penalty review prompt for round %s in channel %s "
                    "(results_posted=%s, had_staged_penalties=%s)",
                    round_id, channel_id, results_posted, bool(staged_penalties_json),
                )
            except Exception:
                log.exception(
                    "Recovery: failed to restore penalty review for round %s", round_id
                )
            continue

        # ------------------------------------------------------------------
        # Mid-submission orphan
        # ------------------------------------------------------------------
        # 1. Delete session_results so the round is not mistaken for complete
        #    by get_next_pending_phase (driver_session_results cascades).
        # 2. Delete the channel row and the Discord channel.
        # 3. Re-trigger the submission wizard immediately (production path).
        async with get_connection(bot.db_path) as db:  # type: ignore[attr-defined]
            await db.execute(
                "DELETE FROM session_results WHERE round_id = ?",
                (round_id,),
            )
            await db.execute(
                "DELETE FROM round_submission_channels WHERE round_id = ?",
                (round_id,),
            )
            await db.commit()

        log.info(
            "Recovery: cleared orphaned session_results and submission row for round %s",
            round_id,
        )

        if guild is None:
            log.warning(
                "Recovery: guild %s not in cache for round %s — "
                "submission row cleared but wizard not re-triggered",
                server_id, round_id,
            )
            continue

        channel = guild.get_channel(channel_id)
        if channel is not None:
            try:
                await channel.delete(reason="Orphaned submission channel cleanup on restart")
            except (discord.NotFound, discord.HTTPException):
                pass

        # Re-trigger the wizard so the round is not silently abandoned in
        # production (where /test-mode advance is not used).
        from services.result_submission_service import run_result_submission_job

        log.info(
            "Recovery: re-triggering result submission wizard for round %s", round_id
        )
        asyncio.create_task(run_result_submission_job(round_id, bot))

        # Notify the log channel so the LM knows to re-submit any sessions
        # that were in progress when the bot was terminated.
        try:
            await bot.output_router.post_log(  # type: ignore[attr-defined]
                server_id,
                f"⚠️ **Bot restarted mid-result-submission** for round {round_id}. "
                "Any sessions submitted before the restart have been cleared and the "
                "submission wizard has been re-opened. Please re-submit all sessions.",
            )
        except Exception:
            log.exception(
                "Recovery: failed to post log for mid-submission orphan round %s", round_id
            )


async def _recover_orphaned_amend_channels(bot: commands.Bot) -> None:
    """Delete any results-amend channels left open by a previous bot process.

    The /round results amend wait_for loop dies with the process on restart.
    We detect stale rows in round_amend_channels, notify the log channel so
    the league manager knows to re-run the command, then delete the Discord
    channel and remove the DB row.
    """
    from db.database import get_connection

    async with get_connection(bot.db_path) as db:  # type: ignore[attr-defined]
        cursor = await db.execute(
            "SELECT id, round_id, server_id, channel_id, session_type FROM round_amend_channels"
        )
        orphans = await cursor.fetchall()

    for row in orphans:
        row_id: int = row["id"]
        round_id: int = row["round_id"]
        server_id: int = row["server_id"]
        channel_id: int = row["channel_id"]
        session_type: str = row["session_type"]

        # Remove the DB row first so a further crash doesn't re-process it.
        async with get_connection(bot.db_path) as db:  # type: ignore[attr-defined]
            await db.execute(
                "DELETE FROM round_amend_channels WHERE id = ?", (row_id,)
            )
            await db.commit()

        # Delete the Discord channel.
        guild = bot.get_guild(server_id)  # type: ignore[attr-defined]
        if guild is not None:
            channel = guild.get_channel(channel_id)
            if channel is not None:
                try:
                    await channel.delete(reason="Orphaned amendment channel cleanup on restart")
                except (discord.NotFound, discord.HTTPException):
                    pass

        log.info(
            "Recovery: deleted orphaned amend channel %s for round %s session %s",
            channel_id, round_id, session_type,
        )

        # Notify the log channel so the LM knows to re-run the command.
        try:
            await bot.output_router.post_log(  # type: ignore[attr-defined]
                server_id,
                f"⚠️ **Bot restarted mid-amendment** for round {round_id} "
                f"({session_type.replace('_', ' ').title()}). "
                "The amendment channel has been deleted. "
                "Please re-run `/round results amend` to re-submit.",
            )
        except Exception:
            log.exception(
                "Recovery: failed to post log for orphaned amend channel round %s session %s",
                round_id, session_type,
            )


async def _recover_pending_setups(bot: commands.Bot) -> None:
    """Restore in-memory pending season configs from DB SETUP seasons."""
    from cogs.season_cog import SeasonCog
    season_cog: SeasonCog | None = bot.get_cog("SeasonCog")  # type: ignore[assignment]
    if season_cog is not None:
        await season_cog.recover_pending_setups()


if __name__ == "__main__":
    asyncio.run(main())
