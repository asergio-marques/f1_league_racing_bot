"""The wait of a command answered off the change queue for the catch-up after a restart.

A command that goes through the queue waits inside `ChangeQueue.ask`. One answered off it (`/bot
pack`, `/bot factory-reset`, the `/test-mode backup` commands that change something, the
`/attendance config` commands that set something, `/attendance sync`, whose recalculation is written
off the queue, and the `/signup` commands that set something) never asks, so it
would act on a league the start-up sweep has not finished catching up. `after_the_catch_up`, worn
directly above the `def` and below the tier guard, makes it wait first (core specification, "When
the bot stops"). A helper never reads the queue off the bot, so each cog hands the decorator the
way to its own: `after_the_catch_up(lambda cog: cog.bot.change_queue)`, once in each cog's file.

While it waits the interaction is deferred, so Discord shows "thinking…". The command was written
to answer at once, by `response.send_message` or its own `response.defer`, and a deferred
interaction takes neither; rather than touch each command's replies, the wrapper puts `_FollowUp`
in the place of the interaction's response for the one run, which sends by a follow-up and treats a
second defer as done. A command run with no hold is untouched: the wrapper adds no step. A tier
guard's refusal, worn above, still comes at once.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Protocol

import discord

#: The slot in which discord.py keeps an interaction's response object, once made.
_RESPONSE_SLOT = "_cs_response"


class HoldsRequests(Protocol):
    """What the decorator needs of the change queue, which a helper does not import."""

    async def wait_out_the_hold(self, interaction: discord.Interaction) -> bool: ...


class _FollowUp:
    """An interaction's response, once it has been deferred: what the command sends goes as a
    follow-up, and a second defer is done already. Anything else is the real response's."""

    def __init__(self, interaction: discord.Interaction, response: Any) -> None:
        self._interaction = interaction
        self._response = response

    def is_done(self) -> bool:
        return True

    async def defer(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        await self._interaction.followup.send(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)


def after_the_catch_up(queue_of: Callable[[Any], HoldsRequests]) -> Callable[[Callable], Callable]:
    """The decorator that makes a command answered off the queue wait for the catch-up before it
    acts. *queue_of* is given the cog and returns its bot's change queue."""

    def decorate(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(
            self: Any, interaction: discord.Interaction, *args: Any, **kwargs: Any
        ) -> None:
            if await queue_of(self).wait_out_the_hold(interaction):
                setattr(interaction, _RESPONSE_SLOT, _FollowUp(interaction, interaction.response))
            await func(self, interaction, *args, **kwargs)

        return wrapper

    return decorate
