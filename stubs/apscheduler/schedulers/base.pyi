# See `stubs/apscheduler/__init__.pyi`.

from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from apscheduler.job import Job
from apscheduler.triggers.base import BaseTrigger

class BaseScheduler(metaclass=ABCMeta):
    def __init__(self, gconfig: Mapping[str, Any] = ..., **options: Any) -> None: ...
    @property
    def running(self) -> bool: ...
    def start(self, paused: bool = False) -> None: ...
    @abstractmethod
    def shutdown(self, wait: bool = True) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def add_job(
        self,
        func: Callable[..., Any],
        trigger: BaseTrigger | str | None = None,
        args: Sequence[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
        id: str | None = None,
        name: str | None = None,
        misfire_grace_time: int | None = ...,
        coalesce: bool = ...,
        max_instances: int = ...,
        next_run_time: datetime | None = ...,
        jobstore: str = "default",
        executor: str = "default",
        replace_existing: bool = False,
        **trigger_args: Any,
    ) -> Job: ...
    def remove_job(self, job_id: str, jobstore: str | None = None) -> None: ...
    def get_jobs(self, jobstore: str | None = None, pending: bool | None = None) -> list[Job]: ...
    def get_job(self, job_id: str, jobstore: str | None = None) -> Job | None: ...
