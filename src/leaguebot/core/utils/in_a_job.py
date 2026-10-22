"""The running job's number, and the guard that refuses a post made outside a job (#439, 6b).

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

**The guard raises in production as in tests.** `posts_in_a_job` marks a function that posts and
raises `PostedOutsideAJob` when called outside a job. A post outside a job is a defect: it would
go unrecorded and unretried, and a loud failure is how the suite and the host's log find one. The
error is no `StepFailedOnDiscord`: it is a fault in the bot's own code, which stops the queue at
the step that made it as any fault there does, and is never taken for a refusal by Discord and
tried again on the schedule as one. A unit test that calls a posting function directly wraps the
call in `tests.support.change_queue.in_a_job()`.
"""
from __future__ import annotations

import functools
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, ParamSpec, TypeVar

_JOB: ContextVar[int | None] = ContextVar("running_job", default=None)

_P = ParamSpec("_P")
_R = TypeVar("_R")


class PostedOutsideAJob(RuntimeError):
    """A function that posts was called outside a job: a fault in the bot's own code."""


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


def posts_in_a_job(post: Callable[_P, Coroutine[Any, Any, _R]]) -> Callable[_P, Coroutine[Any, Any, _R]]:
    """Mark the coroutine function *post* as one that posts: it refuses to run outside a job.

    Raises `PostedOutsideAJob`, naming the function, before *post* runs; inside a job it runs
    and gives back what it returned, its name and docstring kept."""

    @functools.wraps(post)
    async def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if _JOB.get() is None:
            raise PostedOutsideAJob(
                f"{post.__qualname__} posts to Discord and was called outside a job of a change"
            )
        return await post(*args, **kwargs)

    return guarded
