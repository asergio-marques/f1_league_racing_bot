"""The attendance sheet ceiling counts reserves, and is meant to — issue #140.

Three capacity guards sit together in `PlacementService`, and after #140 two of them skip a
population the third counts. That asymmetry is deliberate and this file exists to keep it:

* `_guard_reserve_capacity` measures **reserve drivers only** — an ordinary placement joins no
  reserve block;
* `_guard_standings_capacity` measures **classified drivers only** — a reserve holds no row in
  a classification, ever;
* `_guard_sheet_capacity`, here, measures **every driver of the division** — because the sheet
  draws a reserve the moment one is allocated to a round (`attendance_service` selects on
  `ti.is_reserve = 1 AND dra.assigned_team_id IS NOT NULL`). In the worst case every reserve on
  the books is allocated and takes a row, so counting them all is the right ceiling.

Without these tests the sheet guard reads like the bug the other two had, and the next reader
to tidy it into line would silently lower a ceiling a league relies on.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

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
    """A division of *drivers* drivers on ordinary teams and *reserves* in the reserve team."""
    from db.database import get_connection, run_migrations

    db_path = str(tmp_path / "sheet_capacity.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 10, 20, 30)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-01-01', 'ACTIVE', 5)",
            (SERVER_ID,),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) "
            "VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid

        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, ?, 0, 1)",
            (division_id, RESERVE),
        )
        reserve_instance = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, ?, 2, 0)",
            (division_id, ORDINARY),
        )
        ordinary_instance = cursor.lastrowid

        user_id = 1000

        async def seat(instance_id: int, seat_number: int) -> None:
            nonlocal user_id
            cursor = await db.execute(
                "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (SERVER_ID, str(user_id)),
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
        await db.commit()

    return db_path, season_id, division_id


def _service(db_path, template, *, toggle: bool = True):
    from services.placement_service import PlacementService

    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    bot.image_config_service.get_toggles = AsyncMock(return_value={"attendance": toggle})
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={
            SHEET: MagicMock(
                valid=True, resolved_path=str(template), template_key=SHEET
            )
        }
    )
    return PlacementService(db_path, bot=bot)


# ── The decision this file exists to keep ─────────────────────────────────


async def test_a_seated_reserve_does_count_against_the_sheet_rows(tmp_path):
    """Deliberate, and unlike the standings guard: an allocated reserve takes a row."""
    db_path, _season_id, division_id = await _seed(tmp_path, drivers=1, reserves=1)
    template = _template_file(tmp_path, rows=2)
    service = _service(db_path, template)

    with pytest.raises(ValueError) as excinfo:
        await service._guard_sheet_capacity(SERVER_ID, division_id)

    assert "3" in str(excinfo.value), "both the driver and the reserve are counted"


async def test_a_reserve_placement_is_measured_against_the_sheet_rows(tmp_path):
    """The team being filled is immaterial here — a reserve may yet want a row."""
    db_path, _season_id, division_id = await _seed(tmp_path, drivers=2)
    template = _template_file(tmp_path, rows=2)
    service = _service(db_path, template)

    with pytest.raises(ValueError) as excinfo:
        await service._guard_sheet_capacity(SERVER_ID, division_id)

    assert "**not** assigned" in str(excinfo.value)


async def test_the_guard_takes_no_team_and_is_not_to_be_given_one(tmp_path):
    """Its signature is the decision: #140 gave the other two a team and left this one."""
    import inspect

    from services.placement_service import PlacementService

    parameters = inspect.signature(PlacementService._guard_sheet_capacity).parameters
    assert "team_name" not in parameters, (
        "the sheet counts every driver of the division whatever team is being filled — "
        "see the docstring before adding one"
    )


# ── The ordinary behaviour beside it ──────────────────────────────────────


async def test_an_assignment_within_the_rows_is_allowed(tmp_path):
    db_path, _season_id, division_id = await _seed(tmp_path, drivers=1, reserves=1)
    template = _template_file(tmp_path, rows=8)
    service = _service(db_path, template)

    await service._guard_sheet_capacity(SERVER_ID, division_id)


async def test_the_attendance_aspect_being_off_lets_every_assignment_through(tmp_path):
    db_path, _season_id, division_id = await _seed(tmp_path, drivers=2)
    template = _template_file(tmp_path, rows=2)
    service = _service(db_path, template, toggle=False)

    await service._guard_sheet_capacity(SERVER_ID, division_id)


async def test_the_guard_never_blocks_a_placement_for_its_own_reasons(tmp_path):
    """A fault in the check must not cost a league a placement (XIV.7)."""
    db_path, _season_id, division_id = await _seed(tmp_path, drivers=2)
    template = _template_file(tmp_path, rows=2)
    service = _service(db_path, template)
    service._bot.image_validity_service.template_reports = AsyncMock(
        side_effect=RuntimeError("boom")
    )

    await service._guard_sheet_capacity(SERVER_ID, division_id)


async def test_no_bot_means_no_guard(tmp_path):
    from services.placement_service import PlacementService

    db_path, _season_id, division_id = await _seed(tmp_path, drivers=2)
    await PlacementService(db_path)._guard_sheet_capacity(SERVER_ID, division_id)
