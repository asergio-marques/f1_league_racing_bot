"""The set of driver states, and the rule that every one of them is reachable.

Issue #221: ``SEASON_BANNED`` and ``LEAGUE_BANNED`` sat in the enum and in the transition
table for months while no command could put a driver into either, so the specifications
described a state no league could ever see. These tests pin the seven that survive.
"""
from __future__ import annotations

import pytest

from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.drivers.driver_service import ALLOWED_TRANSITIONS


def test_the_driver_has_seven_states_and_no_ban_among_them():
    """A state nothing can reach is a rule every other rule has to account for and no
    league ever meets. Bans are the stewarding module's to bring, with the commands that
    impose and lift them; adding one back here alone would recreate issue #221."""
    assert {s.value for s in DriverState} == {
        "NOT_SIGNED_UP",
        "PENDING_SIGNUP_COMPLETION",
        "PENDING_ADMIN_APPROVAL",
        "AWAITING_CORRECTION_PARAMETER",
        "PENDING_DRIVER_CORRECTION",
        "UNASSIGNED",
        "ASSIGNED",
    }


def test_every_state_is_reachable_from_not_signed_up():
    """Where a driver starts is Not Signed Up — a member holding no profile is treated as
    one — so a state no walk of the table reaches from there is a state the bot cannot put
    anybody into. This is the guard the ban states slipped past: they were reachable in the
    table, and unreachable in truth, because no caller named them."""
    seen = {DriverState.NOT_SIGNED_UP}
    frontier = [DriverState.NOT_SIGNED_UP]
    while frontier:
        for target in ALLOWED_TRANSITIONS.get(frontier.pop(), set()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)

    assert seen == set(DriverState)


def test_the_table_names_no_state_outside_the_enum():
    """A target the enum does not carry would raise ValueError on the read back, well away
    from the transition that wrote it."""
    named = set(ALLOWED_TRANSITIONS) | {t for ts in ALLOWED_TRANSITIONS.values() for t in ts}

    assert named <= set(DriverState)


_NO_LABEL = "#624: a driver's state has no label of its own yet"


@pytest.mark.xfail(strict=True, reason=_NO_LABEL)
def test_every_state_is_named_as_the_core_specification_names_it():
    """A refusal that names a driver's state names it as the core specification does, never by
    its stored value: "current state: Not Signed Up", not "current state: NOT_SIGNED_UP"."""
    assert {state: state.label for state in DriverState} == {
        DriverState.NOT_SIGNED_UP: "Not Signed Up",
        DriverState.PENDING_SIGNUP_COMPLETION: "Pending Signup Completion",
        DriverState.PENDING_ADMIN_APPROVAL: "Pending Admin Approval",
        DriverState.AWAITING_CORRECTION_PARAMETER: "Awaiting Correction Parameter",
        DriverState.PENDING_DRIVER_CORRECTION: "Pending Driver Correction",
        DriverState.UNASSIGNED: "Unassigned",
        DriverState.ASSIGNED: "Assigned",
    }


_NO_SIGNUP_OFF = "#624: the transition table takes no signup_off widening yet"


@pytest.mark.parametrize(
    "keywords, target, allowed",
    [
        pytest.param({"signup_off": True}, DriverState.ASSIGNED, True, id="signup-off-to-assigned",
                     marks=pytest.mark.xfail(strict=True, reason=_NO_SIGNUP_OFF)),
        pytest.param({}, DriverState.ASSIGNED, False, id="signup-on-to-assigned"),
        pytest.param({"signup_off": True}, DriverState.UNASSIGNED, False,
                     id="signup-off-to-unassigned",
                     marks=pytest.mark.xfail(strict=True, reason=_NO_SIGNUP_OFF)),
    ],
)
async def test_not_signed_up_to_assigned_is_allowed_only_with_signup_off(
    tmp_path, keywords, target, allowed
):
    """A driver who is Not Signed Up is placed straight to Assigned where the season runs with
    the signup module off, and only then: with signup on, an approved signup is the one way
    out of Not Signed Up towards a seat. The widening is Assigned alone: with signup off, Not
    Signed Up to Unassigned stays refused, writing nothing."""
    from leaguebot.core.models.driver_profile import InvalidDriverTransition
    from leaguebot.core.services.drivers.driver_service import write_transition
    from leaguebot.framework.db.database import get_connection, run_migrations

    path = str(tmp_path / "signup_off.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (9, '99', 'NOT_SIGNED_UP')"
        )
        await db.commit()
        if allowed:
            await write_transition(db, 9, DriverState.NOT_SIGNED_UP, target, **keywords)
        else:
            with pytest.raises(InvalidDriverTransition, match="not allowed"):
                await write_transition(db, 9, DriverState.NOT_SIGNED_UP, target, **keywords)
        await db.commit()
        cursor = await db.execute("SELECT current_state FROM driver_profiles WHERE id = 9")
        assert (await cursor.fetchone())[0] == (target.value if allowed else "NOT_SIGNED_UP")
