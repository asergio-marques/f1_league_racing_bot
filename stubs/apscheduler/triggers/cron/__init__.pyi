# See `stubs/apscheduler/__init__.pyi`.

from datetime import datetime, tzinfo

from apscheduler.triggers.base import BaseTrigger

class CronTrigger(BaseTrigger):
    def __init__(
        self,
        year: int | str | None = None,
        month: int | str | None = None,
        day: int | str | None = None,
        week: int | str | None = None,
        day_of_week: int | str | None = None,
        hour: int | str | None = None,
        minute: int | str | None = None,
        second: int | str | None = None,
        start_date: datetime | str | None = None,
        end_date: datetime | str | None = None,
        timezone: tzinfo | str | None = None,
        jitter: int | None = None,
    ) -> None: ...
