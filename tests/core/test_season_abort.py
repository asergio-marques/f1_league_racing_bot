"""`/season abort` — a season whose placements were never confirmed, left as if it never was (#220).

Available only in Configuration, Waiting, Signups and Placements. The season is deleted with every
record of it, its signups included, and takes no number; its drivers go through the driver pass,
its window is closed and test mode is switched off.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.models.season import SeasonStage
from tests.support.undecorate import undecorate

SERVER_ID = 22160


def _cog(stage: SeasonStage | None) -> SeasonCog:
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=None if stage is None else SimpleNamespace(id=7, stage=stage)
    )
    cog.bot.change_queue.ask = AsyncMock(return_value=1)
    cog.bot.output_router.post_log = AsyncMock()
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


_ABORT_REFUSED = (
    "\u274c `/season abort` is available only before a season's placements are first confirmed. "
    "An ongoing season is cancelled with `/season cancel`."
)


@pytest.mark.parametrize(
    "stage, word, reply",
    [
        pytest.param(
            SeasonStage.CONFIGURATION, "confirm",
            "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
            id="abort-without-the-confirmation-word",
        ),
        pytest.param(None, "CONFIRM", _ABORT_REFUSED, id="abort-with-no-season"),
    ],
)
async def test_every_season_abort_refusal_is_recorded(stage, word, reply):
    """The core specification's record of what changed: a refusal is one line naming the member,
    what was refused and why. The admin Admin (id 42) runs /season abort without the exact
    word, or with no season: they get today's reply word for word and nothing else, nothing is
    asked of the change queue, and the log channel gets exactly one line,
    "⛔ `/season abort` refused for Admin (<@42>) — " and the reply's words."""
    cog = _cog(stage)
    interaction = _interaction()
    interaction.user.display_name = "Admin"
    interaction.client = cog.bot
    interaction.command.qualified_name = "season abort"
    interaction.response.is_done = MagicMock(return_value=False)

    await undecorate(SeasonCog.season_abort)(cog, interaction, word)

    replies = [call.args[0] for call in interaction.response.send_message.await_args_list]
    replies += [call.args[0] for call in interaction.followup.send.await_args_list]
    assert replies == [reply]
    cog.bot.change_queue.ask.assert_not_awaited()
    logged = [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]
    assert logged == [f"\u26d4 `/season abort` refused for Admin (<@42>) \u2014 {reply[2:]}"]
