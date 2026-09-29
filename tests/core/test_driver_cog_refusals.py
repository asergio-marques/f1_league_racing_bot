"""Every refusal of the `/driver` commands is recorded in the log channel (#482).

The rule is the core specification's "The record of what changed": a refusal is recorded as one
line naming the member, what was refused and why. Each case below turns a league manager away at
one of the driver cog's own checks, and expects today's reply, seen by them alone, and exactly one
standard refusal line. `/driver reassign`'s stage refusal goes through the shared season gate,
which already records it, and is not a case here.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.driver_cog import DriverCog
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.season_service import SeasonImmutableError
from leaguebot.core.services.team_service import TeamReference
from tests.support.teams import resolves_as_typed
from tests.support.undecorate import undecorate

MANAGER_ID = 4242
DRIVER_ID = 5151
PROFILE_ID = 55
DIVISIONS = {"Pro": (21, "Pro"), "Academy": (22, "Academy")}

#: The marks a reply may open with, which the refusal line leaves out of its reason.
_REPLY_MARKS = ("❌", "⛔", "⚠️", "⚠", "ℹ️", "ℹ", "⏳", "⛓", "⏸️", "⏸")


def _reason(reply: str) -> str:
    """The reason a refusal line gives: the reply's first line, without its opening mark."""
    first = reply.strip().splitlines()[0].strip()
    for mark in _REPLY_MARKS:
        if first.startswith(mark):
            return first[len(mark):].lstrip("️").strip()
    return first


def _cog() -> DriverCog:
    """A driver cog whose every check passes, until a case makes one refuse."""
    cog = DriverCog.__new__(DriverCog)
    bot = MagicMock()
    bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=7, stage=SeasonStage.PLACEMENTS)
    )
    bot.season_service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=7, stage=SeasonStage.ONGOING)
    )
    bot.season_service.assert_season_mutable = AsyncMock()
    bot.placement_service.resolve_division = AsyncMock(
        side_effect=lambda season_id, name: DIVISIONS.get(name)
    )
    bot.team_service.resolve_division_team = resolves_as_typed()
    bot.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(id=PROFILE_ID, current_state=DriverState.UNASSIGNED)
    )
    bot.output_router.post_log = AsyncMock()
    cog.bot = bot
    return cog


