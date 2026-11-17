"""The bot's start-up, run once per process (architecture.md, "Start-up runs once, in one
function", steps 5 to 7, and "Timed work and restarts").

`start_up` is the sweep. It is handed the builder's `StartingHook`, the module service, the change
queue, the scheduler service and the log channel's `post_log`. It starts the scheduler **paused**,
so the job store is readable (``has_job``, ``get_jobs`` and ``remove_job`` see what is stored, which
the signup close recovery relies on) while no job runs. Inside the queue's `sweeping()` it runs
core's own steps in the order they signed up, then turns to each module in sign-up order: it reads
the module's switch and runs the module's steps one at a time in the module's own order; where the
module is off it passes the module over, with a line in the host's log, save for the steps the
module marked with `runs_while_off`, which it runs, in the module's order, all the same. Each
enabled module thus catches up its own missed timed events in its turn; nothing interleaves the events of different
modules by time. The hold the queue keeps on every ask is released on leaving `sweeping()`; then the
queue starts, and the scheduler resumes last: no scheduled job runs until start-up has ended, which
is also why a restart's take-down of a standing season review is always asked before that review's
expiry timer can fire.

**Everything is contained, by the job runner.** The sweep goes through `run_job`, one step at a
time (architecture.md, "Errors and failures"): a step, a module's switch read, a module's list of
steps and the queue's start that raises is logged with its traceback, naming it, and reported to
the log channel by one line (`start_up_failure_line`, in place of the runner's own); the rest carry
on, the module's later steps included, so one failing recovery neither stops the others nor leaves
the queue unstarted. `run_job` says whether the work succeeded, so a module whose switch or list of
steps cannot be read is passed over: a switch that is not signed up (`SwitchNotSignedUp`) is a
fault in the bot's assembly and is reported, never read as off. A queue that fails to start (a
locked database) goes no further, and the scheduler is resumed all the same, so scheduled jobs are
not held paused for the rest of the run. The sweep holds no catch-all of its own.

**The hold is released whatever happens.** An outer `finally` calls `release()` on the queue, so a
scheduler that cannot even start paused does not leave every member's request waiting for the rest
of the run; releasing a queue already released is harmless.

**Once per process, by a guard of its own.** discord.py reports the bot ready again after a network
drop; the second call finds the guard spent, logs that start-up was skipped and does nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from leaguebot.framework.services.switches.module_service import ModuleService
from leaguebot.framework.services.queue.change_queue import ChangeQueue
from leaguebot.framework.services.jobs.scheduler import SchedulerService
from leaguebot.framework.utils.hooks import StartingHook, StartUpStep, runs_whatever_the_switch_says
from leaguebot.framework.utils.interaction_errors import start_up_failure_line
from leaguebot.framework.utils.job_runner import run_job

log = logging.getLogger(__name__)

#: The log channel's `post_log`, as the sweep uses it: a line in, awaited.
PostLog = Callable[[str], Awaitable[object]]

#: Spent by the first call to :func:`start_up`.
_started = False


def _possessive(key: str) -> str:
    """*key* as a possessive: a bare apostrophe after an "s" (results', images'), "'s" otherwise
    (signup's, attendance's, weather's)."""
    return f"{key}'" if key.endswith("s") else f"{key}'s"


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

    async def run(name: str, work: Callable[[], Awaitable[object]]) -> bool:
        return await run_job(name, work, report=post_log, line=start_up_failure_line)

    try:
        scheduler.start(paused=True)
        try:
            async with change_queue.sweeping():
                for name, step in starting.core_steps():
                    await run(name, step)
                for key, steps_of in starting.modules():
                    owner = _possessive(key)
                    enabled = False

                    async def read_switch() -> None:
                        nonlocal enabled
                        enabled = await modules.is_enabled(key)

                    steps: list[StartUpStep] = []

                    async def read_steps() -> None:
                        nonlocal steps
                        steps = list(steps_of())

                    if not await run(f"{owner} switch", read_switch):
                        continue
                    if not await run(f"{owner} start-up steps", read_steps):
                        continue
                    if not enabled:
                        log.info("Start-up passed over the %s module, which is off", key)
                        steps = [step for step in steps if runs_whatever_the_switch_says(step)]
                    for name, step in steps:
                        await run(name, step)
            # Carry on the changes a stop cut off, once the sweep has asked its own: a change a
            # member or Discord asked for meanwhile has waited, held, until here.
            await run("change queue start", change_queue.start)
        finally:
            scheduler.resume()
    finally:
        change_queue.release()
    return True
