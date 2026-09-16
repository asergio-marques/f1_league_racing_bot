"""Three small `/results` commands: detaching a configuration, and the two fastest-lap amends.

Issue #208. `/results config detach`, `/results amend fl` and `/results amend fl-plimit` were
uncovered. None is complicated; each carries one rule worth stating.

**Detaching is for a season still in setup.** Once a season is active its points are
snapshotted, and pulling a configuration out from under a running championship would leave the
snapshot describing a table the season no longer names. After that point, `/results amend` is
the route — through a modification store a league admin reviews.

**Detaching something not attached is an answer, not a failure.** The manager wanted it gone
and it is gone; the reply says so rather than reporting an error for a state that is already
what they asked for.

**The fastest-lap amends write the modification store, not the season.** Nothing a league sees
changes until the amendment is reviewed and approved, which is why both refuse outright when
amendment mode is off rather than writing somewhere nothing will read.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.results_cog import ResultsCog  # noqa: E402
from services.amendment_service import AmendmentNotActiveError  # noqa: E402
from services.season_points_service import (  # noqa: E402
    ConfigNotAttachedError,
    SeasonNotInSetupError,
)
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 13608
SEASON_ID = 1


_UNSET = object()


def _make_cog(*, enabled=True, season=_UNSET):
    bot = MagicMock()
    bot.db_path = "/tmp/not-read.db"
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_season_for_server = AsyncMock(
        return_value=SimpleNamespace(id=SEASON_ID, status="SETUP") if season is _UNSET else season
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(c.args[0])
        for c in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if c.args
    )


def _session():
    return SimpleNamespace(name="Feature Race", value="FEATURE_RACE")


async def _detach(cog, interaction, *, error=None):
    with patch(
        "services.season_points_service.detach_config", new=AsyncMock(side_effect=error)
    ) as detach:
        await undecorate(ResultsCog.config_detach)(cog, interaction, "Standard")
    return detach


async def _fl(cog, interaction, *, command="amend_fl", value=2, error=None):
    target = {
        "amend_fl": "services.amendment_service.modify_fl_bonus",
        "amend_fl_plimit": "services.amendment_service.modify_fl_position_limit",
    }[command]
    with patch(target, new=AsyncMock(side_effect=error)) as modify:
        await undecorate(getattr(ResultsCog, command))(
            cog, interaction, "Standard", _session(), value
        )
    return modify


# ---------------------------------------------------------------------------
# /results config detach
# ---------------------------------------------------------------------------


async def test_a_configuration_is_detached(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    detach = await _detach(cog, interaction)

    detach.assert_awaited_once()
    assert detach.await_args.args[1:4] == (SEASON_ID, "Standard", "SETUP")
    assert "detached from the current season" in _replied(interaction)
    assert "/results config detach | Success" in str(
        cog.bot.output_router.post_log.await_args.args[1]
    )


async def test_detaching_from_a_running_season_is_refused(tmp_path):
    """Its points are snapshotted; `/results amend` is the route once it has started."""
    cog = _make_cog()
    interaction = _interaction()

    await _detach(cog, interaction, error=SeasonNotInSetupError("active"))

    assert "only allowed for seasons in SETUP" in _replied(interaction)
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_detaching_what_is_not_attached_is_an_answer(tmp_path):
    """The manager wanted it gone and it is gone."""
    cog = _make_cog()
    interaction = _interaction()

    await _detach(cog, interaction, error=ConfigNotAttachedError("Standard"))

    assert "is not attached to this season" in _replied(interaction)
    assert "❌" not in _replied(interaction)
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_detaching_with_no_season_is_refused(tmp_path):
    cog = _make_cog(season=None)
    interaction = _interaction()

    detach = await _detach(cog, interaction)

    assert "No season found" in _replied(interaction)
    detach.assert_not_awaited()


async def test_detaching_is_refused_while_the_module_is_off(tmp_path):
    cog = _make_cog(enabled=False)
    interaction = _interaction()

    detach = await _detach(cog, interaction)

    detach.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


# ---------------------------------------------------------------------------
# /results amend fl and fl-plimit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,value,phrase,logged",
    [
        ("amend_fl", 2, "FL bonus → 2 pts", "fl_bonus: 2"),
        ("amend_fl_plimit", 10, "FL position limit → top 10", "fl_position_limit: 10"),
    ],
)
async def test_the_modification_store_is_written(tmp_path, command, value, phrase, logged):
    cog = _make_cog()
    interaction = _interaction()

    modify = await _fl(cog, interaction, command=command, value=value)

    modify.assert_awaited_once()
    assert modify.await_args.args[1:] == (SEASON_ID, "Standard", "FEATURE_RACE", value)
    assert "Updated in modification store" in _replied(interaction)
    assert phrase in _replied(interaction)
    assert logged in str(cog.bot.output_router.post_log.await_args.args[1])


@pytest.mark.parametrize("command", ["amend_fl", "amend_fl_plimit"])
async def test_an_amend_outside_amendment_mode_is_refused(tmp_path, command):
    """Writing a store nothing will read would report a change that never reaches the
    league."""
    cog = _make_cog()
    interaction = _interaction()

    await _fl(cog, interaction, command=command, error=AmendmentNotActiveError("off"))

    assert "Amendment mode is not active" in _replied(interaction)
    cog.bot.output_router.post_log.assert_not_awaited()


@pytest.mark.parametrize("command", ["amend_fl", "amend_fl_plimit"])
async def test_an_amend_with_no_season_is_refused(tmp_path, command):
    cog = _make_cog(season=None)
    interaction = _interaction()

    modify = await _fl(cog, interaction, command=command)

    assert "No active season" in _replied(interaction)
    modify.assert_not_awaited()


@pytest.mark.parametrize("command", ["amend_fl", "amend_fl_plimit"])
async def test_an_amend_is_refused_while_the_module_is_off(tmp_path, command):
    cog = _make_cog(enabled=False)
    interaction = _interaction()

    modify = await _fl(cog, interaction, command=command)

    modify.assert_not_awaited()
