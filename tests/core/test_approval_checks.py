"""The checks a season's approval makes, read by a service rather than the cog (#439, slice 4a).

The approval is judged twice: at the press, by the cog, and again when it comes up to run on the
change queue, by the change type, which may import no cog. So the two reads both make, the
divisions' channels and the unsettled signups, live in `core/services/approval_checks.py`, and the
cog's methods call them. The cog's own tests (`test_placements_confirmation.py`) still pin every
case through the cog; these pin that the service gives the same answers on its own.

The service is imported inside each test, so this file collects while it is unbuilt.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 22071
SEASON_ID = 7

_NOT_BUILT = "#439: the approval's checks are not yet read by a service of their own"


@pytest.fixture
async def db_path(tmp_path):
    """Season id 7 in Placements: Pro with its lineup channel 100 and calendar channel 101, Am
    with no lineup channel, and Gone, cancelled, with neither."""
    path = str(tmp_path / "approval_checks.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-11-01', 'SETUP', 3, 'PLACEMENTS')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, "
            "lineup_channel_id, calendar_channel_id, status) VALUES "
            "(1, ?, 'Pro', 1, 1, 100, 101, 'SETUP'), "
            "(2, ?, 'Am', 1, 2, NULL, 201, 'SETUP'), "
            "(3, ?, 'Gone', 1, 3, NULL, NULL, 'CANCELLED')",
            (SEASON_ID, SEASON_ID, SEASON_ID),
        )
        await db.commit()
    return path


def _modules(*, weather=False, results=False, attendance=False):
    modules = MagicMock()
    modules.is_weather_enabled = AsyncMock(return_value=weather)
    modules.is_results_enabled = AsyncMock(return_value=results)
    modules.is_attendance_enabled = AsyncMock(return_value=attendance)
    return modules


def _guild(*, gone: tuple[int, ...] = ()):
    """A server holding every channel but those *gone*, which a fetch reports not found."""

    async def _fetch(channel_id):
        raise discord.NotFound(MagicMock(status=404), "Unknown Channel")

    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=lambda cid: None if cid in gone else MagicMock())
    guild.fetch_channel = AsyncMock(side_effect=_fetch)
    return guild


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
async def test_the_channel_faults_are_read_by_the_service(db_path):
    """With every module off, Am has no lineup channel and Pro's lineup channel 100 has been
    deleted from the server. The service names both, with the command that sets each, and asks
    nothing of the cancelled division."""
    from leaguebot.core.services.approval_checks import division_channel_faults

    faults = await division_channel_faults(_modules(), db_path, SEASON_ID, _guild(gone=(100,)))

    assert faults == [
        "**Pro**'s lineup channel is no longer on the server — `/division lineup-channel`.",
        "**Am** has no lineup channel — `/division lineup-channel`.",
    ]


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
async def test_the_unsettled_signups_are_read_by_the_service(db_path):
    """Alice is not yet placed, Bob awaits approval, driver 3 is correcting their signup, and
    two others are placed or signed out. The service names the three unsettled, as the review
    and the confirmation name them."""
    from leaguebot.core.services.approval_checks import unsettled_signups

    async with get_connection(db_path) as db:
        for user_id, state, name in (
            ("1", "UNASSIGNED", "Alice"),
            ("2", "PENDING_ADMIN_APPROVAL", "Bob"),
            ("3", "PENDING_DRIVER_CORRECTION", None),
            ("4", "ASSIGNED", "Placed"),
            ("5", "NOT_SIGNED_UP", "Left"),
        ):
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, ?)",
                (user_id, state),
            )
            if name:
                await db.execute(
                    "INSERT INTO signup_records (season_id, discord_user_id, "
                    "server_display_name) VALUES (?, ?, ?)",
                    (SEASON_ID, user_id, name),
                )
        await db.commit()

    unsettled = await unsettled_signups(db_path)

    assert sorted(unsettled) == sorted([
        "**Alice** — not yet placed",
        "**Bob** — awaiting approval",
        "**3** — correcting their signup",
    ])
