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

**Each command answers to the season's stage** (issue #220): assign and unassign to the
placing stages, sack to the ongoing ones. Between seasons nothing is sacked — the season's end
has already returned every driver to Not Signed Up.

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


def _season(status: str = "ACTIVE", stage: str = "PLACEMENTS") -> SimpleNamespace:
    """A season. Its stage defaults to Placements, where assign and unassign are available
    (issue #220); the stage gates have tests of their own in test_placement_committed.py."""
    from models.season import SeasonStage

    return SimpleNamespace(
        id=SEASON_ID, status=SimpleNamespace(value=status), stage=SeasonStage(stage)
    )


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
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
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
    bot.placement_service.move_driver_roles = AsyncMock(return_value=[])
    bot.wizard_service.move_held_channel = AsyncMock(return_value=[])

    bot.driver_service = MagicMock()
    bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    bot.driver_service.get_profile = AsyncMock(return_value=profile)
    # The account named as old is taken to be the driver's current one, which it replaces.
    bot.driver_service.reassign_user_id = AsyncMock(
        side_effect=lambda old, new, *_a: SimpleNamespace(
            profile=SimpleNamespace(
                id=PROFILE_ID,
                current_state=SimpleNamespace(value="ACTIVE"),
                former_driver=False,
            ),
            replaced_account=old,
            accounts=sorted([old, new]),
            switched_back=False,
            merged_accounts=None,
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
    """Sack, with the season ongoing — the only stage a sack is available in (issue #220)."""
    from models.season import SeasonStage

    season = cog.bot.season_service.get_confirmed_season.return_value
    if season is not None:
        cog.bot.season_service.get_confirmed_season.return_value = SimpleNamespace(
            **{**vars(season), "stage": SeasonStage.ONGOING}
        )
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
    assert cog.bot.driver_service.reassign_user_id.await_args.args[0] == "1"
    assert "given a new account" in _replied(interaction)


async def test_reassign_accepts_a_raw_snowflake_for_the_old_user(tmp_path):
    """The account being replaced has often left the server, so it cannot be mentioned —
    the raw id is the only way to name it, and is the reason the parameter exists."""
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(cog, interaction, _member(2, "New"), None, " 4242 ")

    assert cog.bot.driver_service.reassign_user_id.await_args.args[0] == "4242"


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

    assert cog.bot.driver_service.reassign_user_id.await_args.args[0] == "1"


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
# The portrait of the account left behind (issue #222)
# ---------------------------------------------------------------------------
#
# A portrait is a cache of one account's own profile picture, so nothing of it is carried by a
# re-key — the new account has a picture of its own. The old file is removed instead, and the
# directory is the league's own, so these tests build one under `tmp_path` and never resolve
# the configured `resources/league/` tree.


def _with_portraits(cog, directory, monkeypatch, *, remover=None):
    """Point *cog* at a configured driver directory and return the remover it will call."""
    from services import driver_portrait_service, image_render_service

    cog.bot.db_path = "db.sqlite"
    cog.bot.image_config_service = MagicMock()
    cog.bot.image_config_service.get_config = AsyncMock(
        return_value=SimpleNamespace(driver_image_directory=str(directory))
    )
    monkeypatch.setattr(
        image_render_service,
        "resolve_configured_directories",
        lambda *a, **k: ({"driver": directory}, {}),
    )
    remover = remover or AsyncMock(return_value=True)
    monkeypatch.setattr(driver_portrait_service, "remove_portrait", remover)
    return remover


async def test_reassign_removes_the_portrait_of_the_account_left_behind(
    tmp_path, monkeypatch
):
    directory = tmp_path / "drivers"
    directory.mkdir()
    cog = _make_cog()
    remover = _with_portraits(cog, directory, monkeypatch)
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(cog, interaction, _member(2, "New"), None, "4242")

    remover.assert_awaited_once_with("db.sqlite", "4242", directory)
    assert "given a new account" in _replied(interaction)


async def test_reassign_leaves_the_portrait_where_no_directory_resolves(
    tmp_path, monkeypatch
):
    """A row taken without its file would disown a portrait the bot wrote, after which the
    bot would never overwrite its own leftover. Both are left alone instead."""
    from services import image_render_service

    directory = tmp_path / "drivers"
    directory.mkdir()
    cog = _make_cog()
    remover = _with_portraits(cog, directory, monkeypatch)
    monkeypatch.setattr(
        image_render_service,
        "resolve_configured_directories",
        lambda *a, **k: ({}, {"driver": "outside the project root"}),
    )
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(cog, interaction, _member(2, "New"), None, "4242")

    remover.assert_not_awaited()
    assert "given a new account" in _replied(interaction)


async def test_reassign_leaves_the_portrait_where_the_league_has_no_image_config(
    tmp_path, monkeypatch
):
    directory = tmp_path / "drivers"
    directory.mkdir()
    cog = _make_cog()
    remover = _with_portraits(cog, directory, monkeypatch)
    cog.bot.image_config_service.get_config = AsyncMock(return_value=None)
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(cog, interaction, _member(2, "New"), None, "4242")

    remover.assert_not_awaited()


async def test_a_portrait_that_cannot_be_removed_does_not_fail_the_re_key(
    tmp_path, monkeypatch
):
    """The re-key is committed by the time the portrait is touched, so a read-only directory
    must not turn a successful command into a reported failure."""
    directory = tmp_path / "drivers"
    directory.mkdir()
    cog = _make_cog()
    _with_portraits(
        cog, directory, monkeypatch, remover=AsyncMock(side_effect=OSError("read-only"))
    )
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(cog, interaction, _member(2, "New"), None, "4242")

    assert "given a new account" in _replied(interaction)
    cog.bot.output_router.post_log.assert_awaited()


# ---------------------------------------------------------------------------
# The refusal ladder, command by command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", [pytest.param(_assign, id="assign"),
                                     pytest.param(_unassign, id="unassign")])
