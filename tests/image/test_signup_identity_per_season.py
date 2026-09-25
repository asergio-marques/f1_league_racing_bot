"""Graphics draw a driver under the signup they gave for the season being drawn (issue #220).

Signups are kept, never overwritten, so a driver who has signed up in two seasons holds a
record for each. A graphic of one season reads the name and nationality from that season's
signup; with no season named, or none held for it, it reads the driver's latest signup.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.image_results_post import _driver_names, _nationalities  # noqa: E402
from services.image_verdict_post import _driver_nationality  # noqa: E402

SERVER_ID = 22060
USER = 5150


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "identity.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2025-01-01', 'COMPLETED', 1), (2, '2026-01-01', 'ACTIVE', 2)",
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (11, 1, 'Old Pro', 1, 1), (21, 2, 'Pro', 1, 1)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (111, 11, 1, 'NORMAL', 'Silverstone Circuit', "
            "'2025-06-01T14:00:00')"
        )
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, 'ASSIGNED')",
            (str(USER),),
        )
        for season_id, name, nationality in ((1, "Old Name", "French"), (2, "New Name", "British")):
            await db.execute(
                "INSERT INTO signup_records (season_id, discord_user_id, "
                "server_display_name, nationality) VALUES (?, ?, ?, ?)",
                (season_id, str(USER), name, nationality),
            )
        await db.commit()
    return path


def _bot(db_path):
    return SimpleNamespace(db_path=db_path)


async def test_a_graphic_of_the_earlier_season_draws_that_seasons_signup(db_path):
    bot = _bot(db_path)

    assert (await _driver_names(bot, None, [USER], division_id=11))[USER] == "Old Name"
    assert (await _nationalities(bot, [USER], division_id=11))[USER] == "French"


async def test_a_graphic_of_the_later_season_draws_its_own_signup(db_path):
    bot = _bot(db_path)

    assert (await _driver_names(bot, None, [USER], division_id=21))[USER] == "New Name"
    assert (await _nationalities(bot, [USER], division_id=21))[USER] == "British"


async def test_with_no_season_named_the_latest_signup_is_read(db_path):
    bot = _bot(db_path)

    assert (await _driver_names(bot, None, [USER]))[USER] == "New Name"


async def test_a_season_holding_no_signup_falls_back_to_the_latest(db_path):
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM signup_records WHERE season_id = 1")
        await db.commit()

    assert (await _nationalities(_bot(db_path), [USER], division_id=11))[USER] == "British"


async def test_a_verdict_reads_the_nationality_of_its_rounds_season(db_path):
    assert await _driver_nationality(db_path, USER, 111) == "French"
    assert await _driver_nationality(db_path, USER) == "British"


async def test_a_server_display_name_is_cleaned_before_it_is_drawn(db_path):
    """Every graphic's names come through here, and are cleaned by the lineup's chain (#362)."""
    from unittest.mock import MagicMock

    guild = MagicMock()
    guild.get_member = lambda _uid: SimpleNamespace(display_name="Max \U0001F3CE\uFE0F Racer")

    names = await _driver_names(_bot(db_path), guild, [USER])

    assert names[USER] == "Max Racer"

