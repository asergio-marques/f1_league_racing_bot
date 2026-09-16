"""`/driver` — reassign, assign, unassign and sack.

Issue #208. `driver_cog.py` was the worst-covered file in the signup module at 20.5%, and it
is the command group a league manager uses to move drivers between seats by hand. Nothing
under `tests/` invoked any of its four commands.

Three of them — assign, unassign and sack — walk the same ladder of refusals before doing any
work: is there a season, is it still mutable, does the division exist, does the driver have a
profile. **Each rung is tested for each command rather than once for one of them**, because
the ladder is copied between the three rather than shared, which is exactly the arrangement
where one command quietly loses a rung. A single parametrised sweep would not do: the commands
take different arguments and stop at different rungs (`sack` has no division, and tolerates
there being no season at all).

**`sack` tolerating no season is the rule most easily lost.** A driver can be sacked between
seasons — that is when a league clears out drivers who have left — so `season` being `None` is
a legitimate state, passed to `sack_driver` as a `None` season id. The other two commands
refuse outright. `test_a_driver_can_be_sacked_with_no_season_at_all` holds it; a reader
making the four commands consistent would break it and no other test would object.

The archived-season refusal is pinned for all three mutating commands together, because
`SeasonImmutableError` is what stops a completed season being edited after the fact — the
championship's record changing under results already published.

These tests drive the command bodies past their permission decorators with
`tests/support/undecorate.py`; the guards themselves are covered by the channel-guard tests.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.driver_cog import DriverCog  # noqa: E402
from services.season_service import SeasonImmutableError  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 8808
SEASON_ID = 3
DIVISION_ID = 11
PROFILE_ID = 55


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _member(user_id: int, name: str) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    member.display_name = name
    return member


def _season(status: str = "ACTIVE") -> SimpleNamespace:
    return SimpleNamespace(id=SEASON_ID, status=SimpleNamespace(value=status))


def _make_cog(
    *,
    season: object | None = None,
    mutable: bool = True,
    division: tuple[int, str] | None = (DIVISION_ID, "Division 1"),
    profile: object | None = SimpleNamespace(
        id=PROFILE_ID,
        current_state=SimpleNamespace(value="ACTIVE"),
        former_driver=False,
    ),
) -> DriverCog:
    """A `DriverCog` whose services answer as the test requires.

    The defaults are the happy path: an active, mutable season, a division that resolves and
    a driver who has a profile. Each test knocks out the one rung it is about.
    """
    bot = MagicMock()

    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.season_service.get_active_season = AsyncMock(return_value=season)
    bot.season_service.assert_season_mutable = AsyncMock(
        side_effect=None if mutable else SeasonImmutableError("archived")
    )

    bot.placement_service = MagicMock()
    bot.placement_service.resolve_division = AsyncMock(return_value=division)
    bot.placement_service.assign_driver = AsyncMock(
        return_value={"team_name": "Alpha", "division_name": "Division 1"}
    )
    bot.placement_service.unassign_driver = AsyncMock(
        return_value={"team_name": "Alpha", "division_name": "Division 1"}
    )
    bot.placement_service.sack_driver = AsyncMock(return_value=None)

    bot.driver_service = MagicMock()
    bot.driver_service.get_profile = AsyncMock(return_value=profile)
    bot.driver_service.reassign_user_id = AsyncMock(
        return_value=SimpleNamespace(
            current_state=SimpleNamespace(value="ACTIVE"), former_driver=False
        )
    )

    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return DriverCog(bot)


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = _member(999, "Manager")
    interaction.guild = MagicMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction: MagicMock) -> str:
    """Whatever the command told the manager, by whichever route it answered."""
    parts = [
        str(call.args[0])
        for call in interaction.followup.send.await_args_list
        + interaction.response.send_message.await_args_list
        if call.args
    ]
    return "\n".join(parts)


async def _assign(cog, interaction, *, division: str = "Division 1", team: str = "Alpha"):
    await undecorate(DriverCog.assign)(cog, interaction, _member(1, "Driver"), division, team)


async def _unassign(cog, interaction, *, division: str = "Division 1"):
    await undecorate(DriverCog.unassign)(cog, interaction, _member(1, "Driver"), division)


async def _sack(cog, interaction):
    await undecorate(DriverCog.sack)(cog, interaction, _member(1, "Driver"))


#: The three commands that change a placement, each with a caller taking only the cog and
#: interaction. Used where a rung of the refusal ladder is common to all three.
MUTATING = [
    pytest.param(_assign, id="assign"),
    pytest.param(_unassign, id="unassign"),
    pytest.param(_sack, id="sack"),
]


# ---------------------------------------------------------------------------
# /driver reassign
# ---------------------------------------------------------------------------


async def test_reassign_accepts_a_mentioned_old_user(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), None
    )

    cog.bot.driver_service.reassign_user_id.assert_awaited_once()
    assert cog.bot.driver_service.reassign_user_id.await_args.args[1] == "1"
    assert "re-keyed successfully" in _replied(interaction)


async def test_reassign_accepts_a_raw_snowflake_for_the_old_user(tmp_path):
    """The account being replaced has often left the server, so it cannot be mentioned —
    the raw id is the only way to name it, and is the reason the parameter exists."""
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(cog, interaction, _member(2, "New"), None, " 4242 ")

    assert cog.bot.driver_service.reassign_user_id.await_args.args[1] == "4242"


async def test_reassign_with_neither_old_user_nor_id_is_refused(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(cog, interaction, _member(2, "New"), None, None)

    assert "must supply either" in _replied(interaction)
    cog.bot.driver_service.reassign_user_id.assert_not_awaited()


async def test_a_mentioned_old_user_wins_over_a_raw_id(tmp_path):
    """Both may be given. The mention is the more specific of the two and is taken."""
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), "4242"
    )

    assert cog.bot.driver_service.reassign_user_id.await_args.args[1] == "1"


async def test_a_refused_reassignment_is_reported_not_raised(tmp_path):
    cog = _make_cog()
    cog.bot.driver_service.reassign_user_id = AsyncMock(
        side_effect=ValueError("No profile for that account.")
    )
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), None
    )

    assert "No profile for that account." in _replied(interaction)


# ---------------------------------------------------------------------------
# The refusal ladder, command by command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", [pytest.param(_assign, id="assign"),
                                     pytest.param(_unassign, id="unassign")])
async def test_no_season_refuses_a_placement_change(command):
    cog = _make_cog(season=None)
    interaction = _interaction()

    await command(cog, interaction)

    assert "No season in SETUP or ACTIVE" in _replied(interaction)
    cog.bot.placement_service.assign_driver.assert_not_awaited()
    cog.bot.placement_service.unassign_driver.assert_not_awaited()


@pytest.mark.parametrize("command", MUTATING)
async def test_an_archived_season_refuses_every_change(command):
    """A COMPLETED season is the championship's record. Editing it would move drivers
    under results already published."""
    cog = _make_cog(season=_season("COMPLETED"), mutable=False)
    interaction = _interaction()

    await command(cog, interaction)

    assert "archived" in _replied(interaction)
    cog.bot.placement_service.assign_driver.assert_not_awaited()
    cog.bot.placement_service.unassign_driver.assert_not_awaited()
    cog.bot.placement_service.sack_driver.assert_not_awaited()


@pytest.mark.parametrize("command", [pytest.param(_assign, id="assign"),
                                     pytest.param(_unassign, id="unassign")])
async def test_an_unknown_division_is_refused(command):
    cog = _make_cog(season=_season(), division=None)
    interaction = _interaction()

    await command(cog, interaction, division="Division 9")

    assert "Division 9" in _replied(interaction)
    assert "not found" in _replied(interaction)


@pytest.mark.parametrize("command", MUTATING)
async def test_a_user_with_no_driver_profile_is_refused(command):
    """Someone of the server who never signed up. The command names them rather than
    reporting a bare failure, because the manager typed a mention and needs to know which."""
    cog = _make_cog(season=_season(), profile=None)
    interaction = _interaction()

    await command(cog, interaction)

    assert "No driver profile found" in _replied(interaction)


# ---------------------------------------------------------------------------
# /driver assign
# ---------------------------------------------------------------------------


async def test_a_driver_is_assigned_to_the_named_team_and_division(tmp_path):
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await _assign(cog, interaction)

    kwargs = cog.bot.placement_service.assign_driver.await_args.kwargs
    assert kwargs["driver_profile_id"] == PROFILE_ID
    assert kwargs["division_id"] == DIVISION_ID
    assert kwargs["team_name"] == "Alpha"
    assert kwargs["season_id"] == SEASON_ID
    assert "Assigned" in _replied(interaction)


async def test_the_season_state_is_passed_to_the_placement(tmp_path):
    """Placement behaves differently in SETUP and ACTIVE — a seat filled during setup is
    part of the lineup, one filled mid-season is a transfer — so the state travels with
    the call rather than being re-derived there."""
    cog = _make_cog(season=_season("SETUP"))
    interaction = _interaction()

    await _assign(cog, interaction)

    assert cog.bot.placement_service.assign_driver.await_args.kwargs["season_state"] == "SETUP"


async def test_a_refused_assignment_is_reported_not_raised(tmp_path):
    """`assign_driver` refuses a full team, a driver already seated, and a team that would
    outgrow the lineup graphic. All of them must reach the manager as a message."""
    cog = _make_cog(season=_season())
    cog.bot.placement_service.assign_driver = AsyncMock(
        side_effect=ValueError("Team Alpha is full.")
    )
    interaction = _interaction()

    await _assign(cog, interaction)

    assert "Team Alpha is full." in _replied(interaction)


async def test_a_successful_assignment_is_logged(tmp_path):
    """The log is the league's audit trail for who moved whom."""
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await _assign(cog, interaction)

    cog.bot.output_router.post_log.assert_awaited_once()
    logged = cog.bot.output_router.post_log.await_args.args[1]
    assert "/driver assign" in logged
    assert "Alpha" in logged


