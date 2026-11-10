"""A test roster is held to the template capacities a real placement is — issue #150.

Test mode exists so a league can rehearse a season and learn what would go wrong before it
matters. It seats its fake drivers without passing through ``guard_assign``, where the three
template capacity guards live — the lineup's reserve block, the attendance sheet and the
driver standings — so a roster those templates could not hold was accepted, and the overflow
surfaced only as a fallback at the first posting. A rehearsal that passes a configuration a
real season would refuse is worse than no rehearsal.

``PlacementService.guard_roster_capacity`` asks the three guards over a whole change at once,
summed per division, and both roster paths call it before they write anything. What it counts
and asks is ``tests/core/test_placement_capacity_guards.py``'s, through a stub; here the image
module's own implementation of core's protocol is handed in (#283), so that each roster path
is refused as a league meets it.

Nothing here rasterises: the templates are built in memory and read as SVG.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

from leaguebot.core.db.database import get_connection, run_migrations

#: What is not yet true of the test roster's writers.
ON_A_CONNECTION = "#283: the test roster's writers do not yet write on a handed connection"

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


def _drawing(db_path: str, templates: dict[str, str]):
    """The image module's implementation of core's protocol, every image aspect on and
    *templates* the only ones configured."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=True)
    config = MagicMock()
    config.get_toggles = AsyncMock(
        return_value={"lineup": True, "attendance": True, "standings": True}
    )
    validity = MagicMock()
    validity.template_reports = AsyncMock(
        return_value={
            key: MagicMock(valid=True, resolved_path=path, template_key=key)
            for key, path in templates.items()
        }
    )
    return ImageDrawing(
        modules=modules, config=config, validity=validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path=db_path,
    )


def _service(db_path: str, templates: dict[str, str]):
    from leaguebot.core.services.placement_service import PlacementService

    return PlacementService(db_path, drawing=_drawing(db_path, templates))


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
                    "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (divisions[name], team, team, seats, reserve),
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


# ── /test-mode roster add ─────────────────────────────────────────────────


async def _add(path, service, team, division="Alpha", name="Mock"):
    """Seat *name* at *team* in *division* through the roster's writer, held to *service*'s
    capacities, on a connection of the test's own, committed as a change's save commits it."""
    from leaguebot.core.services.test_roster_service import add_test_driver_on

    async with get_connection(path) as db:
        result = await add_test_driver_on(
            db,
            driver_name=name,
            team_name=team,
            division_name=division,
            placement_service=service,
        )
        await db.commit()
    return result


@pytest.mark.xfail(strict=True, reason=ON_A_CONNECTION)
async def test_a_reserve_past_the_lineup_s_reserve_slots_is_refused(tmp_path):
    path, _divisions = await _seed(tmp_path)
    service = _service(path, {LINEUP: _lineup(tmp_path, reserve_slots=1)})

    assert isinstance(await _add(path, service, RESERVE, name="First"), dict)
    result = await _add(path, service, RESERVE, name="Second")

    assert isinstance(result, str)
    assert "2 reserve drivers" in result
    assert "**not** assigned" in result
    assert await _seated(path) == 1, "the refused driver was written anyway"


@pytest.mark.xfail(strict=True, reason=ON_A_CONNECTION)
async def test_a_driver_past_the_sheet_rows_is_refused(tmp_path):
    path, _divisions = await _seed(tmp_path)
    service = _service(path, {SHEET: _sheet(tmp_path, rows=2)})

    assert isinstance(await _add(path, service, TEAMS[0], name="First"), dict)
    assert isinstance(await _add(path, service, RESERVE, name="Second"), dict)
    result = await _add(path, service, TEAMS[1], name="Third")

    assert isinstance(result, str) and "**not** assigned" in result
    assert await _seated(path) == 2


@pytest.mark.xfail(strict=True, reason=ON_A_CONNECTION)
async def test_a_driver_past_the_standings_rows_is_refused_but_a_reserve_is_not(tmp_path):
    path, _divisions = await _seed(tmp_path)
    service = _service(path, {DRIVERS: _standings(tmp_path, rows=2)})

    assert isinstance(await _add(path, service, TEAMS[0], name="First"), dict)
    assert isinstance(await _add(path, service, TEAMS[1], name="Second"), dict)
    refused = await _add(path, service, TEAMS[1], name="Third")
    reserve = await _add(path, service, RESERVE, name="Fourth")

    assert isinstance(refused, str) and "3 drivers" in refused
    assert isinstance(reserve, dict), "a reserve adds no entry to the classification"