async def test_no_season_refuses_a_placement_change(command):
    cog = _make_cog(season=None)
    interaction = _interaction()

    await command(cog, interaction)

    assert "available only while the season is in placements" in _replied(interaction)
    cog.bot.placement_service.assign_driver.assert_not_awaited()
    cog.bot.placement_service.unassign_driver.assert_not_awaited()


@pytest.mark.parametrize("command", [pytest.param(_assign, id="assign"),
                                     pytest.param(_unassign, id="unassign")])
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


@pytest.mark.parametrize(
    "stage, uncommitted_only", [("PLACEMENTS", False), ("ONGOING_PLACEMENTS", True)]
)
async def test_an_assignment_is_made_uncommitted(stage, uncommitted_only):
    """Issue #220: a placement stands outside the championship until placements are confirmed,
    and mid-season the command is kept to drivers who hold no confirmed placement."""
    cog = _make_cog(season=_season("SETUP", stage))
    interaction = _interaction()

    await _assign(cog, interaction)

    kwargs = cog.bot.placement_service.assign_driver.await_args.kwargs
    assert kwargs["committed"] is False
    assert kwargs["uncommitted_only"] is uncommitted_only


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
    logged = cog.bot.output_router.post_log.await_args.args[0]
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


async def test_a_sack_is_refused_with_no_season(tmp_path):
    """Issue #220: sacking is for the ongoing stages. Between seasons the season's end has
    already returned every driver to Not Signed Up."""
    cog = _make_cog(season=None)
    interaction = _interaction()

    await undecorate(DriverCog.sack)(cog, interaction, _member(1, "Driver"))

    assert "only while the season is ongoing" in _replied(interaction)
    cog.bot.placement_service.sack_driver.assert_not_awaited()


@pytest.mark.parametrize("stage", ["PLACEMENTS", "PENDING_COMPLETION"])
async def test_a_sack_is_refused_outside_the_ongoing_stages(tmp_path, stage):
    """Before placements are confirmed nobody is committed; in Pending completion only the
    season's completion remains."""
    cog = _make_cog(season=_season(stage=stage))
    interaction = _interaction()

    await undecorate(DriverCog.sack)(cog, interaction, _member(1, "Driver"))

    assert "only while the season is ongoing" in _replied(interaction)
    cog.bot.placement_service.sack_driver.assert_not_awaited()


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

    assert "/driver sack" in cog.bot.output_router.post_log.await_args.args[0]


async def test_reassign_reports_the_current_and_past_accounts(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), None
    )

    assert "Current account : <@2>" in _replied(interaction)
    assert "Past accounts   : <@1>" in _replied(interaction)


async def test_a_switch_back_is_reported_as_one(tmp_path):
    cog = _make_cog()
    outcome = await cog.bot.driver_service.reassign_user_id("1", "2")
    outcome.switched_back = True
    cog.bot.driver_service.reassign_user_id = AsyncMock(return_value=outcome)
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), None
    )

    assert "switched back to a past account" in _replied(interaction)


async def test_reassign_moves_the_roles_to_the_new_current_account(tmp_path):
    """Issue #243: from the account replaced to the one made current."""
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), None
    )

    args = cog.bot.placement_service.move_driver_roles.await_args.args
    assert args == (interaction.guild, PROFILE_ID, "1", "2")


async def test_roles_discord_would_not_move_are_reported_and_the_reassign_stands(tmp_path):
    """E44."""
    cog = _make_cog()
    cog.bot.placement_service.move_driver_roles = AsyncMock(
        return_value=["the roles could not be given to <@2>: Missing Permissions"]
    )
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), None
    )

    assert "given a new account" in _replied(interaction)
    assert "Missing Permissions" in _replied(interaction)
    assert "not done: the roles could not be given" in cog.bot.output_router.post_log.await_args.args[0]


async def test_reassign_moves_a_held_signup_channel_to_the_new_current_account(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), None
    )

    cog.bot.wizard_service.move_held_channel.assert_awaited_once_with(
        "1", "2", interaction.guild
    )


async def test_a_merge_is_reported_as_one(tmp_path):
    cog = _make_cog()
    outcome = await cog.bot.driver_service.reassign_user_id("1", "2")
    outcome.merged_accounts = ["2", "3"]
    cog.bot.driver_service.reassign_user_id = AsyncMock(return_value=outcome)
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), None
    )

    assert "merged with the driver on <@2>, <@3>" in _replied(interaction)
