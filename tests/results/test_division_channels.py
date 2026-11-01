"""The three results channel setters.

They share one `division_results_config` row and must not blank each other. They are separate
`ON CONFLICT` upserts naming one column each, which is the shape where a copy-paste error
silently clears the neighbouring channel.
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.services.division_channels import (
    set_division_penalty_channel,
    set_division_results_channel,
    set_division_standings_channel,
)

SERVER_ID = 9808
DIVISION_ID = 11

CHANNEL_SETTERS = [
    (set_division_results_channel, "results_channel_id"),
    (set_division_standings_channel, "standings_channel_id"),
    (set_division_penalty_channel, "penalty_channel_id"),
]


async def _make_db(tmp_path) -> str:
    """A database holding one division of a season in setup."""
    db_path = os.path.join(str(tmp_path), "seasons.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'SETUP')",
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, 1, 'Division 11', 11, 555)",
            (DIVISION_ID,),
        )
        await db.commit()
    return db_path


#: `penalty_channel_id` is a TEXT column while the other two are INTEGER, so the value
#: comes back as a string. Compared as text throughout rather than papered over with a cast:
#: the divergence is real and a test that hid it would hide the next one too.
def _as_text(value) -> str | None:
    return None if value is None else str(value)


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_each_results_channel_setter_writes_its_own_column(tmp_path, setter, column):
    db_path = await _make_db(tmp_path)

    await setter(db_path, DIVISION_ID, 700001)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {column} FROM division_results_config WHERE division_id = 11"  # noqa: S608
        )
        assert _as_text((await cursor.fetchone())[column]) == "700001"


async def test_the_three_results_channels_share_a_row_without_blanking_each_other(tmp_path):
    """Three separate upserts naming one column each — the shape where a copy-paste error
    silently clears the neighbouring channel."""
    db_path = await _make_db(tmp_path)

    await set_division_results_channel(db_path, DIVISION_ID, 700001)
    await set_division_standings_channel(db_path, DIVISION_ID, 700002)
    await set_division_penalty_channel(db_path, DIVISION_ID, 700003)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT results_channel_id, standings_channel_id, penalty_channel_id "
            "FROM division_results_config WHERE division_id = 11"
        )
        row = await cursor.fetchone()
    assert row["results_channel_id"] == 700001
    assert row["standings_channel_id"] == 700002
    assert _as_text(row["penalty_channel_id"]) == "700003"


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_a_results_channel_setter_reports_what_it_replaced(tmp_path, setter, column):
    db_path = await _make_db(tmp_path)

    assert await setter(db_path, DIVISION_ID, 700001) is None
    assert _as_text(await setter(db_path, DIVISION_ID, 700002)) == "700001"


@pytest.mark.parametrize("setter,column", CHANNEL_SETTERS)
async def test_a_results_channel_can_be_cleared(tmp_path, setter, column):
    """A league that stops posting one of the three has no other way to unset it."""
    db_path = await _make_db(tmp_path)
    await setter(db_path, DIVISION_ID, 700001)

    await setter(db_path, DIVISION_ID, None)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {column} FROM division_results_config WHERE division_id = 11"  # noqa: S608
        )
        assert (await cursor.fetchone())[column] is None


async def test_setting_a_channel_twice_does_not_make_a_second_row(tmp_path):
    db_path = await _make_db(tmp_path)

    await set_division_results_channel(db_path, DIVISION_ID, 700001)
    await set_division_results_channel(db_path, DIVISION_ID, 700002)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM division_results_config WHERE division_id = ?", (DIVISION_ID,)
        )
        assert (await cursor.fetchone())[0] == 1
