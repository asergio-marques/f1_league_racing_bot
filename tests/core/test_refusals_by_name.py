"""A refusal is caught by its name, and a fault is never shown to a member as one.

architecture.md, "Errors and failures": a command catches only the errors it expects by name, and
only to turn them into a refusal the member can act on. A member's refusal is raised as `Refused`;
a `ValueError` that is not one is what Python raises for a bug (`int("x")`, a bad enum value), and
a catch that took it for a refusal would answer a fault with its text as if the member had asked
for something the league does not allow.

Each case here raises a bare `ValueError` where a bug would raise it, inside the call a catch
guards, and asserts it goes on as a fault: raised out of the check or the service, not turned
into a refusal. The refusals themselves, in their words, are each service's own tests'.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import run_migrations

#: The fault each case raises, as `int()` words one.
BUG = "invalid literal for int() with base 10: 'x'"

#: Why each case fails until #283's build catches refusals by name.
CAUGHT_AS_A_REFUSAL = "#283: a catch still takes any ValueError for a member's refusal"

#: A placement check's payload, every id a number, as the cog asks it.
_PAYLOAD = {
    "season_id": 1, "division_id": 11, "division": "Pro", "from_division_id": 11,
    "from_division": "Pro", "to_division_id": 12, "to_division": "Am", "driver_profile_id": 31,
    "discord_user_id": "4242", "driver_name": "Lewis", "team_name": "Ferrari",
    "team_full_name": "Scuderia Ferrari",
}


def _faulty_placement() -> MagicMock:
    """A placement service whose every guard and judge raises a bug's `ValueError`."""
    placement = MagicMock()
    for name in (
        "guard_move", "guard_assign", "guard_roster_capacity", "judge_move_on", "judge_sack_on",
        "judge_release_on", "judge_assign_on", "judge_unassign_on",
    ):
        setattr(placement, name, AsyncMock(side_effect=ValueError(BUG)))
    return placement


def _placement_check(factory: str) -> Callable[..., Awaitable[None]]:
    """The check of `driver_changes.<factory>`'s change, every refusal read from the database
    standing aside, judged with a placement service that raises a bug's `ValueError`."""

    async def run(tmp_path, monkeypatch) -> None:
        from leaguebot.core.models.change import ChangeOrigin
        from leaguebot.core.services.change_queue import CheckContext
        from leaguebot.core.services import driver_changes

        for refusal in ("ongoing_refusal", "placing_refusal", "division_refusal",
                        "profile_refusal"):
            monkeypatch.setattr(driver_changes, refusal, AsyncMock(return_value=None))
        db_path = str(tmp_path / "check.db")
        await run_migrations(db_path)
        change = getattr(driver_changes, factory)(
            placement=_faulty_placement(),
            now=lambda: datetime(2026, 9, 17, 12, tzinfo=timezone.utc),
        )
        await change.check(CheckContext(
            payload=dict(_PAYLOAD), bot=MagicMock(), db_path=db_path,
            origin=ChangeOrigin.MEMBER,
        ))

    return run


async def _test_driver_added(tmp_path, monkeypatch) -> None:
    """`/test-mode driver add` of a driver to Team 1 in Alpha, the capacity guard raising."""
    from leaguebot.core.services.test_roster_service import add_test_driver
    from tests.core.test_test_roster_capacity_guards import TEAMS, _seed

    path, _divisions = await _seed(tmp_path)
    await add_test_driver(
        driver_name="Mock", team_name=TEAMS[0], division_name="Alpha", db_path=path,
        placement_service=_faulty_placement(),
    )


async def _test_roster_imported(tmp_path, monkeypatch) -> None:
    """A test roster of one driver for Team 1 in Alpha imported, the capacity guard raising."""
    from leaguebot.core.services.test_roster_service import add_test_drivers_in_bulk
    from tests.core.test_test_roster_capacity_guards import TEAMS, _roster, _seed

    path, _divisions = await _seed(tmp_path)
    await add_test_drivers_in_bulk(
        _roster((TEAMS[0], "Alpha")), path, placement_service=_faulty_placement(),
    )


#: One case per catch: what it guards, and how to raise a bug's `ValueError` inside it.
CATCHES = [
    pytest.param(_placement_check("driver_move_change"), id="driver move check",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_A_REFUSAL)),
    pytest.param(_placement_check("driver_sack_change"), id="driver sack check",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_A_REFUSAL)),
    pytest.param(_placement_check("driver_release_change"), id="driver release check",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_A_REFUSAL)),
    pytest.param(_placement_check("driver_assign_change"), id="driver assign check",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_A_REFUSAL)),
    pytest.param(_placement_check("driver_unassign_change"), id="driver unassign check",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_A_REFUSAL)),
    pytest.param(_test_driver_added, id="test driver added",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_A_REFUSAL)),
    pytest.param(_test_roster_imported, id="test roster imported",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_A_REFUSAL)),
]


@pytest.mark.parametrize("provoke", CATCHES)
async def test_an_unexpected_value_error_is_a_failure_not_a_refusal(
    tmp_path, monkeypatch, provoke,
):
    """A bug's `ValueError` raised inside a refusal catch goes on as a fault, in its own words,
    rather than being answered to the member as a refusal."""
    with pytest.raises(ValueError, match="invalid literal") as raised:
        await provoke(tmp_path, monkeypatch)
    assert type(raised.value) is ValueError
