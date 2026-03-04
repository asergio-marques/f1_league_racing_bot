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
    intents.message_content = False  # not reading message content

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
    bot.config_service = ConfigService(DB_PATH)      # type: ignore[attr-defined]
    bot.season_service = SeasonService(DB_PATH)      # type: ignore[attr-defined]
    bot.amendment_service = AmendmentService(DB_PATH)  # type: ignore[attr-defined]
    bot.scheduler_service = SchedulerService(DB_PATH)  # type: ignore[attr-defined]
    bot.output_router = OutputRouter(bot)            # type: ignore[attr-defined]

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

        # Recover any missed phases from before bot restart
        await _recover_missed_phases(bot)

        # Recover any season-end jobs that were lost during a restart
        await _recover_season_end_jobs(bot)

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

    await bot.add_cog(InitCog(bot))
    await bot.add_cog(SeasonCog(bot))
    await bot.add_cog(AmendmentCog(bot))
    await bot.add_cog(TestModeCog(bot))
    await bot.add_cog(ResetCog(bot))

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
                   r.format
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE s.status = 'ACTIVE'
              AND r.format != 'MYSTERY'
            """
        )
        rows = await cursor.fetchall()

    for row in rows:
        round_id, scheduled_at_str, p1, p2, p3, fmt = row
        scheduled_at = datetime.fromisoformat(scheduled_at_str)
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

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
    """Re-register season-end APScheduler jobs lost during a process restart.

    For each server with an ACTIVE season where all non-Mystery rounds are
    complete, compute the fire time (last scheduled_at + 7 days) and either:
    - schedule the job normally if the fire time is in the future, or
    - call execute_season_end immediately if the fire time is already past.
    """
    from services.season_end_service import check_and_schedule_season_end

    server_ids = await bot.season_service.get_all_server_ids_with_active_season()  # type: ignore[attr-defined]
    for server_id in server_ids:
        log.info("Startup recovery: checking season-end status for server %s", server_id)
        await check_and_schedule_season_end(server_id, bot)


async def _recover_pending_setups(bot: commands.Bot) -> None:
    """Restore in-memory pending season configs from DB SETUP seasons."""
    from cogs.season_cog import SeasonCog
    season_cog: SeasonCog | None = bot.get_cog("SeasonCog")  # type: ignore[assignment]
    if season_cog is not None:
        await season_cog.recover_pending_setups()


if __name__ == "__main__":
    asyncio.run(main())