@pytest.mark.xfail(strict=True, reason=ON_A_CONNECTION)
async def test_the_command_refuses_it_in_placements(tmp_path):
    """Through the cog, in the one stage the roster may change in (issue #220).

    Nothing about the stage is stubbed: the season is in Placements, the roster change is
    allowed there, and it is the template that refuses it — with the bot's own placement
    service, handed the image module's drawing, as the builder wires it. The refusal is logged,
    as every refusal of a command that changes something is.
    """
    from types import SimpleNamespace

    from leaguebot.core.cogs.test_mode_cog import TestModeCog
    from tests.support.undecorate import undecorate

    path, _divisions = await _seed(tmp_path)
    assert isinstance(await _add(path, None, TEAMS[0], name="First"), dict)
    bot = MagicMock()
    bot.db_path = path
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(
            test_mode_active=True, test_mode_nationality_required=True
        )
    )
    bot.output_router.post_log = AsyncMock()
    bot.placement_service = _service(path, {DRIVERS: _standings(tmp_path, rows=1)})
    cog = TestModeCog.__new__(TestModeCog)
    cog.bot = bot
    interaction = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Admin"
    interaction.client = bot
    interaction.command.qualified_name = "test-mode roster add"
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()

    await undecorate(TestModeCog.roster_add)(cog, interaction, "Mock", TEAMS[0], "Alpha")

    reply = interaction.response.send_message.await_args.args[0]
    assert reply.startswith("⛔")
    assert "**not** assigned" in reply
    assert await _seated(path) == 1
    [line] = [str(c.args[0]) for c in bot.output_router.post_log.await_args_list]
    assert line.startswith("⛔ ")
    assert "/test-mode roster add" in line
    assert "refused for Admin (<@77>)" in line


# ── /test-mode roster add-bulk ────────────────────────────────────────────


def _roster(*rows: tuple[str, str], nationality: str | None = "British"):
    """ParsedDriver rows of (team, division), given IDs in the synthetic range."""
    from leaguebot.core.utils.roster_import import SYNTHETIC_ID_BASE, ParsedDriver

    return [
        ParsedDriver(
            line=index + 2,
            discord_user_id=SYNTHETIC_ID_BASE + index + 1,
            driver_name=f"Mock {index + 1}",
            team_name=team,
            division_name=division,
            nationality=nationality,
        )
        for index, (team, division) in enumerate(rows)
    ]


async def _bulk(path, service, drivers):
    """Seat *drivers* through the roster's writer, held to *service*'s capacities, on a
    connection of the test's own, committed as a change's save commits it."""
    from leaguebot.core.services.test_roster_service import add_test_drivers_in_bulk_on

    async with get_connection(path) as db:
        result = await add_test_drivers_in_bulk_on(db, drivers, placement_service=service)
        await db.commit()
    return result


@pytest.mark.xfail(strict=True, reason=ON_A_CONNECTION)
async def test_a_roster_outgrowing_the_standings_across_its_teams_seats_nobody(tmp_path):
    """No team overflows on its own. The division does, and the import is refused whole."""
    path, _divisions = await _seed(tmp_path)
    service = _service(path, {DRIVERS: _standings(tmp_path, rows=3)})
    drivers = _roster(
        (TEAMS[0], "Alpha"), (TEAMS[0], "Alpha"), (TEAMS[1], "Alpha"), (TEAMS[1], "Alpha")
    )

    seated, errors = await _bulk(path, service, drivers)

    assert seated == 0
    assert len(errors) == 1
    assert errors[0].startswith("Division 'Alpha':")
    assert "4 drivers" in errors[0]
    assert await _seated(path) == 0


@pytest.mark.xfail(strict=True, reason=ON_A_CONNECTION)
async def test_each_division_is_measured_on_its_own(tmp_path):
    """Three rows each: two divisions of three drivers fit, though six drivers would not."""
    path, _divisions = await _seed(tmp_path)
    service = _service(path, {DRIVERS: _standings(tmp_path, rows=3)})
    drivers = _roster(
        (TEAMS[0], "Alpha"), (TEAMS[0], "Alpha"), (TEAMS[1], "Alpha"),
        (TEAMS[0], "Beta"), (TEAMS[1], "Beta"), (TEAMS[1], "Beta"),
    )

    seated, errors = await _bulk(path, service, drivers)

    assert errors == []
    assert seated == 6


@pytest.mark.xfail(strict=True, reason=ON_A_CONNECTION)
async def test_the_reserves_of_a_roster_are_measured_against_the_reserve_block(tmp_path):
    path, _divisions = await _seed(tmp_path)
    service = _service(path, {LINEUP: _lineup(tmp_path, reserve_slots=1)})
    drivers = _roster((RESERVE, "Alpha"), (RESERVE, "Alpha"), (TEAMS[0], "Alpha"))

    seated, errors = await _bulk(path, service, drivers)

    assert seated == 0
    assert any("2 reserve drivers" in error for error in errors)


@pytest.mark.xfail(strict=True, reason=ON_A_CONNECTION)
async def test_a_capacity_fault_is_named_beside_every_other(tmp_path):
    """Every fault is named at once, so a manager fixes the file in one pass."""
    path, _divisions = await _seed(tmp_path)
    service = _service(path, {SHEET: _sheet(tmp_path, rows=1)})
    drivers = _roster((TEAMS[0], "Alpha"), (TEAMS[1], "Alpha"), nationality="Martian")

    seated, errors = await _bulk(path, service, drivers)

    assert seated == 0
    assert any("Martian" in error for error in errors)
    assert any(error.startswith("Division 'Alpha':") for error in errors)
