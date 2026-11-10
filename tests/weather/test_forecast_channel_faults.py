"""Weather's answer for a division's forecast channel when a season's confirmation is judged,
handed to core through the division channel faults hook (#283).

Its words are those core's own check wrote until weather answered for its channel.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.channel_faults import server_holding


def _modules(on: bool) -> MagicMock:
    modules = MagicMock()
    modules.is_weather_enabled = AsyncMock(return_value=on)
    return modules


@pytest.mark.xfail(
    strict=True, reason="#283: weather does not yet answer for its forecast channel"
)
async def test_weather_names_each_division_s_missing_or_deleted_forecast_channel(tmp_path):
    """Pro's weather channel 110 is gone from the server, Am has none, and Old's 310 is there but
    Old is not among the divisions judged. Weather on names Pro's and Am's, each with
    `/weather channel`, and nothing of Old; where the server cannot say whether 110 is there, it
    is taken as there; with no server, only Am's, unset, is named. Weather off names nothing."""
    from leaguebot.weather.services.forecast_channel import division_channel_faults

    db_path = str(tmp_path / "weather.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-11-01', 'SETUP')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, "
            "forecast_channel_id) VALUES (1, 1, 'Pro', 1, 1, 110), (2, 1, 'Am', 1, 2, NULL), "
            "(3, 1, 'Old', 1, 3, 310)"
        )
        await db.commit()
    divisions = {1: "Pro", 2: "Am"}

    async def faults(on: bool, guild) -> dict[int, list[str]]:
        found = await division_channel_faults(
            _modules(on), db_path, divisions=divisions, guild=guild
        )
        return {division_id: lines for division_id, lines in found.items() if lines}

    am = {2: ["**Am** has no weather channel — `/weather channel`."]}
    assert await faults(True, server_holding(gone=(110,))) == {
        1: ["**Pro**'s weather channel is no longer on the server — `/weather channel`."],
        **am,
    }
    assert await faults(True, server_holding(unreadable=(110,))) == am
    assert await faults(True, None) == am
    assert await faults(False, server_holding(gone=(110,))) == {}
