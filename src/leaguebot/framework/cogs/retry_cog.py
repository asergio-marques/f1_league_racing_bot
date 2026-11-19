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

from leaguebot.framework.services.posting.retry_service import (
    attempt_delivery,
    get_all_pending,
    post_own_line,
)
from leaguebot.framework.utils.interaction_errors import describe_fault, job_failure_line
from leaguebot.framework.utils.job_runner import run_job

if TYPE_CHECKING:
    from leaguebot.framework.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: How the cycle reads in the host's log and in the log channel's line.
_JOB = "log line retry"


class _EntriesFaulted(Exception):
    """The cycle's end, where some kept lines' retries raised: the first fault, and how many.

    Raised so that the job runner logs and reports the cycle once; each fault was logged with its
    traceback, and the lines' ids, as it happened.
    """

    def __init__(self, first: Exception, count: int) -> None:
        super().__init__(f"{count} kept line(s) faulted")
        self.first = first
        self.count = count


def _cycle_line(job: str, error: BaseException) -> str:
    """The log channel's line for a failed cycle: the timed job's line, which for the lines that
    faulted names how many, and the kind of the first fault in `describe_fault`'s words, never a type,
    a message or an id."""
    if not isinstance(error, _EntriesFaulted):
        return job_failure_line(job, error)
    noun = "kept line" if error.count == 1 else "kept lines"
    return (
        f"❌ The bot's timed job `{job}` failed for {error.count} {noun} — "
        f"{describe_fault(error.first)}. The details are in the host's log."
    )


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
        """Attempt delivery for every pending message in the retry queue.

        A discord.py loop stops for good on an exception it was not told to retry, and a retry
        worker that stopped would retry and report nothing until the bot was restarted, so a
        fault never leaves this body. The cycle runs through the job runner: a fault in it as a
        whole (the kept lines cannot be read) is logged with its traceback and reported by one
        line to the log channel, **by the worker's own path, which never queues it** — a line the
        log channel refuses goes to the host's log alone, so a cycle that keeps failing queues
        nothing of its own. Each kept line's try is guarded in the loop's body, so one line's
        database fault neither stops the lines after it nor the loop; the lines that faulted are
        reported by one line at the cycle's end, their ids in the host's log alone.
        """
        router = self._bot.output_router

        async def report(line: str) -> None:
            await post_own_line(router, line)

        await run_job(_JOB, self._cycle, report=report, line=_cycle_line)

    async def _cycle(self) -> None:
        """One pass over the kept lines: raises where the lines cannot be read, or, once every
        line has been tried, where some of them faulted."""
        pending = await get_all_pending(self._bot.db_path)
        if not pending:
            return

        log.info("retry_loop: processing %d pending message(s)", len(pending))
        faults: list[Exception] = []
        for entry in pending:
            try:
                await attempt_delivery(
                    entry, self._bot, router=self._bot.output_router,
                    now=self._bot.change_queue.now(),
                )
            except Exception as exc:  # noqa: BLE001 — one line's fault must not stop the others
                log.error(
                    "retry_loop: trying kept line id=%s raised: %s", entry.id, exc, exc_info=True
                )
                faults.append(exc)
        if faults:
            raise _EntriesFaulted(faults[0], len(faults)) from faults[0]

    @retry_loop.before_loop
    async def before_retry_loop(self) -> None:
        await self._bot.wait_until_ready()
