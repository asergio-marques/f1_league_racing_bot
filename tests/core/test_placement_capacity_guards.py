"""Core's capacity refusals, asked of the image module through the protocol core declares (#283).

`PlacementService` holds no template. It counts the seats a placement would fill, asks the drawing
the builder hands it whether the image module could draw that many, and refuses in its own words
where it could not: the problem and its remedy are the image module's, and that the driver was
**not** assigned is core's. A drawing that cannot answer lets the placement through, with the full
error in the host's log, and a service handed no drawing asks nothing.

The counting against a real template is the image module's, and is tested against its
implementation under `tests/image/`. Nothing here reads a template.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

_NOT_YET = "#283: PlacementService does not yet take a drawing to ask its capacity guards of"

RESERVE = "Reserve"
ORDINARY = "Team 1"
PROBLEM = "The stub template draws too few rows"
REMEDY = "Enlarge the stub template."
TEMPLATE_REFUSAL = f"{PROBLEM}. The driver was **not** assigned. {REMEDY}"
DECLARED_REFUSAL = f"{PROBLEM}. {REMEDY}"
GUARDS = (
    "reserve_capacity_problem",
    "sheet_capacity_problem",
    "standings_capacity_problem",
    "declared_capacity_problem",
)


class _Drawing:
    """A stand-in for the image module's answers: *answers* maps a method to what it gives back,
    or to the error it raises; every other method answers that nothing is wrong."""

    def __init__(self, answers):
        self._answers = answers
        self.asked: list[tuple[str, int]] = []

    async def _answer(self, name: str, count: int):
        self.asked.append((name, count))
        answer = self._answers.get(name)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def reserve_capacity_problem(self, reserves: int):
        return await self._answer("reserve_capacity_problem", reserves)

    async def sheet_capacity_problem(self, drivers: int):
        return await self._answer("sheet_capacity_problem", drivers)

    async def standings_capacity_problem(self, drivers: int):
        return await self._answer("standings_capacity_problem", drivers)

    async def declared_capacity_problem(self, drivers: int):
        return await self._answer("declared_capacity_problem", drivers)


async def _seed(tmp_path, name: str = "capacity_guards"):
    """A fresh database named *name* under *tmp_path*: a division with a reserve team and two race teams of two free seats each, one reserve and
    one race driver seated, and driver 9999 waiting to be placed."""
    db_path = str(tmp_path / f"{name}.db")
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
        reserve = (
            await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, 0, 1)",
                (division_id, RESERVE, RESERVE),
            )
        ).lastrowid
        seats = []
        for index in (1, 2):
            team = (
                await db.execute(
                    "INSERT INTO team_instances (division_id, name, full_name, max_seats, "
                    "is_reserve) VALUES (?, ?, ?, 2, 0)",
                    (division_id, f"Team {index}", f"Team {index}"),
                )
            ).lastrowid
            for seat_number in (1, 2):
                seats.append(
                    (
                        await db.execute(
                            "INSERT INTO team_seats (team_instance_id, seat_number, "
                            "driver_profile_id) VALUES (?, ?, NULL)",
                            (team, seat_number),
                        )
                    ).lastrowid
                )
        reserve_seat = (
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, 1, NULL)",
                (reserve,),
            )
        ).lastrowid
        for user_id, seat_id in (("1000", reserve_seat), ("1001", seats[0])):
            profile = (
                await db.execute(
                    "INSERT INTO driver_profiles (discord_user_id, current_state) "
                    "VALUES (?, 'ASSIGNED')",
                    (user_id,),
                )
            ).lastrowid
            await db.execute(
                "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?", (profile, seat_id)
            )
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


async def _place(service, season_id, division_id, team):
    guild = MagicMock()
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(return_value=None)
    return await service.assign_driver(
        driver_profile_id=9999,
        division_id=division_id,
        team_name=team,
        season_id=season_id,
        acting_user_id=99,
        acting_user_name="Tester",
        guild=guild,
        discord_user_id="8888",
    )


async def _placed(db_path) -> bool:
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) AS n FROM driver_season_assignments WHERE driver_profile_id = 9999"
            )
        ).fetchone()
    return row["n"] == 1


@pytest.mark.xfail(strict=True, reason=_NOT_YET)
@pytest.mark.parametrize(
    "guard, team, refusal",
    [
        ("reserve_capacity_problem", RESERVE, TEMPLATE_REFUSAL),
        ("sheet_capacity_problem", ORDINARY, TEMPLATE_REFUSAL),
        ("standings_capacity_problem", ORDINARY, TEMPLATE_REFUSAL),
        ("declared_capacity_problem", ORDINARY, DECLARED_REFUSAL),
    ],
    ids=["reserve", "sheet", "standings", "declared"],
)
async def test_placement_capacity_guards_run_with_a_stub_drawing(
    tmp_path, caplog, guard, team, refusal
):
    """Each guard refuses in its words where the drawing reports a problem, and lets the placement
    through where it reports none, where it raises (the error kept whole in the host's log), and
    where no drawing was handed at all."""
    from leaguebot.core.services.image_drawing import CapacityProblem
    from leaguebot.core.services.placement_service import PlacementService

    db_path, season_id, division_id = await _seed(tmp_path)
    refusing = _Drawing({guard: CapacityProblem(problem=PROBLEM, remedy=REMEDY)})

    with pytest.raises(ValueError) as refused:
        await _place(PlacementService(db_path, drawing=refusing), season_id, division_id, team)

    assert str(refused.value) == refusal
    assert guard in [name for name, _count in refusing.asked]
    assert not await _placed(db_path)

    db_path, season_id, division_id = await _seed(tmp_path, "quiet")
    quiet = _Drawing({})
    result = await _place(PlacementService(db_path, drawing=quiet), season_id, division_id, team)

    assert result["team_name"] == team
    assert await _placed(db_path)
    assert guard in [name for name, _count in quiet.asked]

    db_path, season_id, division_id = await _seed(tmp_path, "raising")
    fault = RuntimeError("the drawing could not answer")
    raising = _Drawing({name: fault for name in GUARDS})
    with caplog.at_level(logging.ERROR, logger="leaguebot.core.services.placement_service"):
        result = await _place(
            PlacementService(db_path, drawing=raising), season_id, division_id, team
        )

    assert result["team_name"] == team
    assert await _placed(db_path)
    assert guard in [name for name, _count in raising.asked]
    assert any(record.exc_info and record.exc_info[1] is fault for record in caplog.records)


@pytest.mark.parametrize("team", [RESERVE, ORDINARY])
async def test_no_drawing_handed_means_no_guard(tmp_path, team):
    """A service built alone, as a test that exercises placement builds it, asks nothing and
    refuses nothing, for a placement or for a test roster however large: the four
    `test_no_bot_means_no_guard` tests, merged."""
    from leaguebot.core.services.placement_service import PlacementService

    db_path, season_id, division_id = await _seed(tmp_path)
    service = PlacementService(db_path)

    await service.guard_roster_capacity(division_id, {team: 99})
    result = await _place(service, season_id, division_id, team)

    assert result["team_name"] == team
    assert await _placed(db_path)
