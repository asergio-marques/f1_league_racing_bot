"""Results' section of a season review, handed to core through the review sections hook (#283):
the points configurations attached to the season and, in the placements review, what of them
blocks approval.

Its words are those the season cog wrote until the section moved into the module. The placements
review names the attached configurations whether results is on or off, as it always has.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from tests.support import saves

_NOT_YET = "#283: results hands no review section; the season cog writes it"
SEASON_ID = 13


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "results_section.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (5600, 1, 2, 3)"
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SEASON_ID,),
        )
        await db.commit()
    return path


async def _attach_real(db_path, name: str) -> None:
    await saves.create_config(db_path, name)
    await saves.set_session_points(db_path, name, SessionType.FEATURE_RACE, 1, 25)
    await saves.attach_config(db_path, SEASON_ID, name, "SETUP")


async def _attach_phantom(db_path, name: str) -> None:
    """Seeded by hand: `attach_config` refuses to make one."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (SEASON_ID, name),
        )
        await db.commit()


async def _section(db_path, *, on: bool, review: str):
    from leaguebot.core.services.review_sections import Review
    from leaguebot.results.services.season_points_service import review_section

    modules = MagicMock()
    modules.is_results_enabled = AsyncMock(return_value=on)
    return await review_section(modules, db_path, review=Review[review], season_id=SEASON_ID,
                                division_ids=[10])


@pytest.mark.parametrize("on", [
    pytest.param(True, marks=pytest.mark.xfail(strict=True, reason=_NOT_YET)),
    pytest.param(False, marks=pytest.mark.xfail(strict=True, reason=_NOT_YET)),
])
async def test_the_placements_review_names_the_attached_configurations(db_path, on):
    """Standard attached and sound: the placements review names it, results on or off, and
    reports no fault."""
    await _attach_real(db_path, "Standard")

    section = await _section(db_path, on=on, review="PLACEMENTS")

    assert section.module == "results"
    assert section.lines == ["**Points Configs:** Standard", ""]
    assert section.modules_note is None
    assert dict(section.division_lines) == {}


@pytest.mark.xfail(strict=True, reason=_NOT_YET)
async def test_the_placements_review_says_nothing_is_attached(db_path):
    """Results on and nothing attached: the section says none is attached, and that this blocks
    approval, naming the command that attaches one."""
    section = await _section(db_path, on=True, review="PLACEMENTS")

    assert section.lines == [
        "**Points Configs:** *(none attached)*",
        "",
        "❌ **No points configuration attached** — this blocks approval.",
        "  Attach one with `/results config append`, then review again.",
        "",
    ]


@pytest.mark.xfail(strict=True, reason=_NOT_YET)
async def test_the_placements_review_names_a_configuration_attached_and_never_created(db_path):
    """Results on, Standard attached and "Sprnt" attached though never created: the section names
    "Sprnt" as attached but never created, blocking approval, with the two commands that put it
    right."""
    await _attach_real(db_path, "Standard")
    await _attach_phantom(db_path, "Sprnt")

    lines = (await _section(db_path, on=True, review="PLACEMENTS")).lines

    assert "❌ **Attached but never created** — these block approval:" in lines
    assert "  • **Sprnt**" in lines
    assert (
        "  The name above is attached to this season and no such configuration exists on the "
        "server. Build it with `/results config add`, or drop it with `/results config detach`."
    ) in lines


@pytest.mark.xfail(strict=True, reason=_NOT_YET)
async def test_the_configuration_review_names_the_attached_configurations_while_results_is_on(
    db_path,
):
    """The configuration review: with results on, the attached names and nothing else; with
    results off, no section."""
    await _attach_real(db_path, "Standard")

    section = await _section(db_path, on=True, review="CONFIGURATION")

    assert section.lines == ["**Points Configs:** Standard"]
    assert await _section(db_path, on=False, review="CONFIGURATION") is None