# ---------------------------------------------------------------------------
# /driver unassign
# ---------------------------------------------------------------------------


async def test_a_driver_is_unassigned_from_the_named_division(tmp_path):
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await _unassign(cog, interaction)

    kwargs = cog.bot.placement_service.unassign_driver.await_args.kwargs
    assert kwargs["driver_profile_id"] == PROFILE_ID
    assert kwargs["division_id"] == DIVISION_ID
    assert "Removed" in _replied(interaction)


async def test_unassigning_names_the_team_the_driver_left(tmp_path):
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await _unassign(cog, interaction)

    assert "Alpha" in _replied(interaction)


async def test_unassigning_a_driver_who_held_no_seat_omits_the_team(tmp_path):
    """A driver of the division with no seat — a reserve who was never placed. Saying
    "from ****" would read as a missing name rather than as no team."""
    cog = _make_cog(season=_season())
    cog.bot.placement_service.unassign_driver = AsyncMock(
        return_value={"team_name": None, "division_name": "Division 1"}
    )
    interaction = _interaction()

    await _unassign(cog, interaction)

    replied = _replied(interaction)
    assert "Division 1" in replied
    assert "from ****" not in replied


async def test_a_refused_unassignment_is_reported_not_raised(tmp_path):
    cog = _make_cog(season=_season())
    cog.bot.placement_service.unassign_driver = AsyncMock(
        side_effect=ValueError("Driver is not in that division.")
    )
    interaction = _interaction()

    await _unassign(cog, interaction)

    assert "not in that division" in _replied(interaction)


