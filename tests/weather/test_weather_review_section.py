"""Weather's section of a season review, handed to core through the review sections hook (#283).

Its words are those the season cog wrote until the section moved into the module.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import run_migrations


def _modules(on: bool) -> MagicMock:
    modules = MagicMock()
    modules.is_weather_enabled = AsyncMock(return_value=on)
    return modules


@pytest.mark.parametrize("review", [
    pytest.param("PLACEMENTS"),
    pytest.param("CONFIGURATION"),
])
async def test_weather_hands_its_deadlines_to_each_review(tmp_path, review):
    """Weather on, its deadlines as stored: the Weather Config section, its deadlines in the
    words `describe_deadlines` gives them; off, no section."""
    from leaguebot.core.services.review_sections import Review
    from leaguebot.weather.services.weather_config_service import (
        describe_deadlines,
        get_weather_pipeline_config,
        review_section,
    )

    db_path = str(tmp_path / "weather.db")
    await run_migrations(db_path)

    section = await review_section(_modules(True), db_path, review=Review[review], season_id=1,
                                   division_ids=[10])

    assert section.module == "weather"
    assert section.lines == [
        "**Weather Config**",
        *describe_deadlines(await get_weather_pipeline_config(db_path)),
        "",
    ]
    assert section.modules_note is None
    assert await review_section(_modules(False), db_path, review=Review[review], season_id=1,
                                division_ids=[10]) is None
