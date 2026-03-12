"""RetryCog — background worker that retries failed channel posts every 5 minutes.

Constitution Principle VII: Messages that fail to post to their designated channel
are retried until delivered, preserving output channel discipline even under
transient Discord API errors (e.g., 503 upstream overflows).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord.ext import commands, tasks

from services.retry_service import attempt_delivery, get_all_pending

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


class RetryCog(commands.Cog):
    """Processes pending_messages every 5 minutes and retries delivery."""

    def __init__(self, bot: "Bot") -> None:
        self._bot = bot
        self.retry_loop.start()

    def cog_unload(self) -> None:
        self.retry_loop.cancel()

    @tasks.loop(minutes=5)
    async def retry_loop(self) -> None:
        """Attempt delivery for every pending message in the retry queue."""
        try:
            pending = await get_all_pending(self._bot.db_path)  # type: ignore[attr-defined]
        except Exception as exc:
            log.error("retry_loop: failed to load pending messages: %s", exc)
            return

        if not pending:
            return

        log.info("retry_loop: processing %d pending message(s)", len(pending))
        for entry in pending:
            await attempt_delivery(entry, self._bot)

    @retry_loop.before_loop
    async def before_retry_loop(self) -> None:
        await self._bot.wait_until_ready()
