"""The set of driver states, and the rule that every one of them is reachable.

Issue #221: ``SEASON_BANNED`` and ``LEAGUE_BANNED`` sat in the enum and in the transition
table for months while no command could put a driver into either, so the specifications
described a state no league could ever see. These tests pin the seven that survive.
"""
from __future__ import annotations

from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.driver_service import ALLOWED_TRANSITIONS


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
