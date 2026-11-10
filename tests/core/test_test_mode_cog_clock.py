"""The `/test-mode` commands that read the time hand their services the change queue's clock
(#283).

architecture.md, "The time is always passed in": the change queue's clock is the bot's clock, and
a cog hands a service its `now` from `bot.change_queue.now()`, never the host's. `/test-mode
advance` is held to it in `test_test_mode_advance.py`; these are the review and the former-driver
flag. Each queue's clock reads a moment years from the host's, so that a host reading cannot pass
for it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from leaguebot.core.cogs import test_mode_cog as cog_module
from leaguebot.core.cogs.test_mode_cog import TestModeCog as _Cog
from tests.support.undecorate import undecorate

#: The change queue's clock.
QUEUE_NOW = datetime(2031, 3, 4, 5, 6, tzinfo=timezone.utc)


def _cog() -> _Cog:
    """A test-mode cog on a server in test mode, its change queue's clock at `QUEUE_NOW`."""
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=True)
    )
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.change_queue.now = MagicMock(return_value=QUEUE_NOW)
    cog = _Cog.__new__(_Cog)
    cog.bot = bot
    return cog


def _interaction(command: str) -> MagicMock:
    """*command*, run by the league admin Alex."""
    interaction = MagicMock()
    interaction.command.qualified_name = command
    interaction.user.id = 4242
    interaction.user.display_name = "Alex"
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def test_the_review_judges_the_season_at_the_queue_s_time(monkeypatch):
    """With test mode on, Alex runs `/test-mode review`. The summary is built at the change
    queue's time, the moment a raced round's weather steps are judged passed over by, and is
    what Alex is shown."""
    cog = _cog()
    summary = AsyncMock(return_value="Season review")
    monkeypatch.setattr(cog_module, "build_review_summary", summary)
    interaction = _interaction("test-mode review")

    await undecorate(_Cog.review)(cog, interaction)

    assert summary.await_args.kwargs.get("now") == QUEUE_NOW
    interaction.response.send_message.assert_awaited_once_with("Season review", ephemeral=True)


async def test_the_former_driver_flag_is_set_at_the_queue_s_time():
    """With test mode on, Alex sets Racer's former-driver flag. The service is handed the change
    queue's time, and Alex is told the old and new values."""
    cog = _cog()
    cog.bot.driver_service.set_former_driver = AsyncMock(return_value=(False, True))
    member = MagicMock()
    member.id = 5151
    member.display_name = "Racer"
    interaction = _interaction("test-mode set-former-driver")

    await undecorate(_Cog.set_former_driver)(cog, interaction, member, True)

    call = cog.bot.driver_service.set_former_driver.await_args
    assert call.args[:2] == ("5151", True)
    assert call.kwargs.get("now") == QUEUE_NOW
    reply = interaction.response.send_message.await_args.args[0]
    assert "Old value: False" in reply and "New value: True" in reply
