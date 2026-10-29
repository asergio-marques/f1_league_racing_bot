"""Confirming a season's placements mid-season on the caller's save (#439, slice 8, Part B).

`confirm_placements_on` commits the season's uncommitted placements and returns it from Ongoing,
placements to Ongoing, on the connection it is handed, committing nothing: the mid-season
confirmation's save holds it. It never moves the season on to Pending completion: a season whose
every division is done is refused before it is reached (owner, check 1). `uncommitted_placements_on`
reads the placements to confirm on the same connection. `driver_roles_step` is the one job that
gives a driver their division's and team's roles, for the season's approval and the mid-season
confirmation alike.

The season is `test_uncommitted_drivers_outside_attendance`'s: season 1 in Ongoing, placements,
division Pro (role 1), driver 1001 committed at Alpha and driver 1002 placed there uncommitted.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection
from tests.attendance.test_uncommitted_drivers_outside_attendance import (
    DIVISION_ID,
    db_path,  # noqa: F401 — the fixture
)


async def _stage(db) -> str:
    cursor = await db.execute("SELECT stage FROM seasons WHERE id = 1")
    return (await cursor.fetchone())["stage"]


async def _uncommitted(db) -> int:
    cursor = await db.execute(
        "SELECT COUNT(*) FROM driver_season_assignments WHERE season_id = 1 AND committed = 0"
    )
    return (await cursor.fetchone())[0]


# ── confirm_placements_on ──────────────────────────────────────────────────────────


async def test_confirming_on_commits_the_placements_and_returns_the_season_to_ongoing(db_path):
    """On the connection handed, and nothing of it outlasts a rollback."""
    from leaguebot.core.services.season_lifecycle_service import confirm_placements_on

    async with get_connection(db_path) as db:
        confirmed = await confirm_placements_on(db, 1)

        assert confirmed.committed == 1
        assert await _uncommitted(db) == 0
        assert await _stage(db) == "ONGOING"
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _uncommitted(db) == 1
        assert await _stage(db) == "ONGOING_PLACEMENTS"


async def test_confirming_on_from_another_stage_raises_and_writes_nothing(db_path):
    from leaguebot.core.models.season import InvalidStageTransition
    from leaguebot.core.services.season_lifecycle_service import confirm_placements_on

    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET stage = 'ONGOING' WHERE id = 1")
        await db.commit()

        with pytest.raises(InvalidStageTransition):
            await confirm_placements_on(db, 1)

        assert await _uncommitted(db) == 1
        assert await _stage(db) == "ONGOING"


async def test_confirming_on_never_moves_the_season_on_to_pending_completion(db_path):
    """Pro is finished: the confirmation is refused before it gets here (owner, check 1), and
    `confirm_placements_on` itself returns the season to Ongoing only, saying nothing of a move
    on."""
    from leaguebot.core.services.season_lifecycle_service import confirm_placements_on

    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", (DIVISION_ID,))
        await db.commit()

        confirmed = await confirm_placements_on(db, 1)

        assert await _stage(db) == "ONGOING"
        assert not hasattr(confirmed, "pending_completion")


# ── uncommitted_placements_on ──────────────────────────────────────────────────────


async def test_the_uncommitted_placements_are_read_on_the_connection_handed(db_path):
    """Driver 1003, seated at Alpha with no placement, is placed on the open connection and not
    yet saved: the read on that connection sees them beside 1002."""
    from leaguebot.core.services.placement_service import uncommitted_placements_on

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
            "SELECT 3, 1, ?, id, 0 FROM team_seats WHERE driver_profile_id = 3",
            (DIVISION_ID,),
        )

        placements = await uncommitted_placements_on(db, 1)

        assert [p["discord_user_id"] for p in placements] == ["1002", "1003"]
        assert placements[0]["team_name"] == "Alpha"


# ── driver_roles_step ──────────────────────────────────────────────────────────────


def _granting(monkeypatch):
    """The step's placement double, and the league's server it reaches."""
    from leaguebot.core.services import season_approval_change

    guild = MagicMock()
    monkeypatch.setattr(season_approval_change, "league_guild", AsyncMock(return_value=guild))
    placement = MagicMock()
    placement.get_team_role_config = AsyncMock(return_value=SimpleNamespace(role_id=777))
    placement.grant_roles = AsyncMock(return_value=True)
    return guild, placement


async def _grant(db_path, placement, payload):
    from leaguebot.core.services.season_approval_change import driver_roles_step

    step = driver_roles_step(placement)
    ctx = SimpleNamespace(bot=MagicMock(), db_path=db_path, step_payload=payload,
                          step_name=step.name, tries=0, kept=None)
    return await step.run(ctx)


async def test_a_driver_with_no_team_is_granted_their_division_s_role_alone(db_path, monkeypatch):
    """Driver 1002 is placed in Pro with no team seat read (the placements' LEFT JOIN gives no
    team): the step gives Pro's role (1) alone and looks no team role up."""
    guild, placement = _granting(monkeypatch)

    await _grant(db_path, placement,
                 {"user_id": "1002", "division_id": DIVISION_ID, "team_name": None})

    placement.get_team_role_config.assert_not_awaited()
    placement.grant_roles.assert_awaited_once_with(guild, 1002, 1)


async def test_a_driver_with_no_role_to_give_is_granted_nothing(db_path, monkeypatch):
    """Pro has no role set (0) and driver 1002 no team: the step reaches Discord for nothing."""
    _guild, placement = _granting(monkeypatch)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET mention_role_id = 0 WHERE id = ?",
                         (DIVISION_ID,))
        await db.commit()

    await _grant(db_path, placement,
                 {"user_id": "1002", "division_id": DIVISION_ID, "team_name": None})

    placement.grant_roles.assert_not_awaited()
