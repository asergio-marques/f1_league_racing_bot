"""The image module's answer to core's standings question: does the driver standings template draw
rows for that many classified drivers? (FR-044, #140, #283)

Core counts the race drivers a placement would leave in the division's classification and asks;
the image module reads the configured drivers template off the event loop and hands back the
problem and its remedy, or nothing. That a reserve is no entry of a classification, so that only
race drivers are counted and a reserve placement never asks, is core's, and is
`tests/core/test_placement_capacity_guards.py`'s. Here the real implementation is handed to
`PlacementService` as well, so that the refusal a league reads is pinned word for word.

The review's half of the standings ceilings, the constructors and the round columns, is
`tests/image/test_standings_review_capacity.py`'s. Nothing here rasterises.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree


SVG_NS = "http://www.w3.org/2000/svg"
DRIVERS = "standings_drivers_template"
RESERVE = "Reserve"
ORDINARY = "Team 1"
REMEDY = "Enlarge the template, or the standings would silently drop a driver."

_ROW_SUFFIXES = ("position", "driver_name", "team_name", "points")


def _template_file(tmp_path, *, rows: int):
    """A driver standings template declaring *rows* rows."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1200")
    root.set("height", "675")
    for field in ("division_name", "round_number", "result_status"):
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", field)
    for index in range(1, rows + 1):
        group = etree.SubElement(root, f"{{{SVG_NS}}}g")
        group.set("id", f"row_{index}_group")
        for suffix in _ROW_SUFFIXES:
            etree.SubElement(group, f"{{{SVG_NS}}}text").set("id", f"row_{index}_{suffix}")

    path = tmp_path / f"{DRIVERS}.svg"
    path.write_bytes(etree.tostring(root))
    return path


def _drawing(template, *, toggle: bool = True, images: bool = True):
    """The image module's implementation of core's protocol, over stand-ins for its own services:
    the images module *images*, the `standings` aspect *toggle*, and *template* as the drivers
    template."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=images)
    config = MagicMock()
    config.get_toggles = AsyncMock(return_value={"standings": toggle})
    validity = MagicMock()
    validity.template_reports = AsyncMock(
        return_value={
            DRIVERS: MagicMock(valid=True, resolved_path=str(template), template_key=DRIVERS)
        }
    )
    return ImageDrawing(
        modules=modules, config=config, validity=validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path="",
    )


async def _seed(tmp_path, *, drivers: int, reserves: int = 0):
    """A season's division holding *drivers* race drivers in one full team and *reserves* in the
    reserve team, with `Team 1`'s two seats free and driver 9999 waiting to be placed."""
    from leaguebot.core.db.database import get_connection, run_migrations

    db_path = str(tmp_path / "standings_capacity.db")
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

        async def team(name: str, seats: int, is_reserve: int) -> int:
            return (
                await db.execute(
                    "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                    "is_reserve) VALUES (?, ?, ?, ?, ?)",
                    (division_id, name, name, seats, is_reserve),
                )
            ).lastrowid

        reserve = await team(RESERVE, 0, 1)
        seated = await team("Seated", drivers, 0)
        ordinary = await team(ORDINARY, 2, 0)
        for seat_number in (1, 2):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, NULL)",
                (ordinary, seat_number),
            )

        next_user_id = 1000

        async def seat(instance_id: int, seat_number: int) -> None:
            nonlocal next_user_id
            profile = (
                await db.execute(
                    "INSERT INTO driver_profiles (discord_user_id, current_state) "
                    "VALUES (?, 'ASSIGNED')",
                    (str(next_user_id),),
                )
            ).lastrowid
            next_user_id += 1
            seat_id = (
                await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                    "VALUES (?, ?, ?)",
                    (instance_id, seat_number, profile),
                )
            ).lastrowid
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                (profile, season_id, division_id, seat_id),
            )

        for index in range(drivers):
            await seat(seated, index + 1)
        for index in range(reserves):
            await seat(reserve, index + 1)
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (9999, '8888', 'UNASSIGNED')"
        )
        await db.commit()
    return db_path, season_id, division_id


async def _assign(db_path, season_id, division_id, team, drawing):
    """Ask the guards `/driver assign` asks whether the unplaced driver 9999 may join *team*."""
    from leaguebot.core.services.placement_service import PlacementService

    await PlacementService(db_path, drawing=drawing).guard_assign(
        division_id, season_id, team, 9999
    )


# ── The answer ────────────────────────────────────────────────────────────


async def test_drivers_past_the_rows_are_a_problem(tmp_path):
    template = _template_file(tmp_path, rows=2)

    answer = await _drawing(template).standings_capacity_problem(3)

    assert (answer.problem, answer.remedy) == (
        "the division would hold 3 drivers but the configured `standings_drivers_template` "
        "declares 2 row(s)",
        REMEDY,
    )


@pytest.mark.parametrize("rows, drivers", [(5, 3), (3, 3)], ids=["within", "exactly"])
async def test_drivers_the_rows_hold_are_no_problem(tmp_path, rows, drivers):
    template = _template_file(tmp_path, rows=rows)

    assert await _drawing(template).standings_capacity_problem(drivers) is None


@pytest.mark.parametrize(
    "toggle, images", [(False, True), (True, False)], ids=["aspect-off", "module-off"]
)
async def test_no_standings_drawn_means_no_problem(tmp_path, toggle, images):
    """The ceiling exists because a graphic would drop a driver. No graphic, no ceiling."""
    template = _template_file(tmp_path, rows=2)

    drawing = _drawing(template, toggle=toggle, images=images)

    assert await drawing.standings_capacity_problem(3) is None


# ── As a league meets it, through the choke point ─────────────────────────


async def test_a_race_placement_past_the_rows_is_refused_through_guard_assign(tmp_path):
    """The two seated reserves are not among the drivers counted: they hold no row."""
    db_path, season_id, division_id = await _seed(tmp_path, drivers=2, reserves=2)
    template = _template_file(tmp_path, rows=2)

    with pytest.raises(ValueError) as refused:
        await _assign(db_path, season_id, division_id, ORDINARY, _drawing(template))

    assert str(refused.value) == (
        "the division would hold 3 drivers but the configured `standings_drivers_template` "
        "declares 2 row(s). The driver was **not** assigned. Enlarge the template, or the "
        "standings would silently drop a driver."
    )


async def test_a_reserve_placement_survives_full_standings_through_guard_assign(tmp_path):
    """Seating a reserve grows no classification, so a full template cannot refuse it."""
    db_path, season_id, division_id = await _seed(tmp_path, drivers=2)
    template = _template_file(tmp_path, rows=2)

    await _assign(db_path, season_id, division_id, RESERVE, _drawing(template))
