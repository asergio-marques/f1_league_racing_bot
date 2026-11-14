"""The five `/signup` settings that are not the channel (`channel_setting`) or the close time, as changes on the queue.

`/signup nationality`, `time-type`, `time-image`, `time-slot add` and `time-slot remove` each ask
one change, `signup.setting.<name>` (underscores in place of the command's hyphens), built from
the `Setting` below by `setting_change` (`core/services/queue/setting_change.py`, which holds what
every settings change does with a fault). The value, its audit entry and its log line are one
save, stamped by the queue's clock; the command holds no rule of its own.

**The refusals are the check, in the command's order and words,** made at the ask and again as the
change starts: the module on, the configuration not fixed by a confirmed season, then each
command's own (the slot limit, the time, a slot already held; no slot, or none of that number).
They are judged here and not in the command alone because a change ahead on the queue may have
moved the slots since the command asked.

**A slot is removed by its identity, not its rank.** The rank a manager types is the slot's place
in the list as it stood when they asked, and a removal ahead of theirs renumbers the list: asked
twice before either runs, `time-slot remove 1` would remove two slots. The command resolves the
rank to the slot's day and time at the ask and the change carries those, so that the second removal
finds its slot gone and is refused (`test_removing_slot_n_twice_removes_one_slot`).

**A slot already held is refused by name:** the check judges it, and the save, which reads nothing
before it writes, turns the database's own `sqlite3.IntegrityError` into the same refusal. A fault
of any other kind after the insert is a fault, never read as a duplicate
(`test_a_fault_after_a_slot_is_saved_is_not_read_as_a_duplicate`).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import AuditRecord, Verdict
from leaguebot.core.models.refusal import Refused
from leaguebot.core.services.queue.change_types import CheckContext
from leaguebot.core.services.queue.setting_change import Saved, Setting
from leaguebot.core.services.seasons.season_lifecycle_service import configuration_fixed
from leaguebot.core.utils.time_parsing import parse_time_of_day
from leaguebot.signup.models.signup_module import AvailabilitySlot
from leaguebot.signup.services.signup_module_service import settings_on, slots_on

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

KIND_PREFIX = "signup.setting."

#: The most availability slots a league may hold.
MAX_SLOTS = 25

NOT_ENABLED = "⛔ Signup module is not enabled. Use `/module enable signup` first."
NO_SLOTS = "❌ No slots configured."
DUPLICATE_SLOT = "❌ That time slot already exists."

def kind_of(command: str) -> str:
    """The kind of change of `/signup <command>`, *command* being the word the manager types
    ("time-slot add" included)."""
    return KIND_PREFIX + command.replace(" ", "_").replace("-", "_")


def format_slots(slots: list[AvailabilitySlot]) -> str:
    """The slots as `/signup time-slot list` and the slot commands' replies show them."""
    if not slots:
        return "No availability slots configured."
    lines = ["**Availability Time Slots**"]
    lines.extend(f"#{slot.slot_sequence_id} — {slot.display_label}" for slot in slots)
    return "\n".join(lines)


def fixed_refusal(season_number: int, command: str) -> str:
    """The refusal of a signup setting asked while a season's configuration has fixed them."""
    return (
        f"❌ The signup module's settings are fixed for Season {season_number} now that "
        f"its configuration has been confirmed. `{command}` is available again once the "
        "season has ended, or while a new season is in configuration."
    )


