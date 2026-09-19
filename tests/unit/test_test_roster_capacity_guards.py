"""A test roster is held to the template capacities a real placement is — issue #150.

Test mode exists so a league can rehearse a season and learn what would go wrong before it
matters. It seats its fake drivers without passing through ``assign_driver``, where the three
template capacity guards live — the lineup's reserve block, the attendance sheet and the
driver standings — so a roster those templates could not hold was accepted, and the overflow
surfaced only as a fallback at the first posting. A rehearsal that passes a configuration a
real season would refuse is worse than no rehearsal.

``PlacementService.guard_roster_capacity`` runs the three guards over a whole change at once,
summed per division, and both roster paths call it before they write anything.

Nothing here rasterises: the templates are built in memory and read as SVG.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402

SVG_NS = "http://www.w3.org/2000/svg"
LINEUP = "lineup_template"
SHEET = "attendance_template"
DRIVERS = "standings_drivers_template"
SERVER_ID = 15000
RESERVE = "Reserve"
DIVISIONS = ("Alpha", "Beta")
TEAMS = ("Team 1", "Team 2")


# ── Templates ─────────────────────────────────────────────────────────────


def _svg(tmp_path, name: str, root) -> str:
    path = tmp_path / f"{name}.svg"
    path.write_bytes(etree.tostring(root))
    return str(path)


def _root():
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1200")
    root.set("height", "675")
    return root


def _text(parent, field_id: str) -> None:
    etree.SubElement(parent, f"{{{SVG_NS}}}text").set("id", field_id)


def _lineup(tmp_path, *, reserve_slots: int) -> str:
    """Two team blocks of two seats, beside *reserve_slots* reserve slots."""
    root = _root()
    _text(root, "division_name")
    for block in (1, 2):
        for suffix in ("group", "name", "image"):
            _text(root, f"team_{block}_{suffix}")
        for seat in (1, 2):
            for suffix in ("name", "flag", "image"):
                _text(root, f"team_{block}_driver_{seat}_{suffix}")
    _text(root, "reserve_group")
    _text(root, "reserve_name")
    for slot in range(1, reserve_slots + 1):
        for suffix in ("name", "flag", "image"):
            _text(root, f"reserve_driver_{slot}_{suffix}")
    return _svg(tmp_path, LINEUP, root)


def _rows(tmp_path, name: str, suffixes: tuple[str, ...], *, rows: int, extra=()) -> str:
    root = _root()
    for field in extra:
        _text(root, field)
    for index in range(1, rows + 1):
        group = etree.SubElement(root, f"{{{SVG_NS}}}g")
        group.set("id", f"row_{index}_group")
        for suffix in suffixes:
            _text(group, f"row_{index}_{suffix}")
    return _svg(tmp_path, name, root)


def _sheet(tmp_path, *, rows: int) -> str:
    return _rows(
        tmp_path, SHEET, ("driver_name", "points"), rows=rows,
        extra=("division_name", "classification_label"),
    )


def _standings(tmp_path, *, rows: int) -> str:
    return _rows(
        tmp_path, DRIVERS, ("position", "driver_name", "team_name", "points"), rows=rows,
        extra=("division_name", "round_number", "result_status"),
    )


def _bot(db_path: str, templates: dict[str, str]):
    """A bot with every image aspect on and *templates* the only ones configured."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    bot.image_config_service.get_toggles = AsyncMock(
        return_value={"lineup": True, "attendance": True, "standings": True}
    )
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={
            key: MagicMock(valid=True, resolved_path=path, template_key=key)
            for key, path in templates.items()
        }
    )
    return bot


def _service(db_path: str, templates: dict[str, str]):
    from services.placement_service import PlacementService

    return PlacementService(db_path, bot=_bot(db_path, templates))


# ── The season ────────────────────────────────────────────────────────────


async def _seed(tmp_path) -> tuple[str, dict[str, int]]:
    """A test-mode season in Placements, as the roster commands require it (issue #220).

    Two divisions, each holding two race teams of two seats and a reserve team. Nobody is
    seated: the rosters under test do the seating.
    """
    path = str(tmp_path / "roster_capacity.db")
    await run_migrations(path)
    divisions: dict[str, int] = {}
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-19', 'SETUP', 1, 'PLACEMENTS')"
        )
        season_id = cursor.lastrowid
        for tier, name in enumerate(DIVISIONS, start=1):
            cursor = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
                "VALUES (?, ?, ?, 'ACTIVE', ?)",
                (season_id, name, 100 + tier, tier),
            )
            divisions[name] = cursor.lastrowid
            for team, seats, reserve in ((TEAMS[0], 2, 0), (TEAMS[1], 2, 0), (RESERVE, 0, 1)):
                cursor = await db.execute(
                    "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                    "VALUES (?, ?, ?, ?)",
                    (divisions[name], team, seats, reserve),
                )
                for seat in range(1, seats + 1):
                    await db.execute(
                        "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                        (cursor.lastrowid, seat),
                    )
        await db.commit()
    return path, divisions


async def _seated(path: str) -> int:
    async with get_connection(path) as db:
        row = await (
            await db.execute("SELECT COUNT(*) AS n FROM driver_season_assignments")
        ).fetchone()
    return row["n"]


# ── guard_roster_capacity ─────────────────────────────────────────────────


async def test_drivers_across_two_teams_are_summed_against_the_standings(tmp_path):
    """Neither team outgrows three rows on its own; the division does."""
    path, divisions = await _seed(tmp_path)
    service = _service(path, {DRIVERS: _standings(tmp_path, rows=3)})

    await service.guard_roster_capacity(divisions["Alpha"], {TEAMS[0]: 2, TEAMS[1]: 1})
    with pytest.raises(ValueError) as excinfo:
        await service.guard_roster_capacity(divisions["Alpha"], {TEAMS[0]: 2, TEAMS[1]: 2})

    assert "4 drivers" in str(excinfo.value)


async def test_reserves_count_towards_the_reserve_block_and_the_sheet(tmp_path):
    path, divisions = await _seed(tmp_path)
    service = _service(
        path,
        {LINEUP: _lineup(tmp_path, reserve_slots=2), SHEET: _sheet(tmp_path, rows=5)},
    )

    with pytest.raises(ValueError) as excinfo:
        await service.guard_roster_capacity(divisions["Alpha"], {RESERVE: 3})
    assert "3 reserve drivers" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        await service.guard_roster_capacity(divisions["Alpha"], {TEAMS[0]: 4, RESERVE: 2})
    assert "6" in str(excinfo.value), "the sheet draws the reserves beside the grid"


async def test_reserves_do_not_count_towards_the_standings(tmp_path):
    """A reserve is not an entry of a classification, so a full grid still takes one."""
    path, divisions = await _seed(tmp_path)
    service = _service(path, {DRIVERS: _standings(tmp_path, rows=2)})

    await service.guard_roster_capacity(divisions["Alpha"], {TEAMS[0]: 2, RESERVE: 5})


async def test_an_unknown_team_is_left_to_the_roster_to_report(tmp_path):
    path, divisions = await _seed(tmp_path)
    service = _service(path, {DRIVERS: _standings(tmp_path, rows=1)})

    await service.guard_roster_capacity(divisions["Alpha"], {"No Such Team": 9})


async def test_no_bot_means_no_guard(tmp_path):
    from services.placement_service import PlacementService

    path, divisions = await _seed(tmp_path)

    await PlacementService(path).guard_roster_capacity(divisions["Alpha"], {TEAMS[0]: 99})
