"""The running job's number (#439, 6b).

Every message the bot posts, edits or deletes is a job of a change (`docs/design/architecture.md`,
"Posting to Discord"). `current_job()` says whether the code running is inside one: the change
queue's worker sets the number of the job whose `run` or `record` it is carrying out with
`running_job`, around that call and no other, so the change's check and a job's `still_due`, which
run outside the job, see none.

**Why a context variable.** The worker's task and every coroutine it awaits share the variable, a
task a step starts copies it, and nothing outside a step can set it: a function that posts needs
no job number passed down to it, and a caller that is not a step cannot claim to be one by passing
one. *Rejected:* a flag on the bot or on the queue, which one lane's job would set for every other
lane's code to see; and an argument to every posting function, which a caller outside a job
could simply invent.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_JOB: ContextVar[int | None] = ContextVar("running_job", default=None)


@contextmanager
def running_job(job_id: int) -> Iterator[None]:
    """Hold *job_id* as the running job for the length of the block."""
    token = _JOB.set(job_id)
    try:
        yield
    finally:
        _JOB.reset(token)


def current_job() -> int | None:
    """The number of the job whose `run` or `record` is running, or None outside every job."""
    return _JOB.get()