def _toggle(command: str, field: str, *, modules: "ModuleService") -> Setting:
    """The setting of a toggle: *field* of the module's settings, flipped."""
    name = f"/signup {command}"

    async def check(ctx: CheckContext) -> Verdict:
        refusal = await open_to_change(modules, ctx.db_path, name)
        return Verdict.refuse(refusal) if refusal else Verdict.go()

    async def save(db: "aiosqlite.Connection", _payload: dict[str, Any]) -> Saved:
        settings = await settings_on(db)
        old = getattr(settings, field)
        new = (
            ("SHORT_QUALIFICATION" if old == "TIME_TRIAL" else "TIME_TRIAL")
            if field == "time_type" else not old
        )
        setattr(settings, field, new)
        await db.execute(
            "INSERT INTO signup_module_settings "
            "(id, nationality_required, time_type, time_image_required) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "nationality_required = excluded.nationality_required, "
            "time_type = excluded.time_type, "
            "time_image_required = excluded.time_image_required",
            (
                int(settings.nationality_required),
                settings.time_type,
                int(settings.time_image_required),
            ),
        )
        return Saved(
            changed=True,
            audits=(
                AuditRecord(
                    "SIGNUP_SETTINGS_CHANGE",
                    {"field": field, "value": old},
                    {"field": field, "value": new},
                ),
            ),
            details=(f"{field}: {new}",),
            result={"value": new},
        )

    def reply(_payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        value = results["save"]["value"]
        if field == "time_type":
            label = "Time Trial" if value == "TIME_TRIAL" else "Short Qualification"
            return f"✅ Time type: **{label}**."
        state = "**ON**" if value else "**OFF**"
        what = "Nationality" if field == "nationality_required" else "Time image"
        return f"✅ {what} requirement: {state}."

    doing = {
        "nationality_required": "Toggling the nationality requirement",
        "time_type": "Switching the time type",
        "time_image_required": "Toggling the time image requirement",
    }[field]
    return Setting(
        kind=kind_of(command),
        command=lambda _payload: name,
        doing=lambda _payload: doing,
        check=check,
        save=save,
        reply=reply,
        writes=("signup_module_settings",),
    )


async def open_to_change(
    modules: "ModuleService", db_path: str, command: str,
    *, repairing: Callable[[], Awaitable[bool]] | None = None,
) -> str | None:
    """The words refusing a change to the signup settings: the module off, or a season whose
    confirmed configuration has fixed them. None where it may go ahead.

    A setting that repairs what the league cannot do without passes *repairing*, which says
    whether the change is a repair now: a fixed configuration then does not refuse it."""
    if not await modules.is_signup_enabled():
        return NOT_ENABLED
    season_number = await configuration_fixed(db_path)
    if season_number is not None and not (repairing is not None and await repairing()):
        return fixed_refusal(season_number, command)
    return None


def _slot_add(*, modules: "ModuleService", signup: "SignupModuleService") -> Setting:
    name = "/signup time-slot add"

    async def check(ctx: CheckContext) -> Verdict:
        refusal = await open_to_change(modules, ctx.db_path, name)
        if refusal:
            return Verdict.refuse(refusal)
        slots = await signup.get_slots()
        if len(slots) >= MAX_SLOTS:
            return Verdict.refuse(f"❌ Maximum of {MAX_SLOTS} time slots reached.")
        raw = ctx.payload["time"]
        time = parse_time_of_day(raw)
        if time is None:
            return Verdict.refuse(
                f"❌ Could not parse time '{raw}'. Use HH:MM 24h or 12h with am/pm."
            )
        day = ctx.payload["day"]
        if any(slot.day_of_week == day and slot.time_hhmm == time for slot in slots):
            return Verdict.refuse(DUPLICATE_SLOT)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        day = payload["day"]
        time = parse_time_of_day(payload["time"])
        assert time is not None  # judged in the check, which a change is never saved without
        try:
            await db.execute(
                "INSERT INTO signup_availability_slots (day_of_week, time_hhmm) VALUES (?, ?)",
                (day, time),
            )
        except sqlite3.IntegrityError as error:
            raise Refused(DUPLICATE_SLOT) from error
        return Saved(
            changed=True,
            audits=(
                AuditRecord(
                    "SIGNUP_SLOT_ADD",
                    {},
                    # The durable slot ID, not the display ordinal: an audit entry outlives
                    # the list it was written against.
                    {"day": day, "time": time, "slot_id": AvailabilitySlot.make_slot_id(day, time)},
                ),
            ),
            details=(f"day: {payload['day_name']}", f"time: {time}"),
            result={"slots": format_slots(await slots_on(db))},
        )

    return Setting(
        kind=kind_of("time-slot add"),
        command=lambda _payload: name,
        doing=lambda payload: (
            f"Adding the time slot {payload['day_name']} {parse_time_of_day(payload['time']) or payload['time']}"
        ),
        check=check,
        save=save,
        reply=lambda _payload, results: results["save"]["slots"],
        writes=("signup_availability_slots",),
    )


def _slot_remove(*, modules: "ModuleService", signup: "SignupModuleService") -> Setting:
    name = "/signup time-slot remove"

    async def check(ctx: CheckContext) -> Verdict:
        refusal = await open_to_change(modules, ctx.db_path, name)
        if refusal:
            return Verdict.refuse(refusal)
        slots = await signup.get_slots()
        if not slots:
            return Verdict.refuse(NO_SLOTS)
        rank, day, time = ctx.payload["rank"], ctx.payload["day"], ctx.payload["time"]
        if day is None:
            return Verdict.refuse(f"❌ Slot #{rank} does not exist.")
        if not any(slot.day_of_week == day and slot.time_hhmm == time for slot in slots):
            label = AvailabilitySlot.make_label(day, time)
            return Verdict.refuse(f"❌ Slot #{rank} ({label}) no longer exists.")
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        day, time = payload["day"], payload["time"]
        removed = await db.execute(
            "DELETE FROM signup_availability_slots WHERE day_of_week = ? AND time_hhmm = ?",
            (day, time),
        )
        if removed.rowcount == 0:
            raise Refused(f"❌ Slot #{payload['rank']} no longer exists.")
        return Saved(
            changed=True,
            audits=(
                AuditRecord(
                    "SIGNUP_SLOT_REMOVE",
                    # The durable slot ID, not the display ordinal the manager typed.
                    {"slot_id": AvailabilitySlot.make_slot_id(day, time), "day": day, "time": time},
                    {},
                ),
            ),
            details=(
                f"slot_id: {payload['rank']}",
                f"slot: {AvailabilitySlot.make_label(day, time)}",
            ),
            result={"slots": format_slots(await slots_on(db))},
        )

    return Setting(
        kind=kind_of("time-slot remove"),
        command=lambda _payload: name,
        doing=lambda payload: f"Removing time slot #{payload['rank']}",
        check=check,
        save=save,
        reply=lambda _payload, results: results["save"]["slots"],
        writes=("signup_availability_slots",),
    )


def signup_settings(
    *, modules: "ModuleService", signup: "SignupModuleService"
) -> tuple[Setting, ...]:
    """The five settings: the three toggles, then the slot's add and remove."""
    return (
        _toggle("nationality", "nationality_required", modules=modules),
        _toggle("time-type", "time_type", modules=modules),
        _toggle("time-image", "time_image_required", modules=modules),
        _slot_add(modules=modules, signup=signup),
        _slot_remove(modules=modules, signup=signup),
    )


__all__ = [
    "DUPLICATE_SLOT", "KIND_PREFIX", "MAX_SLOTS", "NO_SLOTS", "NOT_ENABLED", "fixed_refusal",
    "format_slots", "kind_of", "open_to_change", "signup_settings",
]
