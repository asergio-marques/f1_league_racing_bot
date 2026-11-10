"""The image module's answer to core's declared-capacity question: does every template whose rows
the catalogue fixes in code draw that many drivers? (XIV.12, #283)

Core counts every driver the season's division would hold and asks; the image module reads the
capacities its catalogues declare, while the images module is on, and hands back the problem
naming the smallest, and its remedy, or nothing. Every catalogue declares none today, so the
guard is armed by data alone: these tests declare one for the test's span. Here the real
implementation is handed to `PlacementService` as well, so that the refusal a league reads is
pinned word for word.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


ORDINARY = "Team 1"
RACE = "results_race_template"
DRIVERS = "standings_drivers_template"
REMEDY = "Enlarge that template, or disable the images module, before adding another driver."


@pytest.fixture()
def declare(monkeypatch):
    """Declare a fixed row capacity in code for a template, as a catalogue would."""
    from leaguebot.image.models.image_catalogues import CATALOGUES, FieldCatalogue, RowSpec

    def install(template_key: str, capacity: int) -> None:
        monkeypatch.setitem(
            CATALOGUES,
            template_key,
            FieldCatalogue(rows=RowSpec(capacity=capacity, fields=frozenset({"position"}))),
        )

    return install


def _drawing(*, images: bool = True):
    """The image module's implementation of core's protocol, over stand-ins for its own services:
    the images module *images*, no aspect toggled, and no template configured."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=images)
    config = MagicMock()
    config.get_toggles = AsyncMock(return_value={})
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value={})
    return ImageDrawing(
        modules=modules, config=config, validity=validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path="",
    )


# ── The answer ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "declared, problem",
    [
        (
            {RACE: 2},
            "This would seat 3 drivers in the division, but the **Results — race** image "
            "template provides only 2 rows",
        ),
        (
            {RACE: 5, DRIVERS: 2},
            "This would seat 3 drivers in the division, but the **Standings — drivers** image "
            "template provides only 2 rows",
        ),
    ],
    ids=["one", "the-smallest-of-two"],
)
async def test_drivers_past_the_declared_rows_are_a_problem(declare, declared, problem):
    for template_key, capacity in declared.items():
        declare(template_key, capacity)

    answer = await _drawing().declared_capacity_problem(3)

    assert (answer.problem, answer.remedy) == (problem, REMEDY)


@pytest.mark.parametrize(
    "declared, drivers",
    [({RACE: 5}, 3), ({RACE: 3}, 3), ({}, 99)],
    ids=["within", "exactly", "none-declared"],
)
async def test_drivers_the_declared_rows_hold_are_no_problem(declare, declared, drivers):
    """No catalogue declares a capacity today, so nothing is measured."""
    for template_key, capacity in declared.items():
        declare(template_key, capacity)

    assert await _drawing().declared_capacity_problem(drivers) is None


async def test_the_images_module_off_answers_nothing(declare):
    declare(RACE, 2)

    assert await _drawing(images=False).declared_capacity_problem(3) is None


# ── As a league meets it, through the choke point ─────────────────────────


async def _seed(tmp_path):
    """A season's division with three race drivers seated, `Team 1`'s two seats free, and driver
    9999 waiting to be placed."""
    from leaguebot.core.db.database import get_connection, run_migrations

    db_path = str(tmp_path / "declared_capacity.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        season_id = (
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number) "
                "VALUES ('2026-01-01', 'ACTIVE', 5)"
            )
        ).lastrowid
        division_id = (
            await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
                (season_id,),
            )
        ).lastrowid
        seated = (
            await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, 'Seated', 'Seated', 3, 0)",
                (division_id,),
            )
        ).lastrowid
        ordinary = (
            await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, ?, ?, 2, 0)",
                (division_id, ORDINARY, ORDINARY),
            )
        ).lastrowid
        for seat_number in (1, 2):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, NULL)",
                (ordinary, seat_number),
            )
        for seat_number in (1, 2, 3):
            profile = (
                await db.execute(
                    "INSERT INTO driver_profiles (discord_user_id, current_state) "
                    "VALUES (?, 'ASSIGNED')",
                    (str(1000 + seat_number),),
                )
            ).lastrowid
            seat_id = (
                await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                    "VALUES (?, ?, ?)",
                    (seated, seat_number, profile),
                )
            ).lastrowid
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                (profile, season_id, division_id, seat_id),
            )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (9999, '8888', 'UNASSIGNED')"
        )
        await db.commit()
    return db_path, season_id, division_id


async def test_a_placement_past_the_declared_rows_is_refused_through_guard_assign(
    tmp_path, declare
):
    from leaguebot.core.models.refusal import Refused
    from leaguebot.core.services.placement_service import PlacementService

    db_path, season_id, division_id = await _seed(tmp_path)
    declare(RACE, 3)

    with pytest.raises(Refused) as refused:
        await PlacementService(db_path, drawing=_drawing()).guard_assign(
            division_id, season_id, ORDINARY, 9999
        )

    assert str(refused.value) == (
        "This would seat 4 drivers in the division, but the **Results — race** image template "
        "provides only 3 rows. Enlarge that template, or disable the images module, before "
        "adding another driver."
    )
