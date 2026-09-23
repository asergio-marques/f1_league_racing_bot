# See `stubs/apscheduler/__init__.pyi`.

from datetime import datetime, tzinfo

from apscheduler.triggers.base import BaseTrigger

class IntervalTrigger(BaseTrigger):
    def __init__(
        self,
        weeks: int = 0,
        days: int = 0,
        hours: int = 0,
        minutes: int = 0,
        seconds: int = 0,
        start_date: datetime | str | None = None,
        end_date: datetime | str | None = None,
        timezone: tzinfo | str | None = None,
        jitter: int | None = None,
    ) -> None: ...
