"""The eight `/attendance config` settings as changes on the queue.

Each of `/attendance config rsvp-notice`, `rsvp-last-notice`, `rsvp-deadline`, `no-rsvp-penalty`,
`absent-penalty`, `no-show-penalty`, `autosack` and `autoreserve` asks one change, `attendance.setting.<name>`
with the underscores of the command's name, built from the `Setting` below by `setting_change`
(`core/services/queue/setting_change.py`, which holds what every settings change does with a value
already held and with a fault). The change carries ``{"value": N}``: a threshold of 0, which
disables it, is carried as ``None``, as the column stores it. The value, its `ATTENDANCE_CONFIG_SET`
audit entry and its log line are one save, stamped by the queue's clock.

**The refusals are the check, in the command's order and words,** made at the ask and again as the
change starts: the module on; for the three timings, no season's placements confirmed; the value
in range; the configuration there; then the rules that compare a setting with another. Those are
the timing invariant (`validate_timing_invariant`: the first notice before the last, the last
before the deadline) and the exclusion of auto-sack and auto-reserve. They are judged here, by the
service that writes the settings, and not in the command alone: a change ahead on the queue may
have moved the other setting, or a season may have been confirmed, since the command asked
("A rule that compares one setting with another", `docs/design/architecture.md`). Settings run
one at a time on the league lane, so what the check reads as the change starts is what the save
finds.

**The value held is read in the save,** on the connection that writes, never at the ask: a value
already held writes nothing and records nothing, and is answered as "Nothing changed".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from leaguebot.attendance.models.attendance import AttendanceConfig
from leaguebot.attendance.services.check_in.attendance_config_service import validate_timing_invariant
from leaguebot.attendance.services.settings.channel_settings import NOT_ENABLED
from leaguebot.core.models.change import AuditRecord, Verdict
from leaguebot.core.services.queue.change_types import CheckContext
from leaguebot.core.services.queue.setting_change import Saved, Setting

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.seasons.season_service import SeasonService

KIND_PREFIX = "attendance.setting."

NOT_ENABLED_HERE = f"{NOT_ENABLED} Use `/module enable attendance` first."
NO_CONFIGURATION = "❌ No attendance configuration found. Enable the module first."
PLACEMENTS_CONFIRMED = (
    "❌ Attendance configuration cannot be changed once a season's placements are confirmed."
)


def _days(value: int | None) -> str:
    return f"{value} day(s)"


def _hours(value: int | None) -> str:
    return f"{value} hour(s)"


def _hours_or_disabled(value: int | None) -> str:
    return "disabled" if not value else f"{value} hour(s)"


def _points(value: int | None) -> str:
    return f"{value} point(s)"


def _points_or_disabled(value: int | None) -> str:
    return "disabled" if value is None else f"{value} point(s)"


@dataclass(frozen=True)
class _Spec:
    """One setting: *command* is its word in `/attendance config`, *column* the column it sets,
    *name* what the line and the reply call it, *shown* how a value reads, *reply* the answer to
    a value set, *timing* whether it is fixed once a season's placements are confirmed,
    *minimum* the least value it takes (a threshold's disabled is None, carried apart from
    it) and *too_low* the words refusing one below it, and
    *judge* the rule that compares it with another setting, given the value and the
    configuration, returning the words refusing it or None."""

    command: str
    column: str
    name: str
    shown: Callable[[int | None], str]
    reply: Callable[[int | None], str]
    timing: bool
    minimum: int
    too_low: str
    judge: Callable[[int | None, AttendanceConfig], str | None]

    @property
    def kind(self) -> str:
        return KIND_PREFIX + self.command.replace("-", "_")


def _invariant(
    place: str,
) -> Callable[[int | None, AttendanceConfig], str | None]:
    """The timing invariant with the value in *place* ("notice", "last" or "deadline")."""

    def judge(value: int | None, cfg: AttendanceConfig) -> str | None:
        assert value is not None
        times = {
            "notice": cfg.rsvp_notice_days,
            "last": cfg.rsvp_last_notice_hours,
            "deadline": cfg.rsvp_deadline_hours,
        }
        times[place] = value
        error = validate_timing_invariant(times["notice"], times["last"], times["deadline"])
        return None if error is None else f"❌ {error}"

    return judge


def _no_rule(_value: int | None, _cfg: AttendanceConfig) -> str | None:
    return None


def _not_with_autoreserve(value: int | None, cfg: AttendanceConfig) -> str | None:
    if value is not None and cfg.autoreserve_threshold:
        return (
            "❌ Cannot set auto-sack while auto-reserve is active. "
            "Disable auto-reserve first (`/attendance config autoreserve 0`)."
        )
    return None


def _not_with_autosack(value: int | None, cfg: AttendanceConfig) -> str | None:
    if value is not None and cfg.autosack_threshold:
        return (
            "❌ Cannot set auto-reserve while auto-sack is active. "
            "Disable auto-sack first (`/attendance config autosack 0`)."
        )
    return None


def _last_notice_reply(value: int | None) -> str:
    if not value:
        return "✅ Last RSVP reminder **disabled** (set to 0)."
    return f"✅ Last RSVP reminder set to **{value}** hour(s) before the race."


def _autosack_reply(value: int | None) -> str:
    if value is None:
        return "✅ Auto-sack **disabled**."
    # Autosack reaches every division, whichever one's points carried a driver over; a league
    # wanting one division alone is pointed at autoreserve.
    return (
        f"✅ Auto-sack threshold set to **{value}** point(s).\n"
        "A driver who reaches it is removed from **every seat in every division**, "
        "whichever division's points carried them over. To drop a driver to reserve "
        "only in the division where they missed rounds, use "
        "`/attendance config autoreserve` instead."
    )


def _autoreserve_reply(value: int | None) -> str:
    if value is None:
        return "✅ Auto-reserve **disabled**."
    return f"✅ Auto-reserve threshold set to **{value}** point(s)."


_PENALTY_LOW = "❌ Penalty points cannot be negative."
_THRESHOLD_LOW = "❌ Threshold cannot be negative."

SPECS: tuple[_Spec, ...] = (
    _Spec(
        "rsvp-notice", "rsvp_notice_days", "RSVP notice", _days,
        lambda v: f"✅ RSVP notice set to **{v}** day(s) before the race.",
        True, 1, "❌ `rsvp_notice_days` must be at least 1.", _invariant("notice"),
    ),
    _Spec(
        "rsvp-last-notice", "rsvp_last_notice_hours", "last RSVP reminder", _hours_or_disabled,
        _last_notice_reply, True, 0, "❌ `rsvp_last_notice_hours` cannot be negative.",
        _invariant("last"),
    ),
    _Spec(
        "rsvp-deadline", "rsvp_deadline_hours", "RSVP deadline", _hours,
        lambda v: f"✅ RSVP deadline set to **{v}** hour(s) before the race.",
        True, 0, "❌ `rsvp_deadline_hours` cannot be negative.", _invariant("deadline"),
    ),
    _Spec(
        "no-rsvp-penalty", "no_rsvp_penalty", "no-RSVP penalty", _points,
        lambda v: f"✅ No-RSVP penalty set to **{v}** point(s).",
        False, 0, _PENALTY_LOW, _no_rule,
    ),
    _Spec(
        "absent-penalty", "absent_penalty", "absent penalty", _points,
        lambda v: f"✅ Absent penalty set to **{v}** point(s).",
        False, 0, _PENALTY_LOW, _no_rule,
    ),
    _Spec(
        "no-show-penalty", "no_show_penalty", "no-show penalty", _points,
        lambda v: f"✅ No-show penalty set to **{v}** point(s).",
        False, 0, _PENALTY_LOW, _no_rule,
    ),
    _Spec(
        "autosack", "autosack_threshold", "auto-sack threshold", _points_or_disabled,
        _autosack_reply, False, 0, _THRESHOLD_LOW, _not_with_autoreserve,
    ),
    _Spec(
        "autoreserve", "autoreserve_threshold", "auto-reserve threshold", _points_or_disabled,
        _autoreserve_reply, False, 0, _THRESHOLD_LOW, _not_with_autosack,
    ),
)

#: The kind of change of each `/attendance config` command, by the command's word.
KINDS: dict[str, str] = {spec.command: spec.kind for spec in SPECS}

#: The column of each penalty's kind of change: the three settings a season's recalculation
#: follows while it is ongoing (`penalty_recalculation`).
PENALTY_COLUMNS: dict[str, str] = {
    spec.kind: spec.column
    for spec in SPECS
    if spec.command in ("no-rsvp-penalty", "absent-penalty", "no-show-penalty")
}

#: The columns of `attendance_config` a manager sets: the only names the saves write.
SETTING_COLUMNS: frozenset[str] = frozenset(spec.column for spec in SPECS)


def _setting(
    spec: _Spec,
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    attendance: "AttendanceService",
) -> Setting:
    """The setting of *spec*, its module, season and configuration read through the services
    handed in."""
    async def check(ctx: CheckContext) -> Verdict:
        value = ctx.payload["value"]
        if not await modules.is_attendance_enabled():
            return Verdict.refuse(NOT_ENABLED_HERE)
        if spec.timing and await seasons.get_confirmed_season() is not None:
            return Verdict.refuse(PLACEMENTS_CONFIRMED)
        if value is not None and value < spec.minimum:
            return Verdict.refuse(spec.too_low)
        cfg = await attendance.get_config()
        if cfg is None:
            return Verdict.refuse(NO_CONFIGURATION)
        refusal = spec.judge(value, cfg)
        return Verdict.refuse(refusal) if refusal else Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        value = payload["value"]
        row = await (
            await db.execute(f"SELECT {spec.column} FROM attendance_config")  # noqa: S608
        ).fetchone()
        if row is None:
            raise ValueError("No attendance configuration found.")
        old = row[0]
        if old == value:
            return Saved(changed=False, details=(f"{spec.name}: {spec.shown(value)}",))
        await db.execute(  # noqa: S608
            f"UPDATE attendance_config SET {spec.column} = ?", (value,)
        )
        return Saved(
            changed=True,
            audits=(
                AuditRecord("ATTENDANCE_CONFIG_SET", {spec.column: old}, {spec.column: value}),
            ),
            details=(f"{spec.name}: {spec.shown(value)} (was {spec.shown(old)})",),
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Setting the {spec.name} to {spec.shown(payload['value'])}"

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        value = payload["value"]
        if not results["save"]["changed"]:
            return f"ℹ️ Nothing changed: the {spec.name} is already **{spec.shown(value)}**."
        return spec.reply(value)

    return Setting(
        kind=spec.kind,
        command=lambda _payload: f"/attendance config {spec.command}",
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("attendance_config",),
    )


def attendance_config_settings(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    attendance: "AttendanceService",
) -> tuple[Setting, ...]:
    """The eight settings, in the order of `SPECS`."""
    return tuple(
        _setting(spec, modules=modules, seasons=seasons, attendance=attendance)
        for spec in SPECS
    )


__all__ = ["KINDS", "KIND_PREFIX", "PENALTY_COLUMNS", "SETTING_COLUMNS", "attendance_config_settings"]
