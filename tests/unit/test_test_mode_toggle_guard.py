"""`/test-mode toggle` answers to the season's stage and refuses a real league.

Test mode is chosen for a season, while that season is in Configuration (issue #220). Outside
Configuration the toggle is refused: confirming the configuration fixes test mode, and the
season's end switches it off. That retired the two refusals the toggle used to make — an open
signup window, and a started season holding fake drivers — since neither can arise while the
season is in Configuration.

Test mode and a real roster may still not share a server: switching test mode off deletes every
fake driver on it without confirmation, and while it is on the signup and placement paths refuse
real drivers outright.

The callback runs against a migrated database with Discord stubbed, and the guards are
unwrapped the way the other cog suites unwrap them.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs import test_mode_cog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.config_service import ConfigService  # noqa: E402
from services.signup_module_service import SignupModuleService  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 7272


# ── Stubs ─────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def is_done(self) -> bool:
        return bool(self.messages)

    async def defer(self, **kwargs):
        pass

    async def send_message(self, content, **kwargs):
        self.messages.append(content)


class _Interaction:
    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.guild = None
        self.response = _Response()
        self.followup = SimpleNamespace(send=self._followup_send)
        self.user = SimpleNamespace(display_name="Tester", id=1)

    async def _followup_send(self, content, **kwargs):
        self.response.messages.append(content)

    @property
    def reply(self) -> str:
        assert self.response.messages, "the command replied with nothing"
        return self.response.messages[-1]


def _unwrap(cmd):
    """The innermost callback, bypassing whatever tier guard the command wears."""
    return undecorate(cmd)


async def _toggle(cog) -> _Interaction:
    interaction = _Interaction()
    await _unwrap(test_mode_cog.TestModeCog.toggle)(cog, interaction)
    return interaction


async def _flag(db_path: str) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT test_mode_active FROM server_configs WHERE server_id = ?",
            (SERVER_ID,),
        )
        row = await cursor.fetchone()
    return bool(row["test_mode_active"])


async def _season(db_path: str, status: str) -> int:
    """A season of *status* with one division, one team and two seats. Returns the team id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-03-01', ?, 1)",
            (status,),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, 'Division One', 1, 'ACTIVE', 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, 'Redline', 2, 0)",
            (division_id,),
        )
        team_id = cursor.lastrowid
        for seat_number in (1, 2):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                (team_id, seat_number),
            )
        await db.commit()
    return team_id


