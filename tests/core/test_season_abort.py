"""`/season abort` — a season whose placements were never confirmed, left as if it never was (#220).

Available only in Configuration, Waiting, Signups and Placements. The season is deleted with every
record of it, its signups included, and takes no number; its drivers go through the driver pass,
its window is closed and test mode is switched off.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.cogs.season_cog import PendingConfig, SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.season_service import SeasonService
from tests.support.undecorate import undecorate

SERVER_ID = 22160


def _cog(stage: SeasonStage | None) -> SeasonCog:
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=None if stage is None else SimpleNamespace(id=7, stage=stage)
    )
    cog.bot.season_service.delete_season = AsyncMock()
    cog.bot.output_router.post_log = AsyncMock()
    cog._pending = {42: PendingConfig(season_id=7)}
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.parametrize(
    "stage",
    [SeasonStage.CONFIGURATION, SeasonStage.WAITING, SeasonStage.SIGNUPS, SeasonStage.PLACEMENTS],
)
async def test_a_season_is_aborted_before_its_placements_are_confirmed(stage):
    cog = _cog(stage)

    with patch(
        "leaguebot.core.services.season_end_service.end_of_season_pass", new=AsyncMock(return_value={})
    ) as the_pass:
        await undecorate(SeasonCog.season_abort)(cog, _interaction(), "CONFIRM")

    the_pass.assert_awaited_once()
    cog.bot.season_service.delete_season.assert_awaited_once_with(7)
    assert cog._pending == {}


@pytest.mark.parametrize(
    "stage", [None, SeasonStage.ONGOING, SeasonStage.ONGOING_PLACEMENTS, SeasonStage.PENDING_COMPLETION]
)
async def test_abort_is_refused_once_placements_are_confirmed(stage):
    cog = _cog(stage)
    interaction = _interaction()

    await undecorate(SeasonCog.season_abort)(cog, interaction, "CONFIRM")

    assert "only before a season's placements" in interaction.response.send_message.await_args.args[0]
    cog.bot.season_service.delete_season.assert_not_awaited()


async def test_abort_needs_the_exact_confirmation_word():
    cog = _cog(SeasonStage.CONFIGURATION)
    interaction = _interaction()

    await undecorate(SeasonCog.season_abort)(cog, interaction, "confirm")

    cog.bot.season_service.delete_season.assert_not_awaited()


async def test_deleting_the_season_takes_its_signups_windows_and_configuration(tmp_path):
    path = str(tmp_path / "abort.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (7, '2026-09-17', 'SETUP', 1, 'PLACEMENTS')"
        )
        await db.execute(
            "INSERT INTO signup_windows (season_id) VALUES (7)")
        await db.execute(
            "INSERT INTO season_signup_config (season_id, nationality_required, time_type, "
            "time_image_required) VALUES (7, 1, 'TIME_TRIAL', 1)"
        )
        await db.execute(
            "INSERT INTO signup_records (season_id, discord_user_id) VALUES (7, '1')"
        )
        await db.commit()

    await SeasonService(path).delete_season(7)

    async with get_connection(path) as db:
        for table in ("seasons", "signup_windows", "season_signup_config", "signup_records"):
            cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
            assert (await cursor.fetchone())[0] == 0, table


async def test_an_abort_goes_ahead_when_the_close_timer_cannot_be_cancelled():
    """A timer already gone is what cancelling it aims at, so its failure stops nothing."""
    cog = _cog(SeasonStage.SIGNUPS)
    cog.bot.scheduler_service.cancel_signup_close_timer = MagicMock(
        side_effect=RuntimeError("no such job")
    )

    with patch(
        "leaguebot.core.services.season_end_service.end_of_season_pass", new=AsyncMock(return_value={})
    ) as the_pass:
        await undecorate(SeasonCog.season_abort)(cog, _interaction(), "CONFIRM")

    the_pass.assert_awaited_once()
    cog.bot.season_service.delete_season.assert_awaited_once_with(7)


async def test_aborting_keeps_the_saved_test_mode_backup(tmp_path):
    """A season abandoned rather than run to its end leaves the state a maintainer goes back
    to (decided 2026-09-17); only completing, and the toggle, delete it."""
    from pathlib import Path

    from leaguebot.core.services import backup_service
    from leaguebot.core.services.season_end_service import end_of_season_pass

    db_path = str(tmp_path / "abort_backup.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id, "
            "log_channel_id, test_mode_active) VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        await db.commit()
    jobstore = Path(db_path).with_name("scheduler.db")
    jobstore.write_bytes(b"")
    backup_service.backup_path(db_path).write_bytes(b"saved")
    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service.get_config = AsyncMock(return_value=None)
    bot.scheduler_service._jobstore_path = str(jobstore)

    with patch("leaguebot.weather.services.forecast_cleanup_service.flush_pending_deletions", new=AsyncMock()):
        await end_of_season_pass(bot, None)

    assert backup_service.backup_path(db_path).read_bytes() == b"saved"
