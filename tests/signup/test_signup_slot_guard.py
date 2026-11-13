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

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.signup.cogs.signup_cog import SignupCog
from leaguebot.core.db.database import get_connection
from tests.support.change_queue import run_queue
from tests.support.settings_league import audit_rows, interaction, last_said, settings_league
from tests.support.undecorate import undecorate

SERVER_ID = 5512


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed(
    tmp_path, *, unassigned: int, slots=((1, "19:00"), (3, "20:00")), stage: str | None = None
):
    """The settings league with signup alone on (`settings_league`), signups closed,
    `unassigned` drivers waiting, and `slots` set.

    *stage* seeds an active season in that stage; left unset, the server holds none.
    """
    league = await settings_league(tmp_path, modules=("signup",))
    async with get_connection(league.db_path) as db:
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
            from leaguebot.core.models.season import SeasonStage, status_of_stage

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
    return league


def _cog(db_path):
    from leaguebot.core.services.drivers.placement_service import PlacementService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_signup_enabled = AsyncMock(return_value=True)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.placement_service = PlacementService(db_path)
    bot.output_router.post_log = AsyncMock()

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _interaction():
    """Nothing has answered it yet, so a refusal goes out as its response."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    return interaction


def _day_choice(day: int):
    choice = MagicMock()
    choice.value = str(day)
    choice.name = "Friday"
    return choice


def _reply(interaction) -> str:
    return interaction.response.send_message.await_args.args[0]


async def _command(league, method: str, name: str, *args):
    """Run `/signup <name>` (`SignupCog.<method>`) as the member, then the queue; return the
    member's interaction."""
    asked = interaction(league.bot, f"signup {name}")
    await undecorate(getattr(SignupCog, method))(SignupCog(league.bot), asked, *args)
    await run_queue(league.bot)
    return asked


async def _add(league, day=5, time="21:00"):
    return await _command(league, "time_slot_add", "time-slot add", _day_choice(day), time)


async def _remove(league, slot_id=1):
    return await _command(league, "time_slot_remove", "time-slot remove", slot_id)


async def _slot_labels(db_path):
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    return [s.display_label for s in await SignupModuleService(db_path).get_slots()]


# ---------------------------------------------------------------------------
# Fixed once the configuration is confirmed
# ---------------------------------------------------------------------------


_FIXED_STAGES = ["WAITING", "SIGNUPS", "PLACEMENTS", "ONGOING", "ONGOING_SIGNUPS",
                 "PENDING_COMPLETION"]


class TestRefusedOnceConfigurationIsConfirmed:
    @pytest.mark.parametrize("stage", _FIXED_STAGES)
    async def test_slot_add_is_refused(self, tmp_path, stage):
        league = await _seed(tmp_path, unassigned=0, stage=stage)
        before = await _slot_labels(league.db_path)

        asked = await _add(league)

        assert "fixed for Season 3" in last_said(asked)
        assert await _slot_labels(league.db_path) == before, "no slot may have been added"

    async def test_slot_remove_is_refused(self, tmp_path):
        league = await _seed(tmp_path, unassigned=0, stage="PLACEMENTS")
        before = await _slot_labels(league.db_path)

        asked = await _remove(league)

        assert "/signup time-slot remove" in last_said(asked)
        assert await _slot_labels(league.db_path) == before, "no slot may have been removed"

    @pytest.mark.parametrize(
        "command, name",
        [
            ("nationality", "nationality"),
            ("time_type", "time-type"),
            ("time_image", "time-image"),
        ],
    )
    async def test_every_setting_is_refused(self, tmp_path, command, name):
        league = await _seed(tmp_path, unassigned=0, stage="ONGOING")

        asked = await _command(league, command, name)

        assert "fixed for Season 3" in last_said(asked)
        assert await audit_rows(league.db_path) == [], "no setting may have been changed"

    async def test_the_channel_is_refused(self, tmp_path):
        """The league's two roles are fixed by the same rule, under `/bot` (issue #276);
        `test_league_role_commands.py` pins them."""
        league = await _seed(tmp_path, unassigned=0, stage="WAITING")

        asked = await _command(league, "signup_channel", "channel", MagicMock())

        assert "fixed for Season 3" in last_said(asked)


# ---------------------------------------------------------------------------
# Free with no season, and in configuration
# ---------------------------------------------------------------------------


class TestPermittedWhileFree:
    async def test_slot_change_permitted_with_no_season(self, tmp_path):
        league = await _seed(tmp_path, unassigned=2)

        await _add(league)
        assert "Friday 21:00 UTC" in await _slot_labels(league.db_path)

        await _remove(league, slot_id=1)
        assert "Monday 19:00 UTC" not in await _slot_labels(league.db_path)

    @pytest.mark.parametrize(
        "stage", ["CONFIGURATION", "COMPLETED"], ids=["configuration", "archived"]
    )
    async def test_slot_change_permitted_while_nothing_is_fixed(self, tmp_path, stage):
        """A season still in Configuration has fixed nothing yet, and an archived season holds
        nothing fixed."""
        league = await _seed(tmp_path, unassigned=0, stage=stage)

        await _add(league)

        assert "Friday 21:00 UTC" in await _slot_labels(league.db_path)


# ---------------------------------------------------------------------------
# The refusal is recorded (#442)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("record", [True, False], ids=["recorded", "not-recorded"])
async def test_the_fixed_configuration_check_logs_its_refusal_unless_record_is_false(
    tmp_path, record
):
    """The check answers the manager either way. With `record` left at its default it writes
    one line in the standard refusal form, naming the command; with `record=False` it writes
    none."""
    cog = _cog((await _seed(tmp_path, unassigned=0, stage="WAITING")).db_path)
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = "signup time-slot add"

    refused = await cog._refuse_while_configuration_fixed(
        interaction, "/signup time-slot add", record=record
    )

    assert refused is True
    assert "fixed for Season 3" in _reply(interaction)
    lines = [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]
    if record:
        [line] = lines
        assert line.startswith("⛔ ")
        assert "/signup time-slot add" in line
        assert "refused for Manager (<@42>)" in line
        assert "fixed for Season 3" in line
    else:
        assert lines == []
