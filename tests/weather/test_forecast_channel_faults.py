"""Weather's answer for a division's forecast channel when a season's confirmation is judged,
handed to core through the division channel faults hook (#283).

Its words are those core's own check wrote until weather answered for its channel.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from tests.support.channel_faults import answer_attachments, server_holding
from tests.support.switches import answer_switch


def _modules(on: bool) -> MagicMock:
    modules = MagicMock()
    answer_switch(modules, "weather", on)
    return modules


async def test_weather_names_each_division_s_missing_or_deleted_forecast_channel(tmp_path):
    """Pro's weather channel 110 is gone from the server, Am has none, and Old's 310 is there but
    Old is not among the divisions judged. Weather on names Pro's and Am's, each with
    `/weather channel`, and nothing of Old; where the server cannot say whether 110 is there, it
    is taken as there; with no server, only Am's, unset, is named. Weather off names nothing.

    With weather pictures on, Pro's channel 110 there but the bot lacking Attach Files in it is
    named with what it lacks; lacking only Embed Links, which a forecast does not need, it is
    not."""
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

    async def faults(on: bool, guild, *pictures: str) -> dict[int, list[str]]:
        found = await division_channel_faults(
            _modules(on), db_path, drawing=answer_attachments(MagicMock(), *pictures),
            divisions=divisions, guild=guild,
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
    assert await faults(True, server_holding(unusable={110: ("attach_files",)}), "weather") == {
        1: [
            "**Pro**'s weather channel is missing permissions the bot needs there: Attach Files "
            "— give the bot them, or `/weather channel`."
        ],
        **am,
    }
    assert await faults(True, server_holding(unusable={110: ("embed_links",)}), "weather") == am


@pytest.mark.xfail(strict=True, reason="#570: weather answers no channel for a picture switched on")
async def test_weather_answers_its_forecast_channels_with_the_forecast_counted_as_a_picture_while_on(
    tmp_path,
):
    """Pro's weather channel is 110, Am has none, and Old's 310 is not among the divisions
    handed. Weather on, the weather pictures being switched on: weather answers Pro's channel 110
    for them, with what a forecast needs and Attach Files, though the drawing does not yet say a
    forecast attaches a file, and `/weather channel` to set it again; nothing for Am or Old.
    Weather on and only other outputs switched on, or weather off, it answers nothing."""
    from leaguebot.core.models.channel_needs import ATTACH, SEND, VIEW
    from leaguebot.core.services.modules.picture_channels import PictureChannel
    from leaguebot.core.services.settings.channel_registry_service import ChannelUse
    from leaguebot.weather.services.forecast_channel import picture_channels

    db_path = str(tmp_path / "weather.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-11-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, "
            "forecast_channel_id) VALUES (1, 1, 'Pro', 1, 1, 110), (2, 1, 'Am', 1, 2, NULL), "
            "(3, 1, 'Old', 1, 3, 310)"
        )
        await db.commit()

    async def answered(on: bool, *aspects: str) -> list:
        async with get_connection(db_path) as db:
            return await picture_channels(
                _modules(on), answer_attachments(MagicMock()), db=db,
                divisions={1: "Pro", 2: "Am"}, aspects=frozenset(aspects),
            )

    assert await answered(True, "weather") == [
        PictureChannel(
            frozenset({"weather"}), 110, ChannelUse("weather", "Pro"), "/weather channel",
            (VIEW, SEND, ATTACH),
        )
    ]
    assert await answered(True, "lineup", "results") == []
    assert await answered(False, "weather") == []
