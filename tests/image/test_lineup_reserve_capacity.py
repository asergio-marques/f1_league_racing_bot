"""The image module's answer to core's reserve question: does the lineup's reserve block hold
that many reserves? (#140, #283)

Core counts the reserves a placement would leave in the division and asks; the image module reads
the configured lineup template off the event loop and hands back the problem and its remedy, or
nothing. That only a reserve placement asks, and that only reserves are counted, is core's, and is
`tests/core/test_placement_capacity_guards.py`'s. Here the real implementation is handed to
`PlacementService` as well, so that the refusal a league reads is pinned word for word (#140: a
full reserve block refused a free race seat).

Nothing here rasterises: the templates are built in memory and read as SVG.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from lxml import etree


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


async def _seed(tmp_path, *, reserves: int):
    """A division carrying *reserves* reserve drivers, and two ordinary teams with empty seats."""
    from leaguebot.core.db.database import get_connection, run_migrations

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
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, 0, 1)",
            (division_id, RESERVE, RESERVE),
        )
        reserve_instance = cursor.lastrowid
        # Each ordinary team carries its two seats, empty until a driver is seated in one —
        # an assignment needs a free seat to place into, as a real division would have.
        for index in (1, 2):
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, 2, 0)",
                (division_id, f"Team {index}", f"Team {index}"),
            )
            instance_id = cursor.lastrowid
            for seat_number in (1, 2):
                await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number, "
                    "driver_profile_id) VALUES (?, ?, NULL)",
                    (instance_id, seat_number),
                )

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

        # The driver every test then tries to place.
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (9999, '8888', 'UNASSIGNED')"
        )
        await db.commit()

    return db_path, season_id, division_id


REMEDY = "Enlarge the template, or turn the `lineup` image aspect off with `/images config toggle`."


def _drawing(reports, *, toggle: bool = True, images: bool = True):
    """The image module's implementation of core's protocol, over stand-ins for its own services:
    the images module *images*, the `lineup` aspect *toggle*, and the template *reports*."""
    from leaguebot.image.services.image_drawing import ImageDrawing

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=images)
    config = MagicMock()
    config.get_toggles = AsyncMock(return_value={"lineup": toggle})
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value=reports)
    return ImageDrawing(
        modules=modules, config=config, validity=validity,
        renderer=MagicMock(), post_log=AsyncMock(), db_path="",
    )


def _report(path):
    return MagicMock(valid=True, resolved_path=str(path), template_key=LINEUP)


async def _assign(db_path, season_id, division_id, team, drawing):
    """Ask the guards `/driver assign` asks whether the unplaced driver 9999 may join *team*."""
    from leaguebot.core.services.placement_service import PlacementService

    await PlacementService(db_path, drawing=drawing).guard_assign(
        division_id, season_id, team, 9999
    )


# ── The answer ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "slots, reserves, problem",
    [
        (
            3,
            4,
            "that would give the division 4 reserve drivers, but the configured lineup "
            "template declares 3 reserve slots",
        ),
        (
            1,
            2,
            "that would give the division 2 reserve drivers, but the configured lineup "
            "template declares 1 reserve slot",
        ),
    ],
    ids=["slots", "one-slot"],
)
async def test_reserves_past_the_slots_are_a_problem(tmp_path, slots, reserves, problem):
    template = _template_file(tmp_path, reserve_slots=slots)

    answer = await _drawing({LINEUP: _report(template)}).reserve_capacity_problem(reserves)

    assert (answer.problem, answer.remedy) == (problem, REMEDY)


@pytest.mark.parametrize(
    "slots, reserves",
    [(5, 4), (3, 3), (0, 9)],
    ids=["within", "exactly", "no-reserve-block"],
)
async def test_reserves_the_block_holds_are_no_problem(tmp_path, slots, reserves):
    """A template declaring no reserve slot at all measures nothing."""
    template = _template_file(tmp_path, reserve_slots=slots)

    answer = await _drawing({LINEUP: _report(template)}).reserve_capacity_problem(reserves)

    assert answer is None


@pytest.mark.parametrize(
    "toggle, images", [(False, True), (True, False)], ids=["aspect-off", "module-off"]
)
async def test_no_lineup_drawn_means_no_problem(tmp_path, toggle, images):
    """The ceiling exists because a graphic would drop a driver. No graphic, no ceiling."""
    template = _template_file(tmp_path, reserve_slots=3)
    drawing = _drawing({LINEUP: _report(template)}, toggle=toggle, images=images)

    assert await drawing.reserve_capacity_problem(4) is None


# ── As a league meets it, through the choke point ─────────────────────────


async def test_a_reserve_overflow_reaches_the_caller_through_guard_assign(tmp_path):
    from leaguebot.core.models.refusal import Refused

    db_path, season_id, division_id = await _seed(tmp_path, reserves=3)
    template = _template_file(tmp_path, reserve_slots=3)

    with pytest.raises(Refused) as refused:
        await _assign(
            db_path, season_id, division_id, RESERVE, _drawing({LINEUP: _report(template)})
        )

    assert str(refused.value) == (
        "that would give the division 4 reserve drivers, but the configured lineup template "
        "declares 3 reserve slots. The driver was **not** assigned. Enlarge the template, or "
        "turn the `lineup` image aspect off with `/images config toggle`."
    )


async def test_an_ordinary_placement_survives_a_full_reserve_block_through_guard_assign(
    tmp_path,
):
    """#140 as a league met it: `/driver assign` refused, citing the reserve block."""
    db_path, season_id, division_id = await _seed(tmp_path, reserves=3)
    template = _template_file(tmp_path, reserve_slots=3)

    await _assign(
        db_path, season_id, division_id, ORDINARY, _drawing({LINEUP: _report(template)})
    )
