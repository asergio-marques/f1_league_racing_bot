# See `stubs/apscheduler/__init__.pyi`.

from collections.abc import Mapping
from typing import Any

from apscheduler.jobstores.base import BaseJobStore

class SQLAlchemyJobStore(BaseJobStore):
    def __init__(
        self,
        url: str | None = None,
        engine: Any = None,
        tablename: str = "apscheduler_jobs",
        metadata: Any = None,
        pickle_protocol: int = ...,
        tableschema: str | None = None,
        engine_options: Mapping[str, Any] | None = None,
    ) -> None: ...
