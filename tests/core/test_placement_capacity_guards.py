"""Core's capacity refusals, asked of the image module through the protocol core declares (#283).

`TemplateCapacityService` holds no template. It counts the seats a placement would fill, asks the
drawing the builder hands it whether the image module could draw that many, and refuses in its own
words where it could not: the problem and its remedy are the image module's, and that the driver was
**not** assigned is core's. A drawing that cannot answer lets the placement through, with the full
error in the host's log, and a service handed no drawing asks nothing.

The counting against a real template is the image module's, and is tested against its
implementation under `tests/image/`. Nothing here reads a template.
"""
from __future__ import annotations

import logging

import pytest

from leaguebot.core.db.database import get_connection, run_migrations


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
    """A fresh database named *name* under *tmp_path*: a division with a reserve team and two race
    teams of two seats each, one reserve and two race drivers seated (one in each race team, so
    that each count below tells the reserves and the race drivers apart), and driver 9999 waiting
    to be placed."""
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
        for user_id, seat_id in (("1000", reserve_seat), ("1001", seats[0]), ("1002", seats[2])):
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


def _capacity(db_path, drawing=None):
    """The template capacity service on *db_path*, handed *drawing* (none by default, so it asks
    nothing), as the builder makes it."""
    from leaguebot.core.services.drivers.template_capacity_service import TemplateCapacityService

    return TemplateCapacityService(db_path, drawing=drawing)


def _guarded(db_path, drawing):
    """The placement service on *db_path*, handed a capacity service that asks *drawing*."""
    from leaguebot.core.services.drivers.placement_service import PlacementService

    return PlacementService(db_path, capacity=_capacity(db_path, drawing))


async def _place(service, season_id, division_id, team):
    """Ask *service* whether the unplaced driver 9999 may be assigned to *team*: the guards
    `/driver assign` asks at the ask and again as its change starts."""
    await service.guard_assign(division_id, season_id, team, 9999)


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
    """Each guard refuses in its words where the drawing reports a problem, and lets the assignment
    through where it reports none, and where it raises (the error kept whole in the host's
    log)."""
    from leaguebot.core.models.refusal import Refused
    from leaguebot.core.services.modules.image_drawing import CapacityProblem

    db_path, season_id, division_id = await _seed(tmp_path)
    refusing = _Drawing({guard: CapacityProblem(problem=PROBLEM, remedy=REMEDY)})

    with pytest.raises(Refused) as refused:
        await _place(_guarded(db_path, refusing), season_id, division_id, team)

    assert str(refused.value) == refusal
    assert guard in [name for name, _count in refusing.asked]

    db_path, season_id, division_id = await _seed(tmp_path, "quiet")
    quiet = _Drawing({})
    await _place(_guarded(db_path, quiet), season_id, division_id, team)

    assert guard in [name for name, _count in quiet.asked]

    db_path, season_id, division_id = await _seed(tmp_path, "raising")
    fault = RuntimeError("the drawing could not answer")
    raising = _Drawing({name: fault for name in GUARDS})
    with caplog.at_level(logging.ERROR, logger="leaguebot.core.services.drivers.template_capacity_service"):
        await _place(_guarded(db_path, raising), season_id, division_id, team)

    assert guard in [name for name, _count in raising.asked]
    assert any(record.exc_info and record.exc_info[1] is fault for record in caplog.records)


def _failing_reads(monkeypatch, fragment: str, fault: Exception) -> None:
    """Every read `template_capacity_service` makes whose SQL holds *fragment* raises *fault*;
    every other read and write runs on the real connection."""
    from contextlib import asynccontextmanager

    from leaguebot.core.services.drivers import template_capacity_service

    real = template_capacity_service.get_connection

    class _Connection:
        def __init__(self, db):
            self._db = db

        def execute(self, sql, *args, **kwargs):
            if fragment in sql:
                raise fault
            return self._db.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._db, name)

    @asynccontextmanager
    async def connection(*args, **kwargs):
        async with real(*args, **kwargs) as db:
            yield _Connection(db)

    monkeypatch.setattr(template_capacity_service, "get_connection", connection)


@pytest.mark.parametrize(
    "team, read",
    [
        (RESERVE, "AND ti.is_reserve = 1"),
        (ORDINARY, "AS seated FROM driver_season_assignments WHERE division_id = ?"),
        (ORDINARY, "AND ti.is_reserve = 0"),
        (ORDINARY, "AS seated FROM driver_season_assignments WHERE season_id = ?"),
    ],
    ids=["reserve", "sheet", "standings", "declared"],
)
async def test_a_capacity_guard_s_own_read_fault_lets_the_placement_through(
    tmp_path, monkeypatch, caplog, team, read
):
    """A guard's own count of the seats, read before the drawing is asked, sits inside the one
    catch with the asking: where the count raises, the guards let the assignment through and the
    error is kept whole in the host's log, as where the drawing raises."""

    db_path, season_id, division_id = await _seed(tmp_path)
    fault = RuntimeError("the seats could not be counted")
    _failing_reads(monkeypatch, read, fault)
    with caplog.at_level(logging.ERROR, logger="leaguebot.core.services.drivers.template_capacity_service"):
        await _place(
            _guarded(db_path, _Drawing({})), season_id, division_id, team
        )

    assert any(record.exc_info and record.exc_info[1] is fault for record in caplog.records)


