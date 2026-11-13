"""Who may answer a division's check-in call, judged through core's seat holders.

A press of the call is judged on its facts (`check_in_answer_changes._facts`), which read the
driver's seat through `TeamService.seat_holders_on` in place of attendance's own join of the
driver's placement, its seat and the seat's team. The move changes nothing a league sees, so each
driver is judged as the old join judged them: only a driver whose confirmed placement in the
division names a seat there may answer.

The league is `test_seat_join_equivalence`'s: Season 1 is active with Pro and Am, each holding
one round whose call stands. Driver 1 is placed and seated in Pro's Alpha and in Am's Bravo;
driver 2 in Pro's Alpha on a placement not yet confirmed; driver 3 in Pro's reserve team; driver
4 in Am's reserve team; and driver 5 holds a seat of Pro's Alpha with no placement row at all.
"""
from __future__ import annotations

import pytest

from leaguebot.attendance.services import check_in_answer_changes as answers
from leaguebot.core.db.database import get_connection
from tests.attendance.test_seat_join_equivalence import AM, PRO, _seed

_NO_PLACEMENT = (
    "#285: a driver holding a seat with no placement row in the division is let answer, where "
    "the old join refused them as not a member"
)


async def _league(path: str) -> None:
    """The league of the module docstring, with the call of each division's round standing and
    the attendance configuration written."""
    await _seed(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (5, '105', 'ASSIGNED')"
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (6, 2, 3, 5)"
        )
        await db.execute("INSERT INTO attendance_config (id, module_enabled) VALUES (1, 1)")
        for division_id in (PRO, AM):
            await db.execute(
                "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, "
                "channel_id, posted_at) VALUES (?, ?, '7001', '603', '2026-02-27T18:00:00+00:00')",
                (division_id, division_id),
            )
        await db.commit()


@pytest.mark.parametrize(("division_id", "driver", "reserve"), [
    pytest.param(PRO, 1, False, id="a-confirmed-seat"),
    pytest.param(PRO, 3, True, id="a-confirmed-reserve-seat"),
    pytest.param(PRO, 2, None, id="b-unconfirmed-placement"),
    pytest.param(PRO, 4, None, id="c-seated-in-another-division-only"),
    pytest.param(
        PRO, 5, None, id="d-seat-with-no-placement",
        marks=pytest.mark.xfail(strict=True, reason=_NO_PLACEMENT),
    ),
    pytest.param(PRO, 1, False, id="e-placed-in-two-divisions-pro"),
    pytest.param(AM, 1, False, id="e-placed-in-two-divisions-am"),
])
async def test_the_seat_read_through_core_judges_a_driver_as_the_old_join_did(
    tmp_path, division_id, driver, reserve,
):
    """A driver pressing the call of their division's round is judged a member, with their seat's
    reserve flag, only where their confirmed placement there names a seat of it; any other
    driver, *reserve* None here, is answered that they are not a member of this division."""
    path = str(tmp_path / "league.db")
    await _league(path)

    async with get_connection(path) as db:
        facts = await answers._facts(db, {
            "round_id": division_id, "division_id": division_id, "driver_profile_id": driver,
        })

    if reserve is None:
        assert facts == answers._NOT_A_MEMBER
    else:
        assert not isinstance(facts, str)
        assert facts.is_reserve is reserve
        assert facts.found == "NO_RSVP"
