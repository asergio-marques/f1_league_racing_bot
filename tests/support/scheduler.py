"""A scheduler double for the commands that copy its job store.

Each of them pauses the scheduler around the copy through `SchedulerService.paused` and copies the
file `SchedulerService.jobstore_path` names; this double offers those two and nothing else.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


class PausingScheduler:
    """Names *jobstore_path* as where its jobs are, and records in `events` each ``"pause"`` and
    ``"resume"`` that `paused` makes, in order, the resume made however the block ends."""

    def __init__(self, jobstore_path: str) -> None:
        self.jobstore_path = jobstore_path
        self.events: list[str] = []

    @contextmanager
    def paused(self) -> Iterator[None]:
        self.events.append("pause")
        try:
            yield
        finally:
            self.events.append("resume")
