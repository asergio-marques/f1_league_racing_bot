"""The one job runner: work with no member to answer for it, run so that a failure is heard.

A timed job, the start-up's steps, the retry worker's cycle and the bot's background work have
no command to reply to, so a fault in one was heard of only in the host's log. `run_job` is where
such work is run (`docs/design/architecture.md`, "Errors and failures"), and does two things, each
independent of the other:

- **The host's log keeps the traceback**, at ERROR, naming the job.
- **The log channel gets one line**, through the *report* it is handed, formed by *line*
  (`job_failure_line` unless the caller has a form of its own, as the start-up sweep has). No
  traceback: the channel is for people.

**It never raises.** It runs where a failure already happened, and the fault may be the one the
report would write to: the log channel or the database it reads its channel from. A fault in
*report* is logged on the host and dropped. A cancellation is no failure and passes through, as
it passes any `except Exception`.

**Where *report* is not given** the failure goes to the host's log alone: work whose own failure
is what the log channel just reported, or whose closing line is told elsewhere.

**Its two catch-alls are the failure path of timed work**, the first place
`docs/design/architecture.md` allows one, and are named in `CATCH_ALLS_ALLOWED`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from leaguebot.framework.utils.interaction_errors import job_failure_line

log = logging.getLogger(__name__)

#: The log channel's `post_log`, as the runner uses it: a line in, awaited.
Report = Callable[[str], Awaitable[object]]


async def run_job(
    name: str,
    work: Callable[[], Awaitable[object]],
    *,
    report: Report | None = None,
    line: Callable[[str, BaseException], str] = job_failure_line,
) -> bool:
    """Await *work*, and say whether it succeeded; a failure of it never reaches the caller.

    *name* is how the job reads in the host's log and in the line (the kind with its key, where it
    has one). On an `Exception` the traceback is logged naming *name*, and *report*, where given,
    is awaited with ``line(name, error)``; a fault in *report* is logged and dropped.
    """
    try:
        await work()
    except Exception as error:  # noqa: BLE001 — the runner must never raise
        log.error("The job %r failed", name, exc_info=error)
        if report is not None:
            try:
                await report(line(name, error))
            except Exception as fault:  # noqa: BLE001 — the runner must never raise
                log.error(
                    "could not record in the log channel that the job %r failed: %s",
                    name,
                    fault,
                    exc_info=fault,
                )
        return False
    return True
