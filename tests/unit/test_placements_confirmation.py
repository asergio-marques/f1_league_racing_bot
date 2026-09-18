"""Confirming placements, as issue #220 specifies it.

`/season placements-review` runs only while the season is in Placements. Its button — and the
confirmation behind it — is withheld while any signup is unsettled or any division lacks its
lineup or calendar channel. Confirming commits every placement made.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.season import SeasonStage  # noqa: E402
from services.season_service import SeasonService  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 22070
SEASON_ID = 7
USER_ID = 42


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "confirmation.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number, stage) "
            "VALUES (?, ?, '2026-09-17', 'SETUP', 1, 'PLACEMENTS')",
            (SEASON_ID, SERVER_ID),
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


def _cog(db_path) -> SeasonCog:
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    return cog


async def _driver(db_path, uid: str, state: str, name: str | None = None):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, ?)",
            (uid, state),
        )
        if name:
            await db.execute(
                "INSERT INTO signup_records (season_id, discord_user_id, "
                "server_display_name) VALUES (?, ?, ?)",
                (SEASON_ID, uid, name),
            )
        await db.commit()


# ── The faults ──────────────────────────────────────────────────────────────────────


async def test_every_unsettled_signup_is_named(db_path):
    await _driver(db_path, "1", "UNASSIGNED", "Alice")
    await _driver(db_path, "2", "PENDING_ADMIN_APPROVAL", "Bob")
    await _driver(db_path, "3", "PENDING_DRIVER_CORRECTION")
    await _driver(db_path, "4", "ASSIGNED", "Placed")
    await _driver(db_path, "5", "NOT_SIGNED_UP", "Left")

    unsettled, _ = await _cog(db_path)._placement_confirmation_faults(SERVER_ID, SEASON_ID)

    joined = "\n".join(unsettled)
    assert len(unsettled) == 3
    assert "**Alice** — not yet placed" in joined
    assert "**Bob** — awaiting approval" in joined
    assert "**3** — correcting their signup" in joined


async def test_a_division_missing_its_lineup_or_calendar_channel_is_named(db_path):
    _, channels = await _cog(db_path)._placement_confirmation_faults(SERVER_ID, SEASON_ID)

    assert channels == ["**Am** has no lineup channel"]


async def test_a_settled_season_with_its_channels_has_no_faults(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET lineup_channel_id = 200 WHERE id = 2")
        await db.commit()
    await _driver(db_path, "4", "ASSIGNED")

    assert await _cog(db_path)._placement_confirmation_faults(SERVER_ID, SEASON_ID) == ([], [])


# ── The review and the confirmation refuse ─────────────────────────────────────────


@pytest.mark.parametrize("stage", [SeasonStage.CONFIGURATION, SeasonStage.SIGNUPS])
async def test_the_review_is_refused_outside_placements(db_path, stage):
    cog = _cog(db_path)
    cog._pending = {USER_ID: SimpleNamespace(server_id=SERVER_ID, season_id=SEASON_ID)}
    cog.bot.season_service.get_stage = AsyncMock(return_value=stage)
    cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()

    await undecorate(SeasonCog.season_review)(cog, interaction)

    assert "only be reviewed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )
    interaction.response.defer.assert_not_awaited()


async def test_the_confirmation_refuses_an_unsettled_signup_and_commits_nothing(db_path):
    await _driver(db_path, "1", "UNASSIGNED", "Alice")
    cog = _cog(db_path)
    pending = SimpleNamespace(server_id=SERVER_ID, season_id=SEASON_ID, season_number=1)
    cog._pending = {USER_ID: pending}
    cog.bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    cog.bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(status="SETUP")]
    )
    cog.bot.season_service.transition_to_active = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await SeasonCog._do_approve(cog, interaction)

    assert "Alice" in interaction.followup.send.await_args.args[0]
    cog.bot.season_service.transition_to_active.assert_not_awaited()


@pytest.mark.parametrize("divisions", [[], [SimpleNamespace(status="CANCELLED")]])
async def test_the_confirmation_refuses_a_season_with_no_division(db_path, divisions):
    """A season with nothing to race would never reach Pending completion (issue #220)."""
    cog = _cog(db_path)
    cog._pending = {USER_ID: SimpleNamespace(server_id=SERVER_ID, season_id=SEASON_ID)}
    cog.bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    cog.bot.season_service.get_divisions = AsyncMock(return_value=divisions)
    cog.bot.season_service.transition_to_active = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await SeasonCog._do_approve(cog, interaction)

    reply = interaction.followup.send.await_args.args[0]
    assert "no divisions" in reply and "Nothing has been approved" in reply
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_confirmation_refuses_a_season_no_longer_in_placements(db_path):
    cog = _cog(db_path)
    cog._pending = {USER_ID: SimpleNamespace(server_id=SERVER_ID, season_id=SEASON_ID)}
    cog.bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.ONGOING)
    cog.bot.season_service.transition_to_active = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await SeasonCog._do_approve(cog, interaction)

    assert "no longer in placements" in interaction.followup.send.await_args.args[0]
    cog.bot.season_service.transition_to_active.assert_not_awaited()


# ── Committing ─────────────────────────────────────────────────────────────────────


async def test_confirming_commits_every_placement_of_the_season(db_path):
    async with get_connection(db_path) as db:
        for profile_id in (1, 2):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile_id, str(profile_id)),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, committed) VALUES (?, ?, 1, 0)",
                (profile_id, SEASON_ID),
            )
        await db.commit()

    assert await SeasonService(db_path).commit_placements(SEASON_ID) == 2

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT committed FROM driver_season_assignments")
        assert [row["committed"] for row in await cursor.fetchall()] == [1, 1]
