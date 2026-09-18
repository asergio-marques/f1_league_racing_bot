"""The signup module's settings are fixed once a season's configuration is confirmed.

Issue #220. The signup module is enabled, disabled and configured only while the server holds
no active season, or while its season stands in Configuration. Confirming that configuration
fixes it until the season ends: the time slots, the questions and the roles are what the
season's signups were made under.

This replaced two narrower guards (issue #126): a slot change refused while the window was
open, and while any driver awaited placement. Both only ever arise inside a season whose
configuration is already confirmed, so the one rule covers them.

"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.signup_cog import SignupCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 5512


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed(
    tmp_path, *, unassigned: int, slots=((1, "19:00"), (3, "20:00")), stage: str | None = None
):
    """A server with signups closed, `unassigned` drivers waiting, and `slots` set.

    *stage* seeds an active season in that stage; left unset, the server holds none.
    """
    path = str(tmp_path / "slot_guard.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_module_config (id, signups_open) VALUES (?, 0)",
            (1,),
        )
        for day, time_hhmm in slots:
            await db.execute(
                "INSERT INTO signup_availability_slots (day_of_week, time_hhmm) "
                "VALUES (?, ?)",
                (day, time_hhmm),
            )
        if stage is not None:
            from models.season import SeasonStage, status_of_stage

            status = status_of_stage(SeasonStage(stage)).value
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number, stage) "
                "VALUES ('2026-09-17', ?, 3, ?)",
                (status, stage),
            )
        for i in range(1, unassigned + 1):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'UNASSIGNED')",
                (i, str(9000 + i)),
            )
        await db.commit()
    return path


def _cog(db_path):
    from services.placement_service import PlacementService
    from services.signup_module_service import SignupModuleService

    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service = SignupModuleService(db_path)
    bot.placement_service = PlacementService(db_path, bot=MagicMock())
    bot.output_router.post_log = AsyncMock()

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    return interaction


def _day_choice(day: int):
    choice = MagicMock()
    choice.value = str(day)
    choice.name = "Friday"
    return choice


def _reply(interaction) -> str:
    return interaction.response.send_message.await_args.args[0]


async def _add(cog, interaction, day=5, time="21:00"):
    await undecorate(SignupCog.time_slot_add)(cog, interaction, _day_choice(day), time)


async def _remove(cog, interaction, slot_id=1):
    await undecorate(SignupCog.time_slot_remove)(cog, interaction, slot_id)


async def _slot_labels(db_path):
    from services.signup_module_service import SignupModuleService

    return [s.display_label for s in await SignupModuleService(db_path).get_slots()]


# ---------------------------------------------------------------------------
# Fixed once the configuration is confirmed
# ---------------------------------------------------------------------------


_FIXED_STAGES = ["WAITING", "SIGNUPS", "PLACEMENTS", "ONGOING", "ONGOING_SIGNUPS",
                 "PENDING_COMPLETION"]


class TestRefusedOnceConfigurationIsConfirmed:
    @pytest.mark.parametrize("stage", _FIXED_STAGES)
    async def test_slot_add_is_refused(self, tmp_path, stage):
        db_path = await _seed(tmp_path, unassigned=0, stage=stage)
        before = await _slot_labels(db_path)
        interaction = _interaction()

        await _add(_cog(db_path), interaction)

        assert "fixed for Season 3" in _reply(interaction)
        assert await _slot_labels(db_path) == before, "no slot may have been added"

    async def test_slot_remove_is_refused(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=0, stage="PLACEMENTS")
        before = await _slot_labels(db_path)
        interaction = _interaction()

        await _remove(_cog(db_path), interaction)

        assert "/signup time-slot remove" in _reply(interaction)
        assert await _slot_labels(db_path) == before, "no slot may have been removed"

    @pytest.mark.parametrize(
        "command, args",
        [
            ("nationality", ()),
            ("time_type", ()),
            ("time_image", ()),
        ],
    )
    async def test_every_setting_is_refused(self, tmp_path, command, args):
        db_path = await _seed(tmp_path, unassigned=0, stage="ONGOING")
        cog = _cog(db_path)
        cog.bot.signup_module_service = MagicMock()
        cog.bot.signup_module_service.get_settings = AsyncMock()
        interaction = _interaction()

        await undecorate(getattr(SignupCog, command))(cog, interaction, *args)

        assert "fixed for Season 3" in _reply(interaction)
        cog.bot.signup_module_service.get_settings.assert_not_awaited()

    async def test_the_roles_and_channel_are_refused(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=0, stage="WAITING")
        cog = _cog(db_path)
        for command in ("signup_channel", "signup_base_role", "signup_complete_role"):
            interaction = _interaction()
            await undecorate(getattr(SignupCog, command))(cog, interaction, MagicMock())
            assert "fixed for Season 3" in _reply(interaction), command

    async def test_the_deprecated_roles_command_is_refused_too(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=0, stage="SIGNUPS")
        interaction = _interaction()

        await undecorate(SignupCog.config_roles)(_cog(db_path), interaction, MagicMock(), MagicMock())

        assert "fixed for Season 3" in _reply(interaction)


# ---------------------------------------------------------------------------
# Free with no season, and in configuration
# ---------------------------------------------------------------------------


class TestPermittedWhileFree:
    async def test_slot_change_permitted_with_no_season(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=2)
        cog = _cog(db_path)

        await _add(cog, _interaction())
        assert "Friday 21:00 UTC" in await _slot_labels(db_path)

        await _remove(cog, _interaction(), slot_id=1)
        assert "Monday 19:00 UTC" not in await _slot_labels(db_path)

    async def test_slot_change_permitted_in_configuration(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=0, stage="CONFIGURATION")

        await _add(_cog(db_path), _interaction())

        assert "Friday 21:00 UTC" in await _slot_labels(db_path)

    async def test_an_archived_season_holds_nothing_fixed(self, tmp_path):
        db_path = await _seed(tmp_path, unassigned=0, stage="COMPLETED")

        await _add(_cog(db_path), _interaction())

        assert "Friday 21:00 UTC" in await _slot_labels(db_path)