@pytest.mark.parametrize(
    "team, asked",
    [
        (
            RESERVE,
            [
                ("reserve_capacity_problem", 2),
                ("sheet_capacity_problem", 4),
                ("declared_capacity_problem", 4),
            ],
        ),
        (
            ORDINARY,
            [
                ("sheet_capacity_problem", 4),
                ("standings_capacity_problem", 3),
                ("declared_capacity_problem", 4),
            ],
        ),
    ],
    ids=["reserve", "ordinary"],
)
async def test_a_placement_asks_each_guard_its_own_count(tmp_path, team, asked):
    """Core counts, and the drawing is asked of the count alone: the reserve block counts the
    reserves, the sheet every driver of the division, the standings the race drivers, the
    declared capacity every driver of the season's division. A reserve joins no classification,
    so a reserve placement never asks the standings; only a reserve fills the reserve block, so
    an ordinary placement never asks it (#140)."""

    db_path, season_id, division_id = await _seed(tmp_path)
    drawing = _Drawing({})

    await _place(_guarded(db_path, drawing), season_id, division_id, team)

    assert drawing.asked == asked


async def test_an_unknown_team_asks_neither_the_reserve_block_nor_the_standings(tmp_path):
    """Whether a team is the reserve cannot be known of a team the division does not hold, so
    the two guards that ask it are left out and the guards raise nothing: the assignment's own
    check refuses the team in its own words (`test_placement_assign.py`)."""

    db_path, season_id, division_id = await _seed(tmp_path)
    drawing = _Drawing({})

    await _place(_guarded(db_path, drawing), season_id, division_id, "No Such Team")

    asked = [name for name, _count in drawing.asked]
    assert "reserve_capacity_problem" not in asked
    assert "standings_capacity_problem" not in asked


@pytest.mark.parametrize(
    "added, asked",
    [
        (
            {RESERVE: 2, ORDINARY: 1, "Team 2": 2, "No Such Team": 5},
            [
                ("reserve_capacity_problem", 3),
                ("sheet_capacity_problem", 8),
                ("standings_capacity_problem", 5),
            ],
        ),
        (
            {ORDINARY: 2},
            [("sheet_capacity_problem", 5), ("standings_capacity_problem", 4)],
        ),
        (
            {RESERVE: 2},
            [("reserve_capacity_problem", 3), ("sheet_capacity_problem", 5)],
        ),
    ],
    ids=["mixed", "race-only", "reserves-only"],
)
async def test_a_roster_asks_each_guard_once_summed_across_its_teams(tmp_path, added, asked):
    """A test roster is measured whole, per division (#150): each guard is asked once, of the
    drivers already seated and every driver the roster adds across its teams; a team the
    division does not hold adds nothing, the roster refusing it in its own words; and the
    declared capacity is never asked, since every catalogue declares none."""

    db_path, _season_id, division_id = await _seed(tmp_path)
    drawing = _Drawing({})

    await _capacity(db_path, drawing).guard_roster_capacity(division_id, added)

    assert drawing.asked == asked


async def test_no_drawing_handed_means_no_guard(tmp_path):
    """A service built alone, as a test that exercises placement builds it, refuses no test
    roster however large: the four `test_no_bot_means_no_guard` tests, merged. That such a service
    places a driver is `test_placement_assign.py`'s."""

    db_path, _season_id, division_id = await _seed(tmp_path)

    await _capacity(db_path).guard_roster_capacity(
        division_id, {RESERVE: 99, ORDINARY: 99}
    )


async def test_a_guard_whose_switch_nobody_signed_up_for_raises(tmp_path):
    """The images module's switch reader was never signed up (`SwitchNotSignedUp`): that is a fault
    in the bot's assembly, not a guard that cannot run, so the placement raises it rather than
    letting the driver through unchecked."""
    from leaguebot.core.services.modules.module_service import SwitchNotSignedUp

    db_path, season_id, division_id = await _seed(tmp_path)
    drawing = _Drawing({name: SwitchNotSignedUp("images") for name in GUARDS})

    with pytest.raises(SwitchNotSignedUp, match="images"):
        await _place(_guarded(db_path, drawing), season_id, division_id, RESERVE)
