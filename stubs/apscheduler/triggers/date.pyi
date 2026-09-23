# See `stubs/apscheduler/__init__.pyi`.

from datetime import datetime, tzinfo

from apscheduler.triggers.base import BaseTrigger

class DateTrigger(BaseTrigger):
    def __init__(
        self, run_date: datetime | str | None = None, timezone: tzinfo | str | None = None
    ) -> None: ...
