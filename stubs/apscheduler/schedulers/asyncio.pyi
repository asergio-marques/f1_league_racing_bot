# See `stubs/apscheduler/__init__.pyi`.

from apscheduler.schedulers.base import BaseScheduler

class AsyncIOScheduler(BaseScheduler):
    def shutdown(self, wait: bool = True) -> None: ...
