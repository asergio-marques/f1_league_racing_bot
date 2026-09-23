# See `stubs/apscheduler/__init__.pyi`.

from datetime import datetime
from typing import Any

from apscheduler.schedulers.base import BaseScheduler

class Job:
    id: str
    name: str
    kwargs: dict[str, Any]
    next_run_time: datetime | None
    def __init__(self, scheduler: BaseScheduler, id: str | None = None, **kwargs: Any) -> None: ...
