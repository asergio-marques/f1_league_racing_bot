"""The image module's answer to core's sheet question: does the attendance sheet draw rows for that
many drivers? (#140, #283)

Core counts every driver the division would hold and asks; the image module reads the configured
sheet off the event loop and hands back the problem and its remedy, or nothing. That the count
takes in the reserves, as the sheet draws a reserve the moment one is allocated to a round, is
core's, and is `tests/core/test_placement_capacity_guards.py`'s, beside the reserve block and the
standings, which count reserves alone and race drivers alone. Here the real implementation is
handed to `PlacementService` as well, so that the refusal a league reads is pinned word for word.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree


SVG_NS = "http://www.w3.org/2000/svg"
SHEET = "attendance_template"
SERVER_ID = 1
RESERVE = "Reserve"
ORDINARY = "Team 1"


def _template_file(tmp_path, *, rows: int):
    """An attendance sheet declaring *rows* driver rows."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1200")
    root.set("height", "675")
    for field in ("division_name", "classification_label"):
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", field)
    for index in range(1, rows + 1):
        group = etree.SubElement(root, f"{{{SVG_NS}}}g")
        group.set("id", f"row_{index}_group")
        for suffix in ("driver_name", "points"):
            etree.SubElement(group, f"{{{SVG_NS}}}text").set(
                "id", f"row_{index}_{suffix}"
            )

    path = tmp_path / f"{SHEET}.svg"
    path.write_bytes(etree.tostring(root))
    return path


async def _seed(tmp_path, *, drivers: int, reserves: int = 0):
    """A division of *drivers* drivers on ordinary teams and *reserves* in the reserve team, and
    driver 9999 waiting to be placed."""
    from leaguebot.core.db.database import get_connection, run_migrations

    db_path = str(tmp_path / "sheet_capacity.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 10, 20, 30)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 5)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) "
            "VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid

        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, 0, 1)",
            (division_id, RESERVE, RESERVE),
        )
        reserve_instance = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, 2, 0)",
            (division_id, ORDINARY, ORDINARY),
        )
        ordinary_instance = cursor.lastrowid

        user_id = 1000

        async def seat(instance_id: int, seat_number: int) -> None:
            nonlocal user_id
            cursor = await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, 'ASSIGNED')",
                (str(user_id),),
            )
            user_id += 1
            profile_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?)",
                (instance_id, seat_number, profile_id),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                (profile_id, season_id, division_id, cursor.lastrowid),
            )

        for index in range(drivers):
            await seat(ordinary_instance, index + 1)
        for index in range(reserves):
            await seat(reserve_instance, index + 1)
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (9999, '8888', 'UNASSIGNED')"
        )
        await db.commit()

    return db_path, season_id, division_id


REMEDY = "Enlarge the template, or the sheet would silently drop a driver."


def _drawing(template, *, toggle: bool = True, images: bool = True):
    """The image module's implementation of core's protocol, over stand-ins for its own services:
    the images module *images*, the `attendance` aspect *toggle*, and *template* as the sheet."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=images)
    config = MagicMock()
    config.get_toggles = AsyncMock(return_value={"attendance": toggle})
    validity = MagicMock()
    validity.template_reports = AsyncMock(
        return_value={
            SHEET: MagicMock(valid=True, resolved_path=str(template), template_key=SHEET)
        }
    )
    return ImageDrawing(
        modules=modules, config=config, validity=validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path="",
    )


# ── The answer ────────────────────────────────────────────────────────────


async def test_drivers_past_the_rows_are_a_problem(tmp_path):
    template = _template_file(tmp_path, rows=2)

    answer = await _drawing(template).sheet_capacity_problem(3)

    assert (answer.problem, answer.remedy) == (
        "the division would hold 3 drivers but the configured `attendance_template` "
        "declares 2 row(s)",
        REMEDY,
    )


@pytest.mark.parametrize("rows, drivers", [(8, 3), (3, 3)], ids=["within", "exactly"])
async def test_drivers_the_rows_hold_are_no_problem(tmp_path, rows, drivers):
    template = _template_file(tmp_path, rows=rows)

    assert await _drawing(template).sheet_capacity_problem(drivers) is None


@pytest.mark.parametrize(
    "toggle, images", [(False, True), (True, False)], ids=["aspect-off", "module-off"]
)
async def test_no_sheet_drawn_means_no_problem(tmp_path, toggle, images):
    template = _template_file(tmp_path, rows=2)

    drawing = _drawing(template, toggle=toggle, images=images)

    assert await drawing.sheet_capacity_problem(3) is None


# ── As a league meets it, through the choke point ─────────────────────────


async def test_a_reserve_placement_past_the_rows_is_refused_through_assign_driver(tmp_path):
    """Deliberate, and unlike the standings: a seated reserve takes a row, and so would the
    reserve being placed."""
    from leaguebot.core.services.placement_service import PlacementService

    db_path, season_id, division_id = await _seed(tmp_path, drivers=1, reserves=1)
    template = _template_file(tmp_path, rows=2)
    guild = MagicMock()
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(return_value=None)

    with pytest.raises(ValueError) as refused:
        await PlacementService(db_path, drawing=_drawing(template)).assign_driver(
            driver_profile_id=9999,
            division_id=division_id,
            team_name=RESERVE,
            season_id=season_id,
            acting_user_id=99,
            acting_user_name="Tester",
            guild=guild,
            discord_user_id="8888",
        )

    assert str(refused.value) == (
        "the division would hold 3 drivers but the configured `attendance_template` declares "
        "2 row(s). The driver was **not** assigned. Enlarge the template, or the sheet would "
        "silently drop a driver."
    )
