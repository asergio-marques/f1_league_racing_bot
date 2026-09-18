"""The lineup reserve block bounds *reserve* placements, and nothing else — issue #140.

`PlacementService._guard_reserve_capacity` refuses a placement that would seat more reserve
drivers than the configured lineup template draws slots for. It counts the division's reserves
and adds one — which is the right measurement only where the driver being placed is going into
the reserve team.

Until #140 the guard was never told which team was being filled, so it made that measurement on
every assignment. A division carrying as many reserves as its template declared slots could then
place nobody at all: filling an ordinary race seat was refused, citing the reserve block. Every
route in was blocked together, `assign_driver` being the single choke point through which the
signup wizard, `/driver assign` and attendance's autoreserve alike seat a driver.

Nothing here rasterises: the templates are built in memory and read as SVG.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SVG_NS = "http://www.w3.org/2000/svg"
LINEUP = "lineup_template"
SERVER_ID = 1
RESERVE = "Reserve"
ORDINARY = "Team 1"


def _template_file(tmp_path, *, reserve_slots: int, blocks: int = 2, seats: int = 2):
    """A lineup template declaring *blocks* team blocks beside *reserve_slots* reserve slots."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "800")
    root.set("height", "600")

    def node(field_id: str) -> None:
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", field_id)

    node("division_name")
    for block in range(1, blocks + 1):
        node(f"team_{block}_group")
        node(f"team_{block}_name")
        node(f"team_{block}_image")
        for seat in range(1, seats + 1):
            node(f"team_{block}_driver_{seat}_name")
            node(f"team_{block}_driver_{seat}_flag")
            node(f"team_{block}_driver_{seat}_image")

    if reserve_slots:
        node("reserve_group")
        node("reserve_name")
        for slot in range(1, reserve_slots + 1):
            node(f"reserve_driver_{slot}_name")
            node(f"reserve_driver_{slot}_flag")
            node(f"reserve_driver_{slot}_image")

    path = tmp_path / f"{LINEUP}.svg"
    path.write_bytes(etree.tostring(root))
    return path


async def _seed(tmp_path, *, reserves: int, regulars: int = 0, teams: int = 2):
    """A division carrying *reserves* reserve drivers and *regulars* on ordinary teams."""
    from db.database import get_connection, run_migrations

    db_path = str(tmp_path / "reserve_capacity.db")
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
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, ?, 0, 1)",
            (division_id, RESERVE),
        )
        reserve_instance = cursor.lastrowid
        # Each ordinary team carries its two seats, empty until a driver is seated in one —
        # `assign_driver` needs a free seat to place into, as a real division would have.
        ordinary_seats: list[int] = []
        for index in range(1, teams + 1):
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, ?, 2, 0)",
                (division_id, f"Team {index}"),
            )
            instance_id = cursor.lastrowid
            for seat_number in (1, 2):
                cursor = await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number, "
                    "driver_profile_id) VALUES (?, ?, NULL)",
                    (instance_id, seat_number),
                )
                ordinary_seats.append(cursor.lastrowid)

        next_user_id = 1000

        async def occupy(seat_id: int) -> None:
            nonlocal next_user_id
            cursor = await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, 'ASSIGNED')",
                (str(next_user_id),),
            )
            next_user_id += 1
            profile_id = cursor.lastrowid
            await db.execute(
                "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
                (profile_id, seat_id),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                (profile_id, season_id, division_id, seat_id),
            )

        for index in range(reserves):
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, "
                "driver_profile_id) VALUES (?, ?, NULL)",
                (reserve_instance, index + 1),
            )
            await occupy(cursor.lastrowid)
        for seat_id in ordinary_seats[:regulars]:
            await occupy(seat_id)

        # The driver every test then tries to place.
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (9999, '8888', 'UNASSIGNED')"
        )
        await db.commit()

    return db_path, season_id, division_id


def _bot(db_path, reports, *, toggle: bool = True):
    # `db_path` is not decoration: the lineup refresh `assign_driver` ends on reads
    # `bot.db_path`, and a MagicMock there writes a file named after itself into the
    # repo root. The suite keeps no scratch.
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    bot.image_config_service.get_toggles = AsyncMock(return_value={"lineup": toggle})
    bot.image_validity_service.template_reports = AsyncMock(return_value=reports)
    return bot


def _report(path):
    return MagicMock(valid=True, resolved_path=str(path), template_key=LINEUP)


def _service(db_path, bot):
    from services.placement_service import PlacementService

    return PlacementService(db_path, bot=bot)


# ── The defect: an ordinary team is not the reserve block ─────────────────


async def test_a_placement_into_an_ordinary_team_is_not_refused_for_reserve_capacity(
    tmp_path,
):
    """#140: a full reserve block said nothing about a free seat on a race team."""
    db_path, _season_id, division_id = await _seed(tmp_path, reserves=3)
    template = _template_file(tmp_path, reserve_slots=3)
    service = _service(db_path, _bot(db_path, {LINEUP: _report(template)}))

    await service._guard_reserve_capacity(division_id, ORDINARY)


