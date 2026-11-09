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

An assign, unassign or sack that clears the ladder asks the change queue for a
`core.driver_assign`, `core.driver_unassign` or `core.driver_sack` (#283), whose save, roles,
lineups, reply and line are tested against a real database in `test_driver_assign.py`,
`test_driver_unassign.py` and `test_sack_driver.py`, with the refusals it makes at the ask and as
it starts; here, only what the command asks of the queue. So with a reassign, asked of the queue
as a `core.driver_reassign` whose save, roles, portrait, reply and line are tested in
`test_driver_reassign.py`, and its stage and parameter refusals in `test_reassign_stage_gate.py`.

The archived-season refusal is pinned for all three mutating commands together, because
`SeasonImmutableError` is what stops a completed season being edited after the fact — the
championship's record changing under results already published.

These tests drive the command bodies past their permission decorators with
`tests/support/undecorate.py`; the guards themselves are covered by the channel-guard tests.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.driver_cog import DriverCog
from leaguebot.core.services.season_service import SeasonImmutableError
from tests.support.teams import resolves_as_typed
from tests.support.undecorate import undecorate

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
    (issue #220); the stage gates have tests of their own in test_placement_committed.py.

    `/driver reassign` is limited to the same two placement stages (issue #224), which is why
    the tests below hand it one of these rather than relying on `_make_cog`'s seasonless
    default. Its own stage gate is pinned in `test_reassign_stage_gate.py`."""
    from leaguebot.core.models.season import SeasonStage

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

    bot.team_service.resolve_division_team = resolves_as_typed()

    bot.placement_service = MagicMock()
    bot.placement_service.resolve_division = AsyncMock(return_value=division)
    bot.change_queue.ask = AsyncMock()

    bot.driver_service = MagicMock()
    bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    bot.driver_service.get_profile = AsyncMock(return_value=profile)

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
    from leaguebot.core.models.season import SeasonStage

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


_ON_THE_QUEUE = "#283: /driver reassign is not yet asked of the change queue"


async def test_reassign_with_neither_old_user_nor_id_is_refused(tmp_path):
    """The season is in Placements. The league manager runs `/driver reassign` onto account 2,
    naming no old account, neither by mention nor by id. They are told they must supply one, and
    nothing is asked of the queue."""
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(cog, interaction, _member(2, "New"), None, None)

    assert "must supply either" in _replied(interaction)
    cog.bot.change_queue.ask.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_mentioned_old_user_wins_over_a_raw_id(tmp_path):
    """Both may be given. The mention is the more specific of the two and is taken: the season is
    in Placements, the league manager runs `/driver reassign` onto account 2 mentioning account 1
    and typing 4242, and the queue is asked for one `core.driver_reassign` of account 1's driver
    onto account 2."""
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await undecorate(DriverCog.reassign)(
        cog, interaction, _member(2, "New"), _member(1, "Old"), "4242"
    )

    cog.bot.change_queue.ask.assert_awaited_once()
    kind, payload = cog.bot.change_queue.ask.await_args.args[:2]
    assert kind == "core.driver_reassign"
    assert (str(payload["old_user_id"]), str(payload["new_user_id"])) == ("1", "2")


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
    assert cog.bot.change_queue.method_calls == []


@pytest.mark.parametrize("command", [pytest.param(_assign, id="assign"),
                                     pytest.param(_unassign, id="unassign")])
async def test_an_archived_season_refuses_every_change(command):
    """A COMPLETED season is the championship's record. Editing it would move drivers
    under results already published."""
    cog = _make_cog(season=_season("COMPLETED"), mutable=False)
    interaction = _interaction()

    await command(cog, interaction)

    assert "archived" in _replied(interaction)
    assert cog.bot.change_queue.method_calls == []


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
    """The season (3) is in placements. A league manager assigns Driver (account 1, profile 55)
    to Alpha in Division 1 (11). The command asks the queue for one `core.driver_assign` of
    Driver's profile, Division 1 and Alpha, in season 3, and says and logs nothing itself: the
    reply and the line are the change's (`test_driver_assign.py`)."""
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await _assign(cog, interaction)

    cog.bot.change_queue.ask.assert_awaited_once()
    kind, payload = cog.bot.change_queue.ask.await_args.args[:2]
    assert kind == "core.driver_assign"
    assert payload["driver_profile_id"] == PROFILE_ID
    assert payload["division_id"] == DIVISION_ID
    assert payload["team_name"] == "Alpha"
    assert payload["season_id"] == SEASON_ID
    assert _replied(interaction) == ""
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_assign_seats_the_driver_in_the_team_its_shorthand_names(tmp_path):
    """A team is typed by its shorthand (#381), resolved in the division before anything is
    asked; the assignment is asked of the queue under the shorthand as the team holds it."""
    from leaguebot.core.services.team_service import TeamReference

    cog = _make_cog(season=_season())
    cog.bot.team_service.resolve_division_team = AsyncMock(
        return_value=TeamReference(team={"id": 7, "name": "ALP", "full_name": "Alpha Racing"})
    )
    interaction = _interaction()

    await _assign(cog, interaction, team="alp")

    cog.bot.team_service.resolve_division_team.assert_awaited_once_with(DIVISION_ID, "alp")
    assert cog.bot.change_queue.ask.await_args.args[1]["team_name"] == "ALP"


async def test_assign_refuses_a_team_the_division_does_not_have(tmp_path):
    from leaguebot.core.services.team_service import TeamReference

    cog = _make_cog(season=_season())
    cog.bot.team_service.resolve_division_team = AsyncMock(
        return_value=TeamReference(refusal="`@everyone` is not a team.")
    )
    interaction = _interaction()

    await _assign(cog, interaction, team="@everyone")

    assert "`@everyone` is not a team." in _replied(interaction)
    assert cog.bot.change_queue.method_calls == []


# ---------------------------------------------------------------------------
# /driver unassign
# ---------------------------------------------------------------------------


async def test_a_driver_is_unassigned_from_the_named_division(tmp_path):
    """The season (3) is in placements. A league manager unassigns Driver (account 1, profile 55)
    from Division 1 (11). The command asks the queue for one `core.driver_unassign` of Driver's
    profile and Division 1, in season 3, and says and logs nothing itself: the reply and the line
    are the change's (`test_driver_unassign.py`)."""
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await _unassign(cog, interaction)

    cog.bot.change_queue.ask.assert_awaited_once()
    kind, payload = cog.bot.change_queue.ask.await_args.args[:2]
    assert kind == "core.driver_unassign"
    assert payload["driver_profile_id"] == PROFILE_ID
    assert payload["division_id"] == DIVISION_ID
    assert payload["season_id"] == SEASON_ID
    assert _replied(interaction) == ""
    cog.bot.output_router.post_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# /driver sack
# ---------------------------------------------------------------------------


async def test_a_sack_is_asked_of_the_queue(tmp_path):
    """The season (3) is ongoing. A league admin sacks Driver (account 1, profile 55). The
    command asks the queue for one `core.driver_sack` of Driver's profile and account, named
    "Driver", in season 3, and says and logs nothing itself: the reply and the line are the
    change's (`test_sack_driver.py`)."""
    cog = _make_cog(season=_season())
    interaction = _interaction()

    await _sack(cog, interaction)

    cog.bot.change_queue.ask.assert_awaited_once()
    kind, payload = cog.bot.change_queue.ask.await_args.args[:2]
    assert kind == "core.driver_sack"
    assert payload["driver_profile_id"] == PROFILE_ID
    assert payload["season_id"] == SEASON_ID
    assert payload["discord_user_id"] == "1"
    assert payload["driver_name"] == "Driver"
    assert _replied(interaction) == ""
    cog.bot.output_router.post_log.assert_not_awaited()


@pytest.mark.parametrize("stage", [None, "PLACEMENTS", "PENDING_COMPLETION"],
                         ids=["no season", "PLACEMENTS", "PENDING_COMPLETION"])
async def test_a_sack_is_refused_outside_the_ongoing_stages(tmp_path, stage):
    """Issue #220: sacking is for the ongoing stages. Between seasons the season's end has
    already returned every driver to Not Signed Up; before placements are confirmed nobody is
    committed; in Pending completion only the season's completion remains."""
    cog = _make_cog(season=None if stage is None else _season(stage=stage))
    interaction = _interaction()

    await undecorate(DriverCog.sack)(cog, interaction, _member(1, "Driver"))

    assert "only while the season is ongoing" in _replied(interaction)
    assert cog.bot.change_queue.method_calls == []
