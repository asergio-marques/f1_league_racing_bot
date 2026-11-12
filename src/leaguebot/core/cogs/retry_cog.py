"""RetryCog — background worker that retries the log lines the log channel failed to take, every
5 minutes.

Constitution Principle V: a line that fails to post to the log channel is retried until
delivered, so the log stays complete even under transient Discord API errors (e.g., 503
upstream overflows). The posts of a change are not retried here but by the change queue.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord.ext import commands, tasks

from leaguebot.core.services.posting.retry_service import attempt_delivery, get_all_pending

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


class RetryCog(commands.Cog):
    """Processes pending_messages every 5 minutes and retries delivery."""

    def __init__(self, bot: "LeagueBot") -> None:
        self._bot = bot

    async def cog_unload(self) -> None:
        self.retry_loop.cancel()

    def start_loop(self) -> None:
        """Start the retry loop; ``LeagueBot.setup_hook`` calls this once, at login.

        Not from ``__init__`` or ``cog_load``: both run as ``build`` adds the cog, before the
        bot has logged in, and the loop's first act is ``wait_until_ready``, which raises on a
        bot that never logs in (a built bot a test holds) and leaves a failed task. Not from an
        ``on_ready`` listener either: discord.py reports "ready" again after a network drop, and
        start-up work does not belong in that handler (architecture.md, "Start-up runs once").
        ``setup_hook`` runs once per run, after login and before the connection is made.
        """
        self.retry_loop.start()

    @tasks.loop(minutes=5)
    async def retry_loop(self) -> None:
        """Attempt delivery for every pending message in the retry queue."""
        try:
            pending = await get_all_pending(self._bot.db_path)
        except Exception as exc:
            log.error("retry_loop: failed to load pending messages: %s", exc, exc_info=True)
            return

        if not pending:
            return

        log.info("retry_loop: processing %d pending message(s)", len(pending))
        for entry in pending:
            await attempt_delivery(
                entry, self._bot, router=self._bot.output_router,
                now=self._bot.change_queue.now(),
            )

    @retry_loop.before_loop
    async def before_retry_loop(self) -> None:
        await self._bot.wait_until_ready()
