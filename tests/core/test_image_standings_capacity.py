"""The standings row and round ceilings at the season review — 040, US4 (FR-043, FR-045).

Both standings catalogues declare ``capacity=None``: their rows are counted from the
template file, not from a number in the catalogue, so ``declared_capacities()`` cannot see
them.

Two ceilings, caught at two moments, as Constitution XIV.12 requires:

* **A driver assignment** that would take a division past the drivers template's rows is
  refused with the change unapplied. Core counts and refuses
  (`tests/core/test_placement_capacity_guards.py`); the image module measures the template
  (`tests/image/test_standings_drivers_capacity.py`).
* **`/season placements-review`** reports what either template could not draw for the season as a
  whole: the drivers rows, the constructors rows, and each template's round columns against
  the longest calendar. It is the only moment the *constructors* ceiling can be caught at
  all, because seating a driver adds no team. That half is this file's.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree


pytestmark = pytest.mark.asyncio

SVG_NS = "http://www.w3.org/2000/svg"
DRIVERS = "standings_drivers_template"
CONSTRUCTORS = "standings_constructors_template"

_ROW_SUFFIXES = ("position", "driver_name", "team_name", "points")


def _template_file(tmp_path, name, *, rows: int, rounds: int = 0):
    """A standings template declaring *rows* rows, each carrying *rounds* round cells."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1200")
    root.set("height", "675")
    for field in ("division_name", "round_number", "result_status"):
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", field)
    for ordinal in range(1, rounds + 1):
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", f"round_{ordinal}_number")
    for index in range(1, rows + 1):
        group = etree.SubElement(root, f"{{{SVG_NS}}}g")
        group.set("id", f"row_{index}_group")
        for suffix in _ROW_SUFFIXES:
            etree.SubElement(group, f"{{{SVG_NS}}}text").set(
                "id", f"row_{index}_{suffix}"
            )
        for ordinal in range(1, rounds + 1):
            cell = etree.SubElement(group, f"{{{SVG_NS}}}g")
            cell.set("id", f"row_{index}_round_{ordinal}_group")
            etree.SubElement(cell, f"{{{SVG_NS}}}text").set(
                "id", f"row_{index}_round_{ordinal}_feature_race_result"
            )

    path = tmp_path / f"{name}.svg"
    path.write_bytes(etree.tostring(root))
    return path


async def _seed(
    tmp_path, *, drivers: int, teams: int = 1, rounds: int = 1, reserves: int = 0
):
    """A season holding one division of *drivers* classified drivers and *teams* real teams.

    *reserves* seats that many drivers in the reserve team besides. They are assignments of
    the division like any other, but they are **not** entries of its classification, so the
    standings ceiling must not count them.
    """
    from leaguebot.core.db.database import get_connection, run_migrations

    db_path = str(tmp_path / "capacity.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'SETUP', 5)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) "
            "VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid

        for number in range(1, rounds + 1):
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
                "VALUES (?, ?, 'STANDARD', '2026-06-01T18:00:00')",
                (division_id, number),
            )

        # One real team carrying every seat, plus a reserve that must not be counted.
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, 'Reserve', 'Reserve', 0, 1)",
            (division_id,),
        )
        reserve_instance_id = cursor.lastrowid
        for index in range(teams):
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, 2, 0)",
                (division_id, f"Team {index + 1}", f"Team {index + 1}"),
            )
        instance_id = cursor.lastrowid

        async def seat(team_instance_id: int, seat_number: int, user_id: int) -> None:
            cursor = await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, 'ACTIVE')",
                (user_id,),
            )
            profile_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?)",
                (team_instance_id, seat_number, profile_id),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                (profile_id, season_id, division_id, cursor.lastrowid),
            )

        for index in range(drivers):
            await seat(instance_id, index + 1, 1000 + index)
        for index in range(reserves):
            await seat(reserve_instance_id, index + 1, 2000 + index)
        await db.commit()

    return db_path, season_id, division_id


def _bot(db_path, reports, *, toggle=True):
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    bot.image_config_service.get_toggles = AsyncMock(return_value={"standings": toggle})
    bot.image_config_service.is_aspect_enabled = AsyncMock(return_value=toggle)
    bot.image_validity_service.template_reports = AsyncMock(return_value=reports)
    return bot


# ── At the season review (FR-043, FR-045) ─────────────────────────────────


def _cog(bot):
    from leaguebot.core.cogs.season_cog import SeasonCog

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


async def test_the_review_names_the_two_templates_apart_never_as_one_pair(tmp_path):
    """FR-045 — a manager told 'the standings template' would not know which to enlarge."""
    db_path, season_id, _division_id = await _seed(tmp_path, drivers=6, teams=4)
    reports = {
        DRIVERS: MagicMock(
            valid=True,
            resolved_path=str(_template_file(tmp_path, DRIVERS, rows=2)),
            template_key=DRIVERS,
        ),
        CONSTRUCTORS: MagicMock(
            valid=True,
            resolved_path=str(_template_file(tmp_path, CONSTRUCTORS, rows=2)),
            template_key=CONSTRUCTORS,
        ),
    }
    cog = _cog(_bot(db_path, reports))

    lines = await cog._standings_capacity_lines(season_id, reports)

    text = "\n".join(lines)
    assert "Standings — drivers" in text
    assert "Standings — constructors" in text
    assert "6" in text and "4" in text, "each ceiling reports its own count"


async def test_the_reserve_team_is_not_counted_against_the_constructors_rows(tmp_path):
    """The classification draws the real constructors; the reserve is not one of them."""
    db_path, season_id, _division_id = await _seed(tmp_path, drivers=1, teams=2)
    reports = {
        CONSTRUCTORS: MagicMock(
            valid=True,
            resolved_path=str(_template_file(tmp_path, CONSTRUCTORS, rows=2)),
            template_key=CONSTRUCTORS,
        )
    }
    cog = _cog(_bot(db_path, reports))

    assert await cog._standings_capacity_lines(season_id, reports) == []


async def test_a_calendar_longer_than_the_round_columns_is_reported(tmp_path):
    db_path, season_id, _division_id = await _seed(tmp_path, drivers=1, rounds=5)
    reports = {
        DRIVERS: MagicMock(
            valid=True,
            resolved_path=str(_template_file(tmp_path, DRIVERS, rows=5, rounds=2)),
            template_key=DRIVERS,
        )
    }
    cog = _cog(_bot(db_path, reports))

    lines = await cog._standings_capacity_lines(season_id, reports)

    assert len(lines) == 1
    assert "2 round column(s)" in lines[0]
    assert "holds 5" in lines[0]


async def test_a_template_drawing_no_grid_at_all_is_not_an_overflow(tmp_path):
    """XIV.3 — a template that draws the classification alone is a legitimate choice."""
    db_path, season_id, _division_id = await _seed(tmp_path, drivers=1, rounds=20)
    reports = {
        DRIVERS: MagicMock(
            valid=True,
            resolved_path=str(_template_file(tmp_path, DRIVERS, rows=5, rounds=0)),
            template_key=DRIVERS,
        )
    }
    cog = _cog(_bot(db_path, reports))

    assert await cog._standings_capacity_lines(season_id, reports) == []


async def test_an_invalid_template_is_left_to_the_layer_that_reports_it(tmp_path):
    db_path, season_id, _division_id = await _seed(tmp_path, drivers=99)
    reports = {DRIVERS: MagicMock(valid=False, resolved_path=None, template_key=DRIVERS)}
    cog = _cog(_bot(db_path, reports))

    assert await cog._standings_capacity_lines(season_id, reports) == []