# ── The measurement it does exist to make ─────────────────────────────────


async def test_a_reserve_placement_past_the_slots_is_refused(tmp_path):
    db_path, _season_id, division_id = await _seed(tmp_path, reserves=3)
    template = _template_file(tmp_path, reserve_slots=3)
    service = _service(db_path, _bot(db_path, {LINEUP: _report(template)}))

    with pytest.raises(ValueError) as excinfo:
        await service._guard_reserve_capacity(division_id, RESERVE)

    message = str(excinfo.value)
    assert "4 reserve drivers" in message
    assert "3 reserve slots" in message
    assert "**not** assigned" in message


async def test_a_reserve_placement_within_the_slots_is_allowed(tmp_path):
    db_path, _season_id, division_id = await _seed(tmp_path, reserves=3)
    template = _template_file(tmp_path, reserve_slots=5)
    service = _service(db_path, _bot(db_path, {LINEUP: _report(template)}))

    await service._guard_reserve_capacity(division_id, RESERVE)


async def test_drivers_on_ordinary_teams_do_not_count_towards_the_reserve_block(tmp_path):
    """The block bounds reserves alone, so a full grid never fills it."""
    db_path, _season_id, division_id = await _seed(tmp_path, reserves=1, regulars=4)
    template = _template_file(tmp_path, reserve_slots=2)
    service = _service(db_path, _bot(db_path, {LINEUP: _report(template)}))

    await service._guard_reserve_capacity(division_id, RESERVE)


# ── Everything it must not raise for ──────────────────────────────────────


async def test_an_unknown_team_is_left_to_the_check_that_reports_it(tmp_path):
    """assign_driver names a team that does not exist in its own words."""
    db_path, _season_id, division_id = await _seed(tmp_path, reserves=3)
    template = _template_file(tmp_path, reserve_slots=3)
    service = _service(db_path, _bot(db_path, {LINEUP: _report(template)}))

    await service._guard_reserve_capacity(division_id, "No Such Team")


async def test_the_lineup_aspect_being_off_lets_every_placement_through(tmp_path):
    """The ceiling exists because a graphic would drop a driver. No graphic, no ceiling."""
    db_path, _season_id, division_id = await _seed(tmp_path, reserves=3)
    template = _template_file(tmp_path, reserve_slots=3)
    service = _service(db_path, _bot(db_path, {LINEUP: _report(template)}, toggle=False))

    await service._guard_reserve_capacity(division_id, RESERVE)


async def test_the_guard_never_blocks_a_placement_for_its_own_reasons(tmp_path):
    """A fault in the check must not cost a league a placement (XIV.7)."""
    db_path, _season_id, division_id = await _seed(tmp_path, reserves=3)
    bot = _bot(db_path, {})
    bot.image_validity_service.template_reports = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    service = _service(db_path, bot)

    await service._guard_reserve_capacity(division_id, RESERVE)


async def test_no_bot_means_no_guard(tmp_path):
    from services.placement_service import PlacementService

    db_path, _season_id, division_id = await _seed(tmp_path, reserves=3)
    await PlacementService(db_path)._guard_reserve_capacity(
        division_id, RESERVE
    )


# ── Through the choke point, not only in isolation ────────────────────────


async def test_an_ordinary_placement_survives_a_full_reserve_block_through_assign_driver(
    tmp_path,
):
    """#140 as a league met it: `/driver assign` refused, citing the reserve block."""
    db_path, season_id, division_id = await _seed(tmp_path, reserves=3)
    template = _template_file(tmp_path, reserve_slots=3)
    service = _service(db_path, _bot(db_path, {LINEUP: _report(template)}))

    guild = MagicMock()
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(return_value=None)

    result = await service.assign_driver(
        driver_profile_id=9999,
        division_id=division_id,
        team_name=ORDINARY,
        season_id=season_id,
        acting_user_id=99,
        acting_user_name="Tester",
        guild=guild,
        discord_user_id="8888",
    )

    assert result["team_name"] == ORDINARY


async def test_a_reserve_overflow_still_reaches_the_caller_through_assign_driver(tmp_path):
    db_path, season_id, division_id = await _seed(tmp_path, reserves=3)
    template = _template_file(tmp_path, reserve_slots=3)
    service = _service(db_path, _bot(db_path, {LINEUP: _report(template)}))

    with pytest.raises(ValueError) as excinfo:
        await service.assign_driver(
            driver_profile_id=9999,
            division_id=division_id,
            team_name=RESERVE,
            season_id=season_id,
            acting_user_id=99,
            acting_user_name="Tester",
            guild=MagicMock(),
            discord_user_id="8888",
        )

    assert "reserve slots" in str(excinfo.value)