def _interaction(cog: DriverCog, command: str) -> MagicMock:
    """`/<command>` run by the manager Alex, answering as Discord's does."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.client = cog.bot
    interaction.command.qualified_name = command
    interaction.user.id = MANAGER_ID
    interaction.user.display_name = "Alex"
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _driver() -> MagicMock:
    member = MagicMock()
    member.id = DRIVER_ID
    member.display_name = "Racer"
    member.remove_roles = AsyncMock()
    return member


def _not_placing(bot):
    bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=SimpleNamespace(id=7, stage=SeasonStage.ONGOING)
    )


def _archived(bot):
    bot.season_service.assert_season_mutable = AsyncMock(side_effect=SeasonImmutableError())


def _not_ongoing(bot):
    bot.season_service.get_confirmed_season = AsyncMock(return_value=None)


def _no_team(bot):
    bot.team_service.resolve_division_team = AsyncMock(
        return_value=TeamReference(refusal="No team of this division has the shorthand `X`.")
    )


def _no_profile(bot):
    bot.driver_service.get_profile = AsyncMock(return_value=None)


def _placed(bot):
    bot.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(id=PROFILE_ID, current_state=DriverState.ASSIGNED)
    )


def _service_refuses(name: str, reason: str):
    def setup(bot):
        service = bot.driver_service if name == "reassign_user_id" else bot.placement_service
        setattr(service, name, AsyncMock(side_effect=ValueError(reason)))
    return setup


def _past_account_gone(bot):
    bot.driver_service.current_account = AsyncMock(return_value="9999")


# case id → (command, how it is called, how the cog is made to refuse, a phrase of today's reply)
_CASES = {
    "reassign bad old_user_id": (
        "driver reassign",
        lambda cog, i: undecorate(DriverCog.reassign)(cog, i, _driver(), None, "not-a-number"),
        None,
        "`old_user_id` must be a numeric Discord user ID",
    ),
    "reassign no old account": (
        "driver reassign",
        lambda cog, i: undecorate(DriverCog.reassign)(cog, i, _driver(), None, None),
        None,
        "You must supply either `old_user`",
    ),
    "reassign refused by the service": (
        "driver reassign",
        lambda cog, i: undecorate(DriverCog.reassign)(cog, i, _driver(), _driver(), None),
        _service_refuses("reassign_user_id", "That account is already a driver's."),
        "That account is already a driver's.",
    ),
    "assign past account gone": (
        "driver assign",
        lambda cog, i: undecorate(DriverCog.assign)(cog, i, _driver(), "Pro", "Reserve"),
        _past_account_gone,
        "is a past account of a driver",
    ),
    "assign not placing": (
        "driver assign",
        lambda cog, i: undecorate(DriverCog.assign)(cog, i, _driver(), "Pro", "Reserve"),
        _not_placing,
        "`/driver assign` is available only while the season is in placements",
    ),
    "assign archived": (
        "driver assign",
        lambda cog, i: undecorate(DriverCog.assign)(cog, i, _driver(), "Pro", "Reserve"),
        _archived,
        "This season is archived",
    ),
    "assign unknown division": (
        "driver assign",
        lambda cog, i: undecorate(DriverCog.assign)(cog, i, _driver(), "Nowhere", "Reserve"),
        None,
        "Division **Nowhere** not found",
    ),
    "assign unknown team": (
        "driver assign",
        lambda cog, i: undecorate(DriverCog.assign)(cog, i, _driver(), "Pro", "X"),
        _no_team,
        "No team of this division has the shorthand `X`.",
    ),
    "assign no profile": (
        "driver assign",
        lambda cog, i: undecorate(DriverCog.assign)(cog, i, _driver(), "Pro", "Reserve"),
        _no_profile,
        "No driver profile found for **Racer**",
    ),
    "assign refused by the service": (
        "driver assign",
        lambda cog, i: undecorate(DriverCog.assign)(cog, i, _driver(), "Pro", "Reserve"),
        _service_refuses("assign_driver", "**Reserve** has no available seats."),
        "**Reserve** has no available seats.",
    ),
    "unassign not placing": (
        "driver unassign",
        lambda cog, i: undecorate(DriverCog.unassign)(cog, i, _driver(), "Pro"),
        _not_placing,
        "`/driver unassign` is available only while the season is in placements",
    ),
    "unassign archived": (
        "driver unassign",
        lambda cog, i: undecorate(DriverCog.unassign)(cog, i, _driver(), "Pro"),
        _archived,
        "This season is archived",
    ),
    "unassign unknown division": (
        "driver unassign",
        lambda cog, i: undecorate(DriverCog.unassign)(cog, i, _driver(), "Nowhere"),
        None,
        "Division **Nowhere** not found",
    ),
    "unassign no profile": (
        "driver unassign",
        lambda cog, i: undecorate(DriverCog.unassign)(cog, i, _driver(), "Pro"),
        _no_profile,
        "No driver profile found for **Racer**",
    ),
    "unassign refused by the service": (
        "driver unassign",
        lambda cog, i: undecorate(DriverCog.unassign)(cog, i, _driver(), "Pro"),
        _service_refuses("unassign_driver", "Racer holds no seat in Pro."),
        "Racer holds no seat in Pro.",
    ),
    "move not ongoing": (
        "driver move",
        lambda cog, i: undecorate(DriverCog.move)(cog, i, _driver(), "Pro", "Reserve", None),
        _not_ongoing,
        "`/driver move` is available only while the season is ongoing",
    ),
    "move from unknown division": (
        "driver move",
        lambda cog, i: undecorate(DriverCog.move)(cog, i, _driver(), "Nowhere", "Reserve", None),
        None,
        "Division **Nowhere** not found",
    ),
    "move into unknown division": (
        "driver move",
        lambda cog, i: undecorate(DriverCog.move)(cog, i, _driver(), "Pro", "Reserve", "Nowhere"),
        None,
        "Division **Nowhere** not found",
    ),
    "move unknown team": (
        "driver move",
        lambda cog, i: undecorate(DriverCog.move)(cog, i, _driver(), "Pro", "X", None),
        _no_team,
        "No team of this division has the shorthand `X`.",
    ),
    "move no profile": (
        "driver move",
        lambda cog, i: undecorate(DriverCog.move)(cog, i, _driver(), "Pro", "Reserve", None),
        _no_profile,
        "No driver profile found for **Racer**",
    ),
    "move refused by the service": (
        "driver move",
        lambda cog, i: undecorate(DriverCog.move)(cog, i, _driver(), "Pro", "Reserve", None),
        _service_refuses("move_driver", "**Reserve** in this division has no available seats."),
        "**Reserve** in this division has no available seats.",
    ),
    "release not ongoing": (
        "driver release",
        lambda cog, i: undecorate(DriverCog.release)(cog, i, _driver(), "Pro"),
        _not_ongoing,
        "`/driver release` is available only while the season is ongoing",
    ),
    "release unknown division": (
        "driver release",
        lambda cog, i: undecorate(DriverCog.release)(cog, i, _driver(), "Nowhere"),
        None,
        "Division **Nowhere** not found",
    ),
    "release no profile": (
        "driver release",
        lambda cog, i: undecorate(DriverCog.release)(cog, i, _driver(), "Pro"),
        _no_profile,
        "No driver profile found for **Racer**",
    ),
    "release refused by the service": (
        "driver release",
        lambda cog, i: undecorate(DriverCog.release)(cog, i, _driver(), "Pro"),
        _service_refuses("release_driver", "That is the driver's only seat."),
        "That is the driver's only seat.",
    ),
    "reject not placing": (
        "driver reject",
        lambda cog, i: undecorate(DriverCog.reject)(cog, i, _driver()),
        _not_placing,
        "`/driver reject` is available only while the season is in placements",
    ),
    "reject not an unassigned driver": (
        "driver reject",
        lambda cog, i: undecorate(DriverCog.reject)(cog, i, _driver()),
        _placed,
        "**Racer** is not an Unassigned driver",
    ),
    "sack not ongoing": (
        "driver sack",
        lambda cog, i: undecorate(DriverCog.sack)(cog, i, _driver()),
        _not_ongoing,
        "`/driver sack` is available only while the season is ongoing",
    ),
    "sack no profile": (
        "driver sack",
        lambda cog, i: undecorate(DriverCog.sack)(cog, i, _driver()),
        _no_profile,
        "No driver profile found for **Racer**",
    ),
    "sack refused by the service": (
        "driver sack",
        lambda cog, i: undecorate(DriverCog.sack)(cog, i, _driver()),
        _service_refuses("sack_driver", "Racer has no seat to sack them from."),
        "Racer has no seat to sack them from.",
    ),
}


@pytest.mark.xfail(strict=True, reason="#482: the driver cog's own refusals write no log line")
@pytest.mark.parametrize("case", sorted(_CASES))
async def test_a_refusal_of_the_driver_cog_is_recorded_in_the_log_channel(case):
    """A manager the driver cog's own checks turn away is answered as today, seen by them
    alone, and one standard line records the refusal (#482)."""
    command, run, setup, phrase = _CASES[case]
    cog = _cog()
    if setup is not None:
        setup(cog.bot)
    interaction = _interaction(cog, command)

    await run(cog, interaction)

    sent = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    assert len(sent) == 1 and sent[0].kwargs.get("ephemeral") is True
    reply = sent[0].args[0]
    assert phrase in reply
    lines = [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]
    assert lines == [f"⛔ `/{command}` refused for Alex (<@{MANAGER_ID}>) — {_reason(reply)}"]
