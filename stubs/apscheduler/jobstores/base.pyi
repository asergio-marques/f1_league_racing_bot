# See `stubs/apscheduler/__init__.pyi`.

from abc import ABCMeta

class BaseJobStore(metaclass=ABCMeta): ...

class JobLookupError(KeyError):
    def __init__(self, job_id: str) -> None: ...
