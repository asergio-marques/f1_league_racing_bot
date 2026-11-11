"""A command answered off the change queue waits for the catch-up after a restart (#440).

`after_the_catch_up` (`leaguebot.core.utils.catch_up_gate`) is worn below a command's tier guard by
each command that changes the league without asking the queue. Driven here on a real change queue,
held as `main` leaves it before the bot connects or never held, and on a member's interaction that
answers through its response slot as a real one does, so no Discord is needed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import discord
import pytest

from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.utils import catch_up_gate
from leaguebot.core.utils.catch_up_gate import after_the_catch_up
from leaguebot.core.utils.channel_guard import (
    CHANGES_NOTHING_ATTRIBUTE,
    LEAGUE_MANAGER,
    TIER_ATTRIBUTE,
    changes_nothing,
    league_manager_only,
)
from tests.support.change_queue import member_interaction, response_in_its_slot


@pytest.mark.parametrize("held", [True, False], ids=["held for the catch-up", "not held"])
async def test_a_command_held_for_the_catch_up_acts_and_answers_by_follow_up(held):
    """A league manager invokes a command that is answered off the queue and wears the wait for
    the catch-up; the command answers by `response.send_message`, and defers by `response.defer`
    as some do. Held: the command does not run until the catch-up ends and the queue is released;
    then it runs once, its message goes as a follow-up, its own defer does nothing, and its
    response reads as answered. Not held: it runs at once, through the interaction's own response,
    with nothing deferred for it."""
    queue = ChangeQueue("unused.db", MagicMock(), MagicMock(), MagicMock())
    if held:
        queue.hold()
    runs: list[bool] = []

    class _Cog:
        def __init__(self) -> None:
            self.bot = SimpleNamespace(change_queue=queue)

        @after_the_catch_up(lambda cog: cog.bot.change_queue)
        async def command(self, interaction: Any) -> None:
            await interaction.response.defer(ephemeral=True)
            runs.append(interaction.response.is_done())
            await interaction.response.send_message("Done.", ephemeral=True)

    press = member_interaction(MagicMock())
    acting = asyncio.create_task(_Cog().command(response_in_its_slot(press)))
    for _ in range(20):
        await asyncio.sleep(0)

    if held:
        assert not acting.done()
        assert runs == []
        queue.release()
    await asyncio.wait_for(acting, timeout=5)

    assert len(runs) == 1
    if held:
        assert runs == [True]
        [deferred] = press.response.defer.await_args_list
        assert deferred.kwargs.get("thinking") is True
        press.followup.send.assert_awaited_once_with("Done.", ephemeral=True)
        assert press.response.send_message.await_count == 0
    else:
        press.response.defer.assert_awaited_once_with(ephemeral=True)
        press.response.send_message.assert_awaited_once_with("Done.", ephemeral=True)
        assert press.followup.send.await_count == 0


def test_a_tier_guard_still_reads_through_the_wait_for_the_catch_up():
    """A tier guard worn above the wait for the catch-up still marks the command with its tier, and
    still reads a mark worn below the wait, the wrapper keeping the command's name. Nothing is
    invoked, so whether the queue is held does not matter."""
    queue = ChangeQueue("unused.db", MagicMock(), MagicMock(), MagicMock())

    async def body(self: Any, interaction: Any) -> None:
        return None

    guarded = league_manager_only(after_the_catch_up(lambda cog: queue)(changes_nothing(body)))
    assert getattr(guarded, TIER_ATTRIBUTE) == LEAGUE_MANAGER
    assert getattr(guarded, CHANGES_NOTHING_ATTRIBUTE, False) is True
    assert guarded.__name__ == "body"


def test_the_response_slot_the_gate_swaps_is_the_one_discord_py_keeps():
    """The wait for the catch-up answers a held command by follow-up by putting a stand-in in the
    slot where discord.py caches an interaction's response, a private name of discord.py's. The
    suite's interaction double declares that slot itself, so it cannot tell if discord.py renames
    it; this reads the real `discord.Interaction`, so an upgrade that moves the slot fails here
    rather than failing every held command during a catch-up."""
    response = discord.Interaction.__dict__["response"]
    assert isinstance(response, discord.utils.CachedSlotProperty)
    assert response.name == catch_up_gate._RESPONSE_SLOT
    assert catch_up_gate._RESPONSE_SLOT in discord.Interaction.__slots__
