"""The bot's start-up, run once per process (architecture.md, "Start-up runs once, in one
function", steps 5 to 7).

`start_up` is handed the builder's ordered steps, the change queue and the scheduler service. It
starts the scheduler **paused**, so the job store is readable (``has_job``, ``get_jobs`` and
``remove_job`` see what is stored, which the signup close recovery relies on) while no job runs,
runs each step in turn, starts the queue, and resumes the scheduler last: no scheduled job runs
until start-up has ended, which is also why a restart's take-down of a standing season review is
always asked before that review's expiry timer can fire.

**Each step is contained.** A step that raises is logged with its traceback, naming the step, and
the next one runs, so one failing recovery neither stops the rest nor leaves the queue unstarted.
The queue's own start is the last of the steps and is contained the same way: a queue that fails to
start (a locked database) is logged with its traceback and goes no further, and the scheduler is
resumed all the same, so scheduled jobs are not held paused for the rest of the run.

**Once per process, by a guard of its own.** discord.py reports the bot ready again after a network
drop; the second call finds the guard spent, logs that start-up was skipped and does nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from leaguebot.core.services.change_queue import ChangeQueue
from leaguebot.core.services.scheduler_service import SchedulerService

log = logging.getLogger(__name__)

#: A start-up step: its name, for the log, and what it runs, handed nothing.
StartUpStep = tuple[str, Callable[[], Awaitable[None]]]

#: Spent by the first call to :func:`start_up`.
_started = False


async def start_up(
    *,
    steps: Sequence[StartUpStep],
    change_queue: ChangeQueue,
    scheduler: SchedulerService,
) -> bool:
    """Run the start-up once, and say whether this call was the one that ran it.

    Returns False, having run nothing, where start-up has run already in this process."""
    global _started
    if _started:
        log.info("Start-up already ran in this process; skipped")
        return False
    _started = True

    scheduler.start(paused=True)
    try:
        # Carry on the changes a stop cut off, as the last of the steps: a change asked for while
        # the steps ran has waited, queued, until here.
        for name, run in (*steps, ("change queue start", change_queue.start)):
            try:
                await run()
            except Exception:
                log.exception("Start-up step %r failed", name)
    finally:
        scheduler.resume()
    return True
