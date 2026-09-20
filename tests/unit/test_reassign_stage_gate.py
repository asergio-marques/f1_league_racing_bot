"""`/driver reassign` runs only while drivers are being placed (issue #224).

Re-keying a profile onto another Discord account is part of settling who sits where, so it
belongs to the two stages in which a league is placing drivers — **Placements**, and
**Ongoing, placements** with the drivers of a closed signup window still to place. Everywhere
else `/driver move` is the command for changing where a driver sits, and the reassign had no
stage check at all: it ran in Configuration, mid-race, in Pending completion, and on a server
whose only season was long since archived.

**The consequence is accepted, not a fault:** a person who changes account between seasons is
re-keyed once the next season reaches placements, which is the moment they would be placed
anyway.

The gate is checked **before** the parameters, so a manager running it in the wrong stage is
told the rule rather than told they mis-typed a snowflake. `test_the_stage_is_read_before_the
_parameters` pins that ordering.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.driver_cog import DriverCog  # noqa: E402
from models.season import SeasonStage, status_of_stage  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402
from utils.season_gate import LIVE_STAGES, PLACEMENT_STAGES  # noqa: E402

#: Every stage the command is refused in, plus the no-season case. Derived from the live
#: stages rather than listed, so a stage added to the lifecycle is covered without an edit.
REFUSED_STAGES = sorted(LIVE_STAGES - PLACEMENT_STAGES, key=lambda s: s.value)


def _bot(stage: SeasonStage | None) -> MagicMock:
    season = (
        None
        if stage is None
        else SimpleNamespace(
            id=1, season_number=2, stage=stage, status=status_of_stage(stage).value
        )
    )
    bot = MagicMock()
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.driver_service = MagicMock()
    bot.driver_service.reassign_user_id = AsyncMock(
        return_value=SimpleNamespace(
            profile=SimpleNamespace(
                id=5, former_driver=False, current_state=SimpleNamespace(value="Assigned")
            ),
            replaced_account="111",
            accounts=["222"],
            switched_back=False,
            merged_accounts=[],
        )
    )
    bot.placement_service = MagicMock()
    bot.placement_service.move_driver_roles = AsyncMock(return_value=[])
    bot.wizard_service = MagicMock()
    bot.wizard_service.move_held_channel = AsyncMock(return_value=[])
    bot.image_config_service = MagicMock()
    bot.image_config_service.get_config = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    return bot


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild = None
    interaction.user = MagicMock()
    interaction.user.id = 9
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _said(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _run(bot, interaction, *, old_user_id: str | None = "111"):
    cog = DriverCog.__new__(DriverCog)
    cog.bot = bot
    new_user = MagicMock(id=222, display_name="New")
    await undecorate(DriverCog.reassign)(
        cog, interaction, new_user, None, old_user_id
    )


# ---------------------------------------------------------------------------
# Permitted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", sorted(PLACEMENT_STAGES, key=lambda s: s.value))
async def test_a_reassign_runs_while_drivers_are_being_placed(stage):
    bot = _bot(stage)
    interaction = _interaction()

    await _run(bot, interaction)

    bot.driver_service.reassign_user_id.assert_awaited_once()
    assert "✅" in _said(interaction)


# ---------------------------------------------------------------------------
# Refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", REFUSED_STAGES)
async def test_a_reassign_is_refused_in_every_other_live_stage(stage):
    bot = _bot(stage)
    interaction = _interaction()

    await _run(bot, interaction)

    bot.driver_service.reassign_user_id.assert_not_awaited()
    said = _said(interaction)
    assert "drivers are being placed" in said, said
    assert "/driver move" in said


async def test_a_reassign_is_refused_with_no_live_season():
    """Between seasons the refusal reads the same. Telling a manager there is no season
    would have them start one, which is not what the rule is asking of them.
    """
    bot = _bot(None)
    interaction = _interaction()

    await _run(bot, interaction)

    bot.driver_service.reassign_user_id.assert_not_awaited()
    said = _said(interaction)
    assert "drivers are being placed" in said, said
    assert "no season" not in said.lower()


async def test_pending_completion_is_among_the_stages_refused():
    """Named on its own because it is the stage issue #224 is about."""
    assert SeasonStage.PENDING_COMPLETION in REFUSED_STAGES


async def test_the_stage_is_read_before_the_parameters():
    """Neither `old_user` nor `old_user_id` given — a refusal the command makes for itself.

    In a stage where the reassign is closed, the manager hears the rule, not the parameter
    complaint: the parameter one would send them off to find a snowflake they cannot use.
    """
    interaction = _interaction()

    await _run(_bot(SeasonStage.ONGOING), interaction, old_user_id=None)

    said = _said(interaction)
    assert "drivers are being placed" in said, said
    assert "snowflake" not in said


async def test_the_parameter_check_still_fires_in_a_stage_that_allows_it():
    interaction = _interaction()

    await _run(_bot(SeasonStage.PLACEMENTS), interaction, old_user_id=None)

    assert "old_user" in _said(interaction)
