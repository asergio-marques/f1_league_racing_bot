"""Attendance's own switch, on and off, as two settings on the change queue.

`/module enable attendance` and `/module disable attendance` each ask one change built from a
`Setting` below by `setting_change` (`core/services/queue/setting_change.py`, which holds what every
settings change does with a value already held and with a fault): `module_on("attendance")` and
`module_off("attendance")` (`core/models/change.py`), so core's cog names the change without
importing attendance. `docs/design/architecture.md`, "How modules and core fit together", designs
the gap this closes: weather's switch (`weather/services/module_switch_settings.py`) is the model.

**The switch-on is one save.** The packaged values are written over any stored row and the flag
raised, with the audit entry, so a fault anywhere in the save leaves none of it: never the module
on with nothing configured, and never a part-made configuration a later enable would build on.
Writing the packaged values over a row already there, rather than leaving it, is the Principle
X.6 reset: re-enabling starts fresh, pinned by
`tests/core/test_module_enable_disable.py::test_re_enabling_attendance_starts_from_the_packaged_defaults`.
The switch-off keeps the settings row rather than clearing it; issue #449 would keep it across
a switch-off and re-enable without the reset instead.

**The switch-off is one save: `AttendanceService.switch_off_on` and the audit entry.** That
method lowers the flag and clears every division's check-in and attendance channel bindings —
stale bindings the module would otherwise post into the moment it came back on, against a season
that had moved on — while keeping the settings row, on the connection the save is handed. The
cascade from turning Results & Standings off calls the same method on its own save's connection
(`results_off_change.py`), so the plain disable and the cascade leave attendance in the same
state either way.

**The switch-on's check reads the dependency table and the season's stage again as the change
starts,** not only at the ask (`module_cog.py`'s stage refusal): asked while a module attendance
`needs` is on, or while no season's placements are confirmed, and run once that module has gone
off first or an approval has confirmed placements, the save still finds it so and refuses, rather
than switching attendance on behind what the league was told.

**The switch-off's check reads Pending completion again as the change starts,** as
`results_off_change`'s already does: asked before the season reaches it, and run once a change
waiting ahead of it on the lane has finished the last division, the save still finds the season
Pending completion and refuses, rather than switching attendance off in it.

**Each switch asks the hub's panel to be refreshed, once, as an `After` step**
(`hub_service.hub_refresh_after`), rather than the command doing it after every press.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import AuditRecord, PlannedStep, Verdict, module_off, module_on
from leaguebot.core.models.module_dependencies import needs
from leaguebot.core.services.queue.change_types import CheckContext
from leaguebot.core.services.modules.hub_service import REFRESH_HUB, hub_refresh_after
from leaguebot.core.services.seasons.season_lifecycle_service import (
    FROZEN_FOR_COMPLETION_REFUSAL,
    PLACEMENTS_CONFIRMED_REFUSAL,
    modules_frozen_for_completion,
)
from leaguebot.core.services.queue.setting_change import Saved, Setting

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.attendance.services.attendance_config_service import AttendanceService
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.seasons.season_service import SeasonService

_ENABLE_COMMAND = "/module enable attendance"
_DISABLE_COMMAND = "/module disable attendance"


async def attendance_enabled_on(db: "aiosqlite.Connection") -> bool:
    """Whether attendance is on, read on *db*: the module's switch reader, which the builder signs
    up with `ModuleService` and the module's own work asks. A league that has never switched it on
    has no row, and reads as off."""
    row = await (await db.execute("SELECT module_enabled FROM attendance_config")).fetchone()
    return row is not None and bool(row["module_enabled"])


#: How each module the dependency table may name reads in attendance's own refusal. Covers only
#: what `needs("attendance")` names today; a further entry needs a label added here too, or the
#: refusal raises `KeyError` rather than naming it.
_MODULE_LABEL = {
    "attendance": "Attendance module",
    "results": "Results & Standings module",
}


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable attendance` again once the fault is cleared."


def _nothing_saved(_payload: dict[str, Any]) -> str:
    return "Nothing was saved. Run `/module disable attendance` again to retry."


def module_on_attendance(*, modules: "ModuleService", seasons: "SeasonService") -> Setting:
    """`module.on:attendance`: the packaged values written over any stored row, the flag raised
    and the audit, in one save (Principle X.6's reset)."""

    async def check(_ctx: CheckContext) -> Verdict:
        if await modules.is_attendance_enabled():
            return Verdict.refuse("⚠️ Attendance module is already enabled.")
        for required in needs("attendance"):
            is_enabled = getattr(modules, f"is_{required}_enabled")
            if not await is_enabled():
                return Verdict.refuse(
                    f"❌ The {_MODULE_LABEL['attendance']} requires the "
                    f"{_MODULE_LABEL[required]} to be enabled first."
                )
        if await seasons.get_confirmed_season() is not None:
            return Verdict.refuse(PLACEMENTS_CONFIRMED_REFUSAL)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        await db.execute(
            "INSERT OR REPLACE INTO attendance_config "
            "(id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
            "rsvp_deadline_hours, no_rsvp_penalty, absent_penalty, no_show_penalty, "
            "autoreserve_threshold, autosack_threshold) "
            "VALUES (1, 1, 5, 24, 2, 1, 1, 1, NULL, NULL)"
        )
        return Saved(
            changed=True,
            audits=(
                AuditRecord(change_type="ATTENDANCE_MODULE_ENABLED", old_value={}, new_value={}),
            ),
            then=(PlannedStep(REFRESH_HUB),),
        )

    def reply(_payload: dict[str, Any], _results: Mapping[str, dict[str, Any]]) -> str:
        return "✅ Attendance module enabled."

    return Setting(
        kind=module_on("attendance"),
        command=lambda _payload: _ENABLE_COMMAND,
        doing=lambda _payload: "Turning the attendance module on",
        check=check,
        save=save,
        reply=reply,
        writes=("attendance_config",),
        not_saved=_still_off,
        after={REFRESH_HUB: hub_refresh_after(_ENABLE_COMMAND)},
    )


def module_off_attendance(*, modules: "ModuleService", attendance: "AttendanceService") -> Setting:
    """`module.off:attendance`: `AttendanceService.switch_off_on` and the audit entry, in one
    save. It keeps the settings row and clears the division channel bindings, as the cascade
    from turning Results & Standings off does through the same method."""

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_attendance_enabled():
            return Verdict.refuse("⚠️ Attendance module is already disabled.")
        if await modules_frozen_for_completion(ctx.db_path):
            return Verdict.refuse(FROZEN_FOR_COMPLETION_REFUSAL)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        await attendance.switch_off_on(db)
        return Saved(
            changed=True,
            audits=(
                AuditRecord(change_type="ATTENDANCE_MODULE_DISABLED", old_value={}, new_value={}),
            ),
            then=(PlannedStep(REFRESH_HUB),),
        )

    def reply(_payload: dict[str, Any], _results: Mapping[str, dict[str, Any]]) -> str:
        return "✅ Attendance module disabled."

    return Setting(
        kind=module_off("attendance"),
        command=lambda _payload: _DISABLE_COMMAND,
        doing=lambda _payload: "Turning the attendance module off",
        check=check,
        save=save,
        reply=reply,
        writes=("attendance_config", "attendance_division_config"),
        not_saved=_nothing_saved,
        after={REFRESH_HUB: hub_refresh_after(_DISABLE_COMMAND)},
    )


__all__ = ["module_off_attendance", "module_on_attendance"]
