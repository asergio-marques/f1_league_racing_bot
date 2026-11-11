"""The buttons of the change queue's stop notice: Retry and Discard.

A job that fails stops the queue until it is cleared, and the one ❌ message the stop posts to the
log channel carries these two buttons (`docs/design/architecture.md`, "How a change is carried
out"). Retry is a league manager's or a league admin's; Discard is a league admin's. Who may press
is the queue's to judge and to record: a button here only hands the interaction and the id of the
message it was pressed on to the queue, which looks the job up by its notice and answers the
presser.

**The view is persistent, with fixed custom ids.** `queue:retry` and `queue:discard` never change
once shipped, so that a notice posted before a restart still works after it: the queue registers
one instance at start-up (`bot.add_view`), and any notice's press is routed to it by custom id.
It is built with the queue instance and holds no state of its own, so one instance serves every
notice.
"""

from __future__ import annotations

from typing import Protocol

import discord

from leaguebot.core.utils.league_server import CallbackButton, LeagueView

__all__ = ["QueueStopView", "StopControls", "RETRY_ID", "DISCARD_ID"]

#: The custom ids of the two buttons.
RETRY_ID = "queue:retry"
DISCARD_ID = "queue:discard"


class StopControls(Protocol):
    """What the buttons press: the change queue's Retry and Discard, each given the id of the
    notice the press was made on (None where the press carries no message) and the interaction."""

    async def retry(
        self, notice_message_id: int | None, interaction: discord.Interaction
    ) -> None: ...

    async def discard(
        self, notice_message_id: int | None, interaction: discord.Interaction
    ) -> None: ...


class QueueStopView(LeagueView):
    """Retry and Discard, on the stop notice of the job the queue is stopped at."""

    def __init__(self, queue: StopControls) -> None:
        super().__init__(timeout=None)
        self._queue = queue
        self.add_item(CallbackButton(
            label="Retry", style=discord.ButtonStyle.primary, custom_id=RETRY_ID,
            on_press=self._retry,
        ))
        self.add_item(CallbackButton(
            label="Discard", style=discord.ButtonStyle.danger, custom_id=DISCARD_ID,
            on_press=self._discard,
        ))

    async def _retry(self, interaction: discord.Interaction) -> None:
        await self._queue.retry(_notice_id(interaction), interaction)

    async def _discard(self, interaction: discord.Interaction) -> None:
        await self._queue.discard(_notice_id(interaction), interaction)


def _notice_id(interaction: discord.Interaction) -> int | None:
    """The id of the message the button was pressed on, or None where the press carries none."""
    message = interaction.message
    return None if message is None else message.id