async def _seat_a_test_driver(db_path: str, team_id: int) -> None:
    """A fake driver in the first free seat of *team_id*, as `/test-mode roster add` leaves one."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "is_test_driver, test_display_name) "
            "VALUES ('9000000000000000001', 'ASSIGNED', 1, 'Mock Alpha')"
        )
        profile_id = cursor.lastrowid
        cursor = await db.execute(
            "SELECT id FROM team_seats WHERE team_instance_id = ? "
            "AND driver_profile_id IS NULL ORDER BY seat_number LIMIT 1",
            (team_id,),
        )
        seat_id = (await cursor.fetchone())["id"]
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?", (profile_id, seat_id)
        )
        cursor = await db.execute(
            "SELECT ti.division_id, d.season_id FROM team_instances ti "
            "JOIN divisions d ON d.id = ti.division_id WHERE ti.id = ?",
            (team_id,),
        )
        row = await cursor.fetchone()
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
            "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
            (profile_id, row["season_id"], row["division_id"], seat_id),
        )
        await db.commit()


async def _in_test_mode(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs SET test_mode_active = 1 WHERE server_id = ?",
            (SERVER_ID,),
        )
        await db.commit()


async def _fake_drivers(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_profiles "
            "WHERE is_test_driver = 1",
        )
        return (await cursor.fetchone())["n"]


async def _open_signups(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE signup_module_config SET signups_open = 1",
        )
        await db.commit()


async def _add_driver(db_path: str, user_id: str, state: str, *, test: bool = False) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles "
            "(discord_user_id, current_state, is_test_driver) VALUES (?, ?, ?)",
            (user_id, state, 1 if test else 0),
        )
        await db.commit()


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "toggle_guard.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 1, 2, 3, 0)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_module_config (id, signup_channel_id, base_role_id, "
            "signed_up_role_id, signups_open) VALUES (?, 11, 12, 13, 0)",
            (1,),
        )
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-17', 'SETUP', 1, 'CONFIGURATION')"
        )
        await db.commit()
    return path


@pytest.fixture
def cog(db_path):
    return test_mode_cog.TestModeCog(
        SimpleNamespace(
            db_path=db_path,
            config_service=ConfigService(db_path),
            signup_module_service=SignupModuleService(db_path),
            output_router=SimpleNamespace(post_log=AsyncMock()),
        )
    )


# ── Enabling ──────────────────────────────────────────────────────────────


class TestEnabling:
    async def test_a_clean_server_may_enter_test_mode(self, cog, db_path):
        interaction = await _toggle(cog)

        assert "**enabled**" in interaction.reply
        assert await _flag(db_path) is True

    async def test_a_real_driver_refuses_it(self, cog, db_path):
        await _add_driver(db_path, "4001", "ASSIGNED")

        interaction = await _toggle(cog)

        assert "cannot be enabled" in interaction.reply
        assert "**1** real driver" in interaction.reply

    async def test_the_flag_is_left_alone_when_refused(self, cog, db_path):
        """A refusal must not flip the flag: the toggle flips before it branches."""
        await _add_driver(db_path, "4002", "PENDING_ADMIN_APPROVAL")

        await _toggle(cog)

        assert await _flag(db_path) is False

    async def test_a_former_driver_does_not_stand_in_the_way(self, cog, db_path):
        await _add_driver(db_path, "4003", "NOT_SIGNED_UP")

        interaction = await _toggle(cog)

        assert "**enabled**" in interaction.reply
        assert await _flag(db_path) is True

    async def test_a_fake_driver_does_not_stand_in_the_way(self, cog, db_path):
        await _add_driver(db_path, "9000000000000000001", "ASSIGNED", test=True)

        interaction = await _toggle(cog)

        assert "**enabled**" in interaction.reply

    async def test_the_count_is_named(self, cog, db_path):
        await _add_driver(db_path, "4004", "UNASSIGNED")
        await _add_driver(db_path, "4005", "ASSIGNED")

        interaction = await _toggle(cog)

        assert "**2** real driver" in interaction.reply


class TestTheSeasonsStage:
    async def test_it_is_refused_with_no_season(self, cog, db_path):
        async with get_connection(db_path) as db:
            await db.execute("DELETE FROM seasons")
            await db.commit()

        interaction = await _toggle(cog)

        assert "only be switched while a season is in configuration" in interaction.reply
        assert await _flag(db_path) is False

    @pytest.mark.parametrize(
        "stage, status",
        [("WAITING", "SETUP"), ("PLACEMENTS", "SETUP"), ("ONGOING", "ACTIVE"),
         ("PENDING_COMPLETION", "ACTIVE")],
    )
    async def test_it_is_refused_once_the_configuration_is_confirmed(
        self, cog, db_path, stage, status
    ):
        async with get_connection(db_path) as db:
            await db.execute("UPDATE seasons SET status = ?, stage = ?", (status, stage))
            await db.commit()
        await _in_test_mode(db_path)

        interaction = await _toggle(cog)

        assert "only be switched while a season is in configuration" in interaction.reply
        assert await _flag(db_path) is True, "test mode stays as the season fixed it"

    async def test_it_may_be_switched_off_again_in_configuration(self, cog, db_path):
        await _in_test_mode(db_path)

        interaction = await _toggle(cog)

        assert "**disabled**" in interaction.reply
        assert await _flag(db_path) is False


# ── The saved backup goes with test mode ──────────────────────────────────


async def test_toggling_off_deletes_the_saved_backup(cog, db_path):
    """Decided 2026-09-17: the backup commands run in test mode alone, so a state kept past
    it is one nothing could restore. The lock does not protect it."""
    from pathlib import Path

    from services import backup_service

    await _toggle(cog)  # on
    jobstore = Path(db_path).with_name("scheduler.db")
    jobstore.write_bytes(b"")
    backup_service.backup_path(db_path).write_bytes(b"saved")
    backup_service.backup_path(jobstore).write_bytes(b"saved jobs")
    backup_service.set_lock(db_path, who="Maintainer")

    interaction = await _toggle(cog)  # off

    assert not backup_service.backup_path(db_path).exists()
    assert not backup_service.backup_path(jobstore).exists()
    assert not backup_service.lock_path(db_path).exists()
    assert "Deleted the saved test-mode backup" in interaction.reply
    assert "backup: deleted" in cog.bot.output_router.post_log.await_args.args[1]


async def test_toggling_off_with_nothing_saved_says_nothing_about_a_backup(cog, db_path):
    await _toggle(cog)  # on

    interaction = await _toggle(cog)  # off

    assert "backup" not in interaction.reply.lower()
