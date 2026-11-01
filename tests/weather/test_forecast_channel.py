"""The forecast channel setter: the channel a division's forecast is posted in."""
from __future__ import annotations

import os

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.weather.services.forecast_channel import set_division_forecast_channel

SERVER_ID = 9808


async def test_the_forecast_channel_is_set_and_reports_what_it_replaced(tmp_path):
    """The previous value is what the audit entry records, so a manager can get back to
    the channel they replaced."""
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
            "VALUES (11, 1, 'Division 11', 11, 555)",
        )
        await db.commit()

    assert await set_division_forecast_channel(db_path, 11, 700001) is None
    assert await set_division_forecast_channel(db_path, 11, 700002) == 700001
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT forecast_channel_id FROM divisions WHERE id = 11")
        assert (await cursor.fetchone())[0] == 700002