# ---------------------------------------------------------------------------
# /driver sack
# ---------------------------------------------------------------------------


async def test_a_driver_is_sacked(tmp_path):
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await _sack(cog, interaction)

    kwargs = cog.bot.placement_service.sack_driver.await_args.kwargs
    assert kwargs["driver_profile_id"] == PROFILE_ID
    assert kwargs["season_id"] == SEASON_ID
    assert "has been sacked" in _replied(interaction)


async def test_a_driver_can_be_sacked_with_no_season_at_all(tmp_path):
    """Between seasons is when a league clears out drivers who have left, so no season is
    a legitimate state here — unlike assign and unassign, which refuse. The season id
    passed through is `None`, and a reader making the four commands consistent would take
    this away."""
    cog = _make_cog(season=None)
    interaction = _interaction()

    await _sack(cog, interaction)

    cog.bot.placement_service.sack_driver.assert_awaited_once()
    assert cog.bot.placement_service.sack_driver.await_args.kwargs["season_id"] is None


async def test_the_mutability_check_is_skipped_when_there_is_no_season(tmp_path):
    """There is nothing to assert mutability of. Calling it with `None` would raise."""
    cog = _make_cog(season=None)
    interaction = _interaction()

    await _sack(cog, interaction)

    cog.bot.season_service.assert_season_mutable.assert_not_awaited()


async def test_a_refused_sacking_is_reported_not_raised(tmp_path):
    cog = _make_cog(season=_season())
    cog.bot.placement_service.sack_driver = AsyncMock(
        side_effect=ValueError("Driver is already NOT_SIGNED_UP.")
    )
    interaction = _interaction()

    await _sack(cog, interaction)

    assert "already NOT_SIGNED_UP" in _replied(interaction)


async def test_a_successful_sacking_is_logged(tmp_path):
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await _sack(cog, interaction)

    assert "/driver sack" in cog.bot.output_router.post_log.await_args.args[1]
