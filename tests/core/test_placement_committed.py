"""Committed and uncommitted placements (issue #220).

A placement made with `/driver assign` is uncommitted: it stands outside the championship,
granting no role and posting no lineup, until placements are confirmed. Mid-season, the assign
and unassign commands are kept to uncommitted drivers, a committed one being moved or released
instead. A placement written without saying takes the default its season implies.

Both commands are changes on the league's lane (#283). Here are their saves, `assign_on` and
`unassign_on`, each on a connection of the test's own and committed once it returns. No command
places a committed driver, so neither change gives or takes a role or posts a lineup: that is
pinned through the queue in `test_driver_assign.py` and `test_driver_unassign.py`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection
from tests.core.test_placement_assign import (
    DIVISION_ID,
    PROFILE_ID,
    SEASON_ID,
    SERVER_ID,
    _make_db,
    _service,
)

#: Why a placement refusal test fails until #283's build raises the member's refusal by name.
PLACEMENT_REFUSAL = "#283: placement still refuses with a bare ValueError, not Refused"


async def _assign(db_path, *, division_id: int = DIVISION_ID, **kwargs) -> dict:
    """Place the driver at Alpha through `assign_on`: `/driver assign`'s save without the change
    around it."""
    async with get_connection(db_path) as db:
        placed = await _service(db_path).assign_on(
            db,
            driver_profile_id=PROFILE_ID,
            division_id=division_id,
            team_name="Alpha",
            season_id=SEASON_ID,
            **kwargs,
        )
        await db.commit()
    return placed


async def _unassign(db_path, **kwargs) -> dict:
    """Take the driver out of Division 1 through `unassign_on`: `/driver unassign`'s save
    without the change around it."""
    async with get_connection(db_path) as db:
        removed = await _service(db_path).unassign_on(
            db,
            driver_profile_id=PROFILE_ID,
            division_id=DIVISION_ID,
            season_id=SEASON_ID,
            **kwargs,
        )
        await db.commit()
    return removed


async def _place(db_path, committed: int | None = None) -> None:
    """Seat the driver in Alpha's first seat of Division 1 and make him Assigned, written
    directly. Where *committed* is None the column is left unsaid, so the season's default
    applies."""
    columns, values = ("", "") if committed is None else (", committed", f", {committed}")
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? "
            "WHERE team_instance_id = 10 AND seat_number = 1",
            (PROFILE_ID,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            f"(driver_profile_id, season_id, division_id, team_seat_id{columns}) "
            f"SELECT ?, ?, ?, id{values} FROM team_seats "
            "WHERE team_instance_id = 10 AND seat_number = 1",
            (PROFILE_ID, SEASON_ID, DIVISION_ID),
        )
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'ASSIGNED' WHERE id = ?", (PROFILE_ID,)
        )
        await db.commit()


async def _committed(db_path) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT committed FROM driver_season_assignments WHERE driver_profile_id = ?",
            (PROFILE_ID,),
        )
        return (await cursor.fetchone())["committed"]


async def _set_season_status(db_path, status: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = ? WHERE id = ?", (status, SEASON_ID))
        await db.commit()


# ── Assigning ──────────────────────────────────────────────────────────────────────


async def test_an_assignment_is_saved_unconfirmed_even_in_a_confirmed_season(tmp_path):
    """The fixture's season is active, where a placement written without saying is confirmed.
    `/driver assign`'s save places the driver at Alpha in Division 1 unconfirmed all the same."""
    db_path = await _make_db(tmp_path)

    await _assign(db_path)

    assert await _committed(db_path) == 0


async def test_a_placement_in_a_confirmed_season_defaults_to_committed(tmp_path):
    """A placement written into the active fixture season without saying whether it is
    confirmed, as a test roster's is, is confirmed."""
    db_path = await _make_db(tmp_path)  # the fixture season is ACTIVE

    await _place(db_path)

    assert await _committed(db_path) == 1


async def test_a_placement_in_a_season_in_setup_defaults_to_uncommitted(tmp_path):
    db_path = await _make_db(tmp_path)
    await _set_season_status(db_path, "SETUP")

    await _place(db_path)

    assert await _committed(db_path) == 0


@pytest.mark.xfail(strict=True, reason=PLACEMENT_REFUSAL)
async def test_a_committed_driver_is_refused_where_only_uncommitted_ones_may_be_placed(tmp_path):
    """Mid-season placing: the driver already holds a confirmed seat at Alpha in Division 1, and
    is assigned to Division 2 where only unconfirmed drivers may be placed. The save refuses him,
    saying he already holds a confirmed placement, and places him nowhere new."""
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    await _place(db_path)  # committed, in the ACTIVE fixture season
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (12, ?, 'Division 2', 2, 556)",
            (SEASON_ID,),
        )
        await db.commit()

    with pytest.raises(Refused, match="confirmed placement"):
        await _assign(db_path, division_id=12, uncommitted_only=True)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM driver_season_assignments")
        assert (await cursor.fetchone())["n"] == 1


# ── Unassigning ────────────────────────────────────────────────────────────────────


async def test_removing_an_uncommitted_placement_revokes_nothing_and_posts_nothing(tmp_path):
    """The driver sits unconfirmed at Alpha in Division 1. Taking him out names no role to
    take back and no lineup to post."""
    db_path = await _make_db(tmp_path)
    await _place(db_path, committed=0)

    result = await _unassign(db_path)

    assert result["revoke"] == []
    assert result["lineups"] == []


@pytest.mark.xfail(strict=True, reason=PLACEMENT_REFUSAL)
async def test_a_committed_placement_is_refused_where_only_uncommitted_ones_may_be_removed(tmp_path):
    """The driver sits confirmed at Alpha in Division 1. Taking him out where only unconfirmed
    placements may be removed is refused, pointing to `/driver move`, and he stays confirmed."""
    from leaguebot.core.models.refusal import Refused
    db_path = await _make_db(tmp_path)
    await _place(db_path)  # committed, in the ACTIVE fixture season

    with pytest.raises(Refused, match="/driver move"):
        await _unassign(db_path, uncommitted_only=True)

    assert await _committed(db_path) == 1


# ── The commands ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["assign", "unassign"])
@pytest.mark.parametrize("stage_name", ["CONFIGURATION", "SIGNUPS", "ONGOING", "PENDING_COMPLETION"])
async def test_the_placement_commands_are_refused_outside_the_placing_stages(command, stage_name):
    from types import SimpleNamespace

    from leaguebot.core.cogs.driver_cog import DriverCog
    from leaguebot.core.models.season import SeasonStage
    from tests.support.undecorate import undecorate

    cog = DriverCog.__new__(DriverCog)
    cog.bot = MagicMock()
    # Any account names the driver (issue #243); these tests name the current one.
    cog.bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=1, stage=SeasonStage(stage_name))
    )
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    args = (MagicMock(), "Division 1", "Alpha") if command == "assign" else (MagicMock(), "Division 1")
    await undecorate(getattr(DriverCog, command))(cog, interaction, *args)

    assert "available only while the season is in placements" in (
        interaction.followup.send.await_args.args[0]
    )
    assert cog.bot.change_queue.method_calls == []

