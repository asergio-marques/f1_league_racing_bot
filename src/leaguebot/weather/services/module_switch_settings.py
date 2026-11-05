"""Weather's own switch, on and off, as two settings on the change queue (#283, S11b).

`/module enable weather` and `/module disable weather` each ask one change built from a
`Setting` below by `setting_change` (`core/services/setting_change.py`, which holds what every
settings change does with a value already held and with a fault): `module_on("weather")` and
`module_off("weather")` (`core/models/change.py`), so core's cog names the change without
importing weather. Each save writes only `server_configs.weather_module_enabled`, weather's own
column on core's table (architecture.md, "The database"; `OWN_COLUMNS` in
`tests/repository/test_architecture_rules.py`).

**The switch-off removes the season's weather jobs after the save, not before** (A16,
`docs/design/architecture.md`): the flag going down and its audit entry share one save, and only
once that has run does an `After` step call `SchedulerService.cancel_all_weather`. Doing the
jobs' removal after the flag rather than before is safe only because every weather job reads the
flag first and does nothing while it is off (core specification, "A disabled module shall
produce nothing … a scheduled job"; pinned by `tests/core/test_weather_disable_scope.py` and the
draws, posts and clean-up's own guards). A fault removing the jobs follows `setting_change`'s
rule for a step after the save: the switch-off stands, and the reply says the jobs could not be
removed and do nothing while the module is off.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import (
    AuditRecord,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
    module_off,
    module_on,
)
from leaguebot.core.services.change_queue import CheckContext, Step, StepContext
from leaguebot.core.services.setting_change import After, Saved, Setting

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.scheduler_service import SchedulerService

#: The name of the step, after the switch-off's save, that removes the season's weather jobs.
REMOVE_JOBS = "remove_jobs"

_ENABLE_COMMAND = "/module enable weather"
_DISABLE_COMMAND = "/module disable weather"

_JOBS_NOT_REMOVED = (
    "⚠️ The module is off, but its scheduled jobs could not be removed; they do nothing while "
    "the module is off."
)


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable weather` again once the fault is cleared."


def module_on_weather(*, modules: "ModuleService") -> Setting:
    """`module.on:weather`: raise the flag in one save, with its audit entry."""

    async def check(_ctx: CheckContext) -> Verdict:
        if await modules.is_weather_enabled():
            return Verdict.refuse("⚠️ Weather module is already enabled.")
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        await db.execute("UPDATE server_configs SET weather_module_enabled = 1")
        return Saved(
            changed=True,
            audits=(AuditRecord(change_type="MODULE_ENABLE", old_value={}, new_value={"module": "weather"}),),
        )

    def reply(_payload: dict[str, Any], _results: Mapping[str, dict[str, Any]]) -> str:
        return "✅ Weather module enabled."

    return Setting(
        kind=module_on("weather"),
        command=lambda _payload: _ENABLE_COMMAND,
        doing=lambda _payload: "Turning the weather module on",
        check=check,
        save=save,
        reply=reply,
        writes=("server_configs",),
        not_saved=_still_off,
    )


def _remove_jobs(scheduler: "SchedulerService") -> After:
    """The step that removes the season's weather jobs once the flag is down.

    It may fail without holding up the queue: the switch-off stays saved, and the reply says the
    jobs could not be removed and do nothing while the module is off.
    """

    async def run(_ctx: StepContext) -> StepResult:
        await scheduler.cancel_all_weather()
        return StepResult()

    def fault_reply(_payload: dict[str, Any], reply: str) -> str:
        # The base reply claims every job was cancelled; a fault here means it was not, so that
        # claim is dropped rather than left standing beside the warning.
        disabled = reply.split(".", 1)[0] + "."
        return f"{disabled}\n{_JOBS_NOT_REMOVED}"

    return After(step=Step(REMOVE_JOBS, StepKind.ACT, run), fault_reply=fault_reply)


def module_off_weather(*, modules: "ModuleService", scheduler: "SchedulerService") -> Setting:
    """`module.off:weather`: lower the flag and write its audit entry in one save, then remove
    the season's weather jobs in a step of its own."""

    async def check(_ctx: CheckContext) -> Verdict:
        if not await modules.is_weather_enabled():
            return Verdict.refuse("⚠️ Weather module is already disabled.")
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        await db.execute("UPDATE server_configs SET weather_module_enabled = 0")
        return Saved(
            changed=True,
            audits=(AuditRecord(change_type="MODULE_DISABLE", old_value={"module": "weather"}, new_value={}),),
            then=(PlannedStep(REMOVE_JOBS),),
        )

    def reply(_payload: dict[str, Any], _results: Mapping[str, dict[str, Any]]) -> str:
        return "✅ Weather module disabled. All scheduled weather jobs have been cancelled."

    return Setting(
        kind=module_off("weather"),
        command=lambda _payload: _DISABLE_COMMAND,
        doing=lambda _payload: "Turning the weather module off",
        check=check,
        save=save,
        reply=reply,
        writes=("server_configs",),
        after={REMOVE_JOBS: _remove_jobs(scheduler)},
    )


__all__ = ["REMOVE_JOBS", "module_off_weather", "module_on_weather"]
