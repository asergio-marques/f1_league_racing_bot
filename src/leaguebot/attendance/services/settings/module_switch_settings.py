"""Attendance's own switch, on and off, as two settings on the change queue.

`/module enable attendance` and `/module disable attendance` each ask one change built from a
`Setting` below by `setting_change` (`framework/services/queue/setting_change.py`, which holds what every
settings change does with a value already held and with a fault): `module_on("attendance")` and
`module_off("attendance")` (`framework/models/change.py`), so core's cog names the change without
importing attendance. `docs/design/architecture.md`, "How modules and core fit together", designs
the gap this closes: weather's switch (`weather/services/module_switch_settings.py`) is the model.

**The switch-on is one save.** The flag is raised and the audit entry written together, so a fault
anywhere in the save leaves none of it: never the module on with nothing configured, and never a
part-made configuration a later enable would build on. A league's first switch-on creates the
row with the packaged values, the schema's column defaults; every later one finds the row the
switch-off kept and raises only the flag, so the league's timings, penalties and thresholds come
back as they were (Principle X.6), pinned by
`tests/core/test_module_enable_disable.py::test_re_enabling_attendance_keeps_the_league_s_settings`.

**The switch-on checks the channels attendance kept while it was off, before its save**
(`core/services/modules/kept_channels.py`, handed `division_channels.kept_channels`): a check-in
call or attendance channel gone from the server is named, one another setting took meanwhile is
named and cleared by the save through the column's own writer, audited as that channel's own
change (from the channel to none), with a line beneath the Success line for each, and one the bot
cannot use keeps attendance off, refused. A check that cannot be made ends the switch-on on a
fault, nothing saved.

**The switch-off is one save: `AttendanceService.switch_off_on` and the audit entry.** That
method lowers the flag and keeps the settings row and every division's check-in and attendance
channels, so the module comes back as it was left (Principle X.6); it clears only each
division's attendance-sheet message id, which is not configuration. It runs on the connection
the save is handed, which is why `attendance_division_config` stays among the table's declared
`writes`. The
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

from leaguebot.attendance.models.switch import SWITCH_KEY
from leaguebot.framework.models.change import AuditRecord, PlannedStep, Verdict, module_off, module_on
from leaguebot.core.models.module_dependencies import needs
from leaguebot.framework.services.queue.change_types import CheckContext
from leaguebot.core.services.modules.hub_service import REFRESH_HUB, hub_refresh_after
from leaguebot.core.services.seasons.season_lifecycle_service import (
    FROZEN_FOR_COMPLETION_REFUSAL,
    PLACEMENTS_CONFIRMED_REFUSAL,
    modules_frozen_for_completion,
)
from leaguebot.core.services.modules.kept_channels import (
    found_in,
    kept_channels_check,
    named_reply,
)
from leaguebot.attendance.services.settings.division_channels import (
    kept_channels,
    set_attendance_channel,
    set_rsvp_channel,
)
from leaguebot.framework.services.queue.setting_change import Saved, Setting

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService
    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.framework.utils.hooks import AwaitedHook
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.image.services.render.image_drawing import ImageDrawing

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


#: What clearing each of attendance's channels is: the column's own writer and the change type of
#: the setting that sets it.
_CLEARS = {
    "rsvp": (set_rsvp_channel, "RSVP_CHANNEL_SET"),
    "attendance": (set_attendance_channel, "ATTENDANCE_CHANNEL_SET"),
}


def module_on_attendance(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    channel_uses_read: "AwaitedHook",
    drawing: "ImageDrawing",
) -> Setting:
    """`module.on:attendance`: the channels attendance kept checked, then the flag raised and the
    audit (and the clear of each channel another setting took), in one save; the row is created
    with the packaged values on the first switch-on and kept as the league left it after that.

    *channel_uses_read* is core's hook, which the check asks who holds a kept channel through, and
    *drawing* the image module's, which says whether a picture could attach a file."""

    async def check(_ctx: CheckContext) -> Verdict:
        if await modules.is_enabled(SWITCH_KEY):
            return Verdict.refuse("⚠️ Attendance module is already enabled.")
        for required in needs("attendance"):
            if not await modules.is_enabled(required):
                return Verdict.refuse(
                    f"❌ The {_MODULE_LABEL['attendance']} requires the "
                    f"{_MODULE_LABEL[required]} to be enabled first."
                )
        if await seasons.get_confirmed_season() is not None:
            return Verdict.refuse(PLACEMENTS_CONFIRMED_REFUSAL)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        named, taken, details = found_in(payload)
        # The packaged values are the column defaults of `attendance_config`, held once there:
        # a first switch-on inserts the row naming only the flag, so the eight settings take the
        # schema's defaults; a row already kept is left as it is and only its flag is raised.
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled) VALUES (1, 1) "
            "ON CONFLICT(id) DO UPDATE SET module_enabled = 1"
        )
        audits = [
            AuditRecord(change_type="ATTENDANCE_MODULE_ENABLED", old_value={}, new_value={})
        ]
        for each in taken:
            write, change_type = _CLEARS[each.setting]
            old_id = await write(db, each.division_id, None)
            if old_id is not None:
                audits.append(
                    AuditRecord(
                        change_type,
                        {"channel_id": old_id},
                        {"channel_id": None},
                        each.division_id,
                    )
                )
        return Saved(
            changed=True,
            audits=tuple(audits),
            details=tuple(details),
            result={"named": named},
            then=(PlannedStep(REFRESH_HUB),),
        )

    def reply(_payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        return named_reply("✅ Attendance module enabled.", results["save"]["named"])

    return Setting(
        kind=module_on("attendance"),
        command=lambda _payload: _ENABLE_COMMAND,
        doing=lambda _payload: "Turning the attendance module on",
        check=check,
        save=save,
        reply=reply,
        writes=("attendance_config", "attendance_division_config"),
        not_saved=_still_off,
        before=(
            kept_channels_check(
                lambda: kept_channels(drawing), command=_ENABLE_COMMAND,
                channel_uses_read=channel_uses_read,
            ),
        ),
        after={REFRESH_HUB: hub_refresh_after(_ENABLE_COMMAND)},
    )


def module_off_attendance(*, modules: "ModuleService", attendance: "AttendanceService") -> Setting:
    """`module.off:attendance`: `AttendanceService.switch_off_on` and the audit entry, in one
    save. It keeps the settings row and the division channels and clears only the sheet message
    ids, as the cascade from turning Results & Standings off does through the same method."""

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_enabled(SWITCH_KEY):
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
