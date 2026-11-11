"""The bot's start-up, run once per process (architecture.md, "Start-up runs once, in one
function", steps 5 to 7, and "Timed work and restarts").

`start_up` is the sweep. It is handed the builder's `StartingHook`, the module service, the change
queue, the scheduler service and the log channel's `post_log`. It starts the scheduler **paused**,
so the job store is readable (``has_job``, ``get_jobs`` and ``remove_job`` see what is stored, which
the signup close recovery relies on) while no job runs. Inside the queue's `sweeping()` it runs
core's own steps in the order they signed up, then turns to each module in sign-up order: it reads
the module's switch, passes the module over, with a line in the host's log, where it is off, and
otherwise runs the module's steps one at a time in the module's own order. Each enabled module thus
catches up its own missed timed events in its turn; nothing interleaves the events of different
modules by time. The hold the queue keeps on every ask is released on leaving `sweeping()`; then the
queue starts, and the scheduler resumes last: no scheduled job runs until start-up has ended, which
is also why a restart's take-down of a standing season review is always asked before that review's
expiry timer can fire.

**Everything is contained.** A step, a module's switch read, a module's list of steps and the
queue's start that raises is logged with its traceback, naming it, and reported to the log channel
by one line (`start_up_failure_line`); the rest carry on, the module's later steps included, so one
failing recovery neither stops the others nor leaves the queue unstarted. A module whose switch or
list of steps cannot be read is passed over: a switch that is not signed up (`SwitchNotSignedUp`) is
a fault in the bot's assembly and is reported, never read as off. A queue that fails to start (a
locked database) goes no further, and the scheduler is resumed all the same, so scheduled jobs are
not held paused for the rest of the run. Each catch-all sits directly in the body of a `for`, so
none is a catch-all outside the architecture's five places. The job runner (#453) does not exist
yet; this gives its outcome and can be swapped for it.

**The hold is released whatever happens.** An outer `finally` calls `release()` on the queue, so a
scheduler that cannot even start paused does not leave every member's request waiting for the rest
of the run; releasing a queue already released is harmless.

**Once per process, by a guard of its own.** discord.py reports the bot ready again after a network
drop; the second call finds the guard spent, logs that start-up was skipped and does nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from leaguebot.core.services.modules.module_service import ModuleService
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.scheduler_service import SchedulerService
from leaguebot.core.utils.hooks import StartingHook
from leaguebot.core.utils.interaction_errors import start_up_failure_line

log = logging.getLogger(__name__)

#: The log channel's `post_log`, as the sweep uses it: a line in, awaited.
PostLog = Callable[[str], Awaitable[object]]

#: Spent by the first call to :func:`start_up`.
_started = False


async def _tell_the_log_channel(name: str, error: Exception, post_log: PostLog) -> None:
    """Tell the log channel, by one line, that the part *name* failed with *error*. The handler
    that calls this has logged the error and its traceback already, in its own body."""
    await post_log(start_up_failure_line(name, error))


async def start_up(
    *,
    starting: StartingHook,
    modules: ModuleService,
    change_queue: ChangeQueue,
    scheduler: SchedulerService,
    post_log: PostLog,
) -> bool:
    """Run the start-up once, and say whether this call was the one that ran it.

    Returns False, having run nothing, where start-up has run already in this process."""
    global _started
    if _started:
        log.info("Start-up already ran in this process; skipped")
        return False
    _started = True

    try:
        scheduler.start(paused=True)
        try:
            async with change_queue.sweeping():
                for name, run in starting.core_steps():
                    try:
                        await run()
                    except Exception as error:
                        log.exception("Start-up step %r failed", name)
                        await _tell_the_log_channel(name, error, post_log)
                for key, steps_of in starting.modules():
                    try:
                        enabled = await modules.is_enabled(key)
                    except Exception as error:
                        log.exception("Start-up step %r failed", f"{key}' switch")
                        await _tell_the_log_channel(f"{key}' switch", error, post_log)
                        continue
                    if not enabled:
                        log.info("Start-up passed over the %s module, which is off", key)
                        continue
                    try:
                        steps = steps_of()
                    except Exception as error:
                        log.exception("Start-up step %r failed", f"{key}' start-up steps")
                        await _tell_the_log_channel(f"{key}' start-up steps", error, post_log)
                        continue
                    for name, run in steps:
                        try:
                            await run()
                        except Exception as error:
                            log.exception("Start-up step %r failed", name)
                            await _tell_the_log_channel(name, error, post_log)
            # Carry on the changes a stop cut off, once the sweep has asked its own: a change a
            # member or Discord asked for meanwhile has waited, held, until here.
            for name, run in (("change queue start", change_queue.start),):
                try:
                    await run()
                except Exception as error:
                    log.exception("Start-up step %r failed", name)
                    await _tell_the_log_channel(name, error, post_log)
        finally:
            scheduler.resume()
    finally:
        change_queue.release()
    return True
