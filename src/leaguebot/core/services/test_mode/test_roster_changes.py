"""The test roster's four commands on the change queue: `core.test_roster_add`,
`core.test_roster_add_bulk`, `core.test_roster_remove` and `core.test_roster_clear`.

Each is a change type of its own on the league lane, registered by the builder, judged without a
write at the ask and again as the change starts, and one SAVE of its rows and its audit entries on
the connection the queue hands it. A fault in a save stops the lane, and the discard reply says
nothing was changed. What the add-bulk, remove and clear kinds judge and write is in their
factories' docstrings; what follows is `core.test_roster_add`'s.

`/test-mode roster add` is **a change carrying the fake driver's name**, and nothing more: the
driver is created at Not Signed Up with no seat and no nationality, its ID allocated by the bot.
Its division, team and seat are decided as any driver's, by `/driver assign` once its signup is
approved, and its nationality is the one its signup gave.

**The refusals are the check's**, judged at the ask and again as the change starts, in the league's
log channel either time, in the words the command has always used: test mode not on, and what creating the driver is refused for (`judge_add_on`): the name and no
live season. The save judges once more on its own connection, ending the change in the same words
where one has come up since, changing nothing and not stopping the lane.

**One SAVE** creates the driver (`seat_on`) and writes the `TEST_DRIVER_ADDED` audit entry, which
records the driver and its ID, and the log line, stamped by the queue's clock. The synthetic id is
allocated inside the save, so two drivers never share one.

**A fault in the save stops the lane**, as a failed job does, for the reason `/division delete`'s
does: a league admin exists to discard the job and clear the stop, and nothing is saved. The reply
once it is discarded says nobody was added.
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import (
    AuditRecord,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.framework.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.rounds.round_add_changes import saved_result
from leaguebot.core.services.drivers.driver_service import (
    SIGNUP_IN_PROGRESS,
    divisions_taken_part_in,
)
from leaguebot.core.services.test_mode.test_mode_settings import NOT_IN_TEST_MODE
from leaguebot.core.services.test_mode.test_roster_service import (
    AddPlan,
    BulkPlan,
    FakeDriver,
    delete_test_drivers_on,
    fake_drivers_in_division_on,
    find_test_driver_on,
    judge_add_on,
    judge_bulk_on,
    locate_division_on,
    seat_bulk_on,
    seat_on,
)
from leaguebot.framework.utils.hooks import AwaitedHook
from leaguebot.framework.utils.log_lines import refusal_line, reply_reason
from leaguebot.core.utils.roster_import import ParsedDriver, format_roster_errors

TEST_ROSTER_ADD = "core.test_roster_add"
TEST_ROSTER_ADD_BULK = "core.test_roster_add_bulk"
TEST_ROSTER_REMOVE = "core.test_roster_remove"
TEST_ROSTER_CLEAR = "core.test_roster_clear"

#: The name of the step that creates the driver and writes its audit entry and its line.
SAVE = "save"

#: `/test-mode roster add` as the line names it.
ADD_COMMAND = "/test-mode roster add"

#: What `roster remove` answers a fake driver part way through a signup, and what it and `roster
#: clear` answer one who has raced or holds a confirmed seat, which only `/driver sack` undoes.
SIGNUP_UNDER_WAY = (
    "⛔ **{driver_name}** has a signup in progress. Finish it — approve or reject it — or cancel "
    "it first."
)
KEPT_REMOVAL = (
    "⛔ **{driver_name}** has raced or holds a confirmed seat. Run `/driver sack` first; a fake "
    "driver who has raced stays until test mode ends."
)
KEPT_CLEARING = (
    "⛔ **{division}** holds fake drivers who have raced or hold a confirmed seat: {names}. "
    "Run `/driver sack` first; a fake driver who has raced stays until test mode ends."
)

#: `/test-mode roster add-bulk` as the line names it.
ADD_BULK_COMMAND = "/test-mode roster add-bulk"

#: The reply once a league admin has discarded the stopped save of a roster: nobody was added.
ROSTER_NOT_ADDED = (
    "Nothing was added: no test driver was created. Run `/test-mode roster add-bulk` again."
)

#: `/test-mode roster remove` as the line names it.
REMOVE_COMMAND = "/test-mode roster remove"

#: The reply once a league admin has discarded the stopped save: nobody was removed. The first form
#: names the division the driver is seated in, the second serves a driver who holds no seat.
NOBODY_REMOVED = (
    "Nothing was removed: **{driver_name}** is still seated in **{division}**. "
    "Run `/test-mode roster remove` again."
)
NOBODY_REMOVED_UNSEATED = (
    "Nothing was removed: **{driver_name}** is still on the server. "
    "Run `/test-mode roster remove` again."
)

#: `/test-mode roster clear` as the line names it.
CLEAR_COMMAND = "/test-mode roster clear"

#: The reply once a league admin has discarded the stopped save: nobody was removed.
NOBODY_CLEARED = (
    "Nothing was removed: the fake drivers in **{division}** are still seated. "
    "Run `/test-mode roster clear` again."
)

#: The reply once a league admin has discarded the stopped save: nobody was added.
NOBODY_ADDED = (
    "Nothing was added: **{driver_name}** was not created. Run `/test-mode roster add` again."
)


async def standing_refusal_on(db: aiosqlite.Connection) -> str | None:
    """What a roster command is refused for before it looks at what it names, in its words: test mode
    not on. The roster changes in any stage of the season while it is."""
    cursor = await db.execute("SELECT test_mode_active FROM server_configs")
    config = await cursor.fetchone()
    if config is None or not config["test_mode_active"]:
        return NOT_IN_TEST_MODE
    return None


async def has_raced_or_holds_a_confirmed_seat(
    db: aiosqlite.Connection, driver: FakeDriver, divisions_raced_read: AwaitedHook
) -> bool:
    """Whether the fake driver has raced or holds a confirmed seat: the former-driver flag is set,
    or a division is one they took part in, as `/driver reassign` judges it
    (`divisions_taken_part_in`), the modules asked through *divisions_raced_read*. Such a driver is
    removed only by `/driver sack`, or by test mode ending."""
    if driver.former_driver:
        return True
    taken = await divisions_taken_part_in(
        db, driver.profile_id, [driver.discord_user_id], divisions_raced_read
    )
    return bool(taken)


def test_roster_add_change() -> ChangeType:
    """The `core.test_roster_add` change type, for the builder to register."""

    async def judge(db: aiosqlite.Connection, payload: dict[str, Any]) -> AddPlan | str:
        """The plan to create the driver, or the reply refusing it."""
        refusal = await standing_refusal_on(db)
        if refusal is not None:
            return refusal
        judged = await judge_add_on(db, str(payload["driver_name"]))
        return f"⛔ {judged}" if isinstance(judged, str) else judged

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        async with get_connection(ctx.db_path) as db:
            judged = await judge(db, ctx.payload)
        return Verdict.refuse(judged) if isinstance(judged, str) else Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Create the driver, and write the audit entry and the line."""
        judged = await judge(db, ctx.payload)
        if isinstance(judged, str):
            return StepResult(
                result={"refused": judged},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(judged)),),
            )
        created = await seat_on(db, judged)
        new = {"driver": created["display_name"], "user_id": str(created["discord_user_id"])}
        line = (
            f"{ctx.named} | {ADD_COMMAND} | Success\n"
            f"  driver: {created['display_name']}"
        )
        return StepResult(
            result={"saved": True, **new},
            audits=(AuditRecord("TEST_DRIVER_ADDED", dict.fromkeys(new), new),),
            lines=(line,),
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Adding fake driver **{payload['driver_name']}**"

    def outcome(ctx: OutcomeContext) -> str:
        saved = saved_result(ctx)
        if saved.get("refused"):
            return str(saved["refused"])
        if not saved.get("saved"):
            return NOBODY_ADDED.format(**ctx.payload)
        return (
            f"✅ Added fake driver **{saved['driver']}**.\n"
            f"Mention string (copy-paste into results): `<@{saved['user_id']}>`"
        )

    return ChangeType(
        kind=TEST_ROSTER_ADD,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, save)},
        check=check,
        key=lambda payload: f"{TEST_ROSTER_ADD}|{json.dumps(str(payload['driver_name']))}",
        doing=doing,
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


# ─── /test-mode roster add-bulk ───────────────────────────────────────────────


def bulk_payload(drivers: list[ParsedDriver]) -> dict[str, Any]:
    """The request the import form makes: every driver the file names, as the file names them."""
    return {
        "drivers": [
            {
                "line": driver.line,
                "discord_user_id": driver.discord_user_id,
                "driver_name": driver.driver_name,
            }
            for driver in drivers
        ]
    }


def _drivers_of(payload: dict[str, Any]) -> list[ParsedDriver]:
    return [ParsedDriver(**driver) for driver in payload["drivers"]]


def test_roster_add_bulk_change() -> ChangeType:
    """The `core.test_roster_add_bulk` change type, for the builder to register.

    **The refusals are the check's**, judged at the ask and again as the change starts: test mode
    not on, and every fault the database shows in the roster
    (`judge_bulk_on`), listed together as the form has always listed them. A fault the file shows
    on its own is refused by the form, before the ask. **One SAVE** judges once more on its own
    connection, creates every driver (`seat_bulk_on`) and writes a `TEST_DRIVER_ADDED` audit entry
    for each, in the file's order, and the line. A fault in the save stops the lane, as in
    `/test-mode roster add`.
    """

    async def judge(db: aiosqlite.Connection, payload: dict[str, Any]) -> BulkPlan | str:
        """The plan to create the roster, or the reply refusing it."""
        refusal = await standing_refusal_on(db)
        if refusal is not None:
            return refusal
        judged = await judge_bulk_on(db, _drivers_of(payload))
        return format_roster_errors(judged) if isinstance(judged, list) else judged

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        async with get_connection(ctx.db_path) as db:
            judged = await judge(db, ctx.payload)
        return Verdict.refuse(judged) if isinstance(judged, str) else Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Create every driver, and write the audit entries and the line."""
        judged = await judge(db, ctx.payload)
        if isinstance(judged, str):
            return StepResult(
                result={"refused": judged},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(judged)),),
            )
        created = await seat_bulk_on(db, judged)
        audits = []
        for driver in judged.drivers:
            new = {"driver": driver.driver_name, "user_id": str(driver.discord_user_id)}
            audits.append(AuditRecord("TEST_DRIVER_ADDED", dict.fromkeys(new), new))
        line = (
            f"{ctx.named} | {ADD_BULK_COMMAND} | Success\n"
            f"  drivers_added: {created}"
        )
        return StepResult(
            result={"saved": True, "added": created}, audits=tuple(audits), lines=(line,)
        )

    def doing(payload: dict[str, Any]) -> str:
        count = len(payload["drivers"])
        return f"Adding {count} test driver{'' if count == 1 else 's'}"

    def outcome(ctx: OutcomeContext) -> str:
        saved = saved_result(ctx)
        if saved.get("refused"):
            return str(saved["refused"])
        if not saved.get("saved"):
            return ROSTER_NOT_ADDED
        return (
            f"✅ Added **{saved['added']}** test driver(s).\n"
            f"`/test-mode roster list` shows them with the mentions result submission wants."
        )

    return ChangeType(
        kind=TEST_ROSTER_ADD_BULK,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, save)},
        check=check,
        key=lambda payload: f"{TEST_ROSTER_ADD_BULK}|{json.dumps(payload['drivers'])}",
        doing=doing,
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


# ─── /test-mode roster remove ─────────────────────────────────────────────────


def _removed_entry(found: FakeDriver) -> dict[str, Any]:
    """What the audit entry of a fake driver's removal records of them, as it stood."""
    return {
        "driver": found.display_name,
        "user_id": found.discord_user_id,
        "team": found.team_name,
        "division": found.division_name,
    }


def test_roster_remove_change(
    *, profiles_deleted: AwaitedHook, divisions_raced_read: AwaitedHook
) -> ChangeType:
    """The `core.test_roster_remove` change type, for the builder to register: *profiles_deleted*
    is the hook each module signs up to so that it deletes its own rows of the drivers deleted, and
    *divisions_raced_read* the one results signs up to so that a driver who has raced is known.

    A change type of its own on the league lane, **carrying the synthetic id typed** (`user_id`),
    which the form of the command has read as a number. The ask reads the driver's name and
    division into the payload (`fill`), for the reply of a save a league admin discarded.

    **The refusals are the check's**, judged at the ask and again as the change starts: test mode
    not on, no fake driver holding the id (a real driver's id is one), a signup in progress
    (finish it, or cancel it, first), and a driver who has raced or holds a confirmed seat, which
    `/driver sack` undoes first. **One SAVE** judges once more on its own connection, deletes the driver, keeping
    their history, and writes the `TEST_DRIVER_REMOVED` audit entry, on the division they were
    seated in, and the line. A fault in the save stops the lane.
    """

    async def judge(db: aiosqlite.Connection, payload: dict[str, Any]) -> FakeDriver | str:
        refusal = await standing_refusal_on(db)
        if refusal is not None:
            return refusal
        found = await find_test_driver_on(db, int(payload["user_id"]))
        if isinstance(found, str):
            return f"⛔ {found}"
        if found.state in SIGNUP_IN_PROGRESS:
            return SIGNUP_UNDER_WAY.format(driver_name=found.display_name)
        if await has_raced_or_holds_a_confirmed_seat(db, found, divisions_raced_read):
            return KEPT_REMOVAL.format(driver_name=found.display_name)
        return found

    async def fill(ctx: CheckContext) -> None:
        async with get_connection(ctx.db_path) as db:
            found = await judge(db, ctx.payload)
        if not isinstance(found, str):
            ctx.payload["driver_name"] = found.display_name
            ctx.payload["division"] = found.division_name

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            found = await judge(db, ctx.payload)
        return Verdict.refuse(found) if isinstance(found, str) else Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Delete the driver, and write the audit entry and the line."""
        found = await judge(db, ctx.payload)
        if isinstance(found, str):
            return StepResult(
                result={"refused": found},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(found)),),
            )
        await delete_test_drivers_on(db, [found.profile_id], profiles_deleted=profiles_deleted)
        old = _removed_entry(found)
        line = (
            f"{ctx.named} | {REMOVE_COMMAND} | Success\n"
            f"  driver: {found.display_name}\n"
            f"  team: {found.team_name}\n"
            f"  user_id: {found.discord_user_id}"
        )
        return StepResult(
            result={"saved": True, "driver": found.display_name, "team": found.team_name},
            audits=(
                AuditRecord(
                    "TEST_DRIVER_REMOVED", old, dict.fromkeys(old), division_id=found.division_id
                ),
            ),
            lines=(line,),
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Removing fake driver **{payload.get('driver_name') or payload['user_id']}**"

    def outcome(ctx: OutcomeContext) -> str:
        saved = saved_result(ctx)
        if saved.get("refused"):
            return str(saved["refused"])
        if not saved.get("saved"):
            driver_name = ctx.payload.get("driver_name") or ctx.payload["user_id"]
            division = ctx.payload.get("division")
            if not division:
                return NOBODY_REMOVED_UNSEATED.format(driver_name=driver_name)
            return NOBODY_REMOVED.format(driver_name=driver_name, division=division)
        return f"✅ Removed fake driver **{saved['driver']}** from **{saved['team']}**."

    return ChangeType(
        kind=TEST_ROSTER_REMOVE,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, save)},
        check=check,
        key=lambda payload: f"{TEST_ROSTER_REMOVE}|{payload['user_id']}",
        doing=doing,
        outcome=outcome,
        fill=fill,
        lane=lambda _payload: LEAGUE_LANE,
    )


# ─── /test-mode roster clear ──────────────────────────────────────────────────


def test_roster_clear_change(
    *, profiles_deleted: AwaitedHook, divisions_raced_read: AwaitedHook
) -> ChangeType:
    """The `core.test_roster_clear` change type, for the builder to register: *profiles_deleted*
    is the hook each module signs up to so that it deletes its own rows of the drivers deleted.

    A change type of its own on the league lane, **carrying the division as typed**.

    **The refusals are the check's**, judged at the ask and again as the change starts: test mode
    not on, no such division in the live season, and a fake driver in it who has raced or holds a
    confirmed seat, which refuses the whole division and names them, `/driver sack` undoing it
    first. **One SAVE** judges once more on its
    own connection, deletes every fake driver seated in the division, keeping their history, and
    writes a `TEST_DRIVER_REMOVED` audit entry for each, in the order they were created, and the
    line. A division holding no fake driver is an ordinary answer: it is said, and the line says
    nothing changed, with no audit entry. A fault in the save stops the lane.
    """

    async def judge(
        db: aiosqlite.Connection, payload: dict[str, Any]
    ) -> tuple[int, list[FakeDriver]] | str:
        """The division's id and the fake drivers seated in it, or the reply refusing the clear."""
        refusal = await standing_refusal_on(db)
        if refusal is not None:
            return refusal
        located = await locate_division_on(db, str(payload["division"]))
        if isinstance(located, str):
            return f"⛔ {located}"
        division_id, division_name = located
        drivers = await fake_drivers_in_division_on(db, division_id)
        kept = [
            driver for driver in drivers
            if await has_raced_or_holds_a_confirmed_seat(db, driver, divisions_raced_read)
        ]
        if kept:
            return KEPT_CLEARING.format(
                division=division_name,
                names=", ".join(f"**{driver.display_name}**" for driver in kept),
            )
        return division_id, drivers

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            judged = await judge(db, ctx.payload)
        return Verdict.refuse(judged) if isinstance(judged, str) else Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Delete the drivers, and write the audit entries and the line."""
        judged = await judge(db, ctx.payload)
        if isinstance(judged, str):
            return StepResult(
                result={"refused": judged},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(judged)),),
            )
        _division_id, drivers = judged
        division = str(ctx.payload["division"])
        if not drivers:
            line = (
                f"{ctx.named} | {CLEAR_COMMAND} | Nothing changed\n"
                f"  division: {division}\n"
                "  reason: no fake drivers to remove"
            )
            return StepResult(result={"saved": True, "removed": 0}, lines=(line,))
        await delete_test_drivers_on(
            db, [driver.profile_id for driver in drivers], profiles_deleted=profiles_deleted
        )
        removed = len(drivers)
        audits = []
        for driver in drivers:
            old = _removed_entry(driver)
            audits.append(
                AuditRecord(
                    "TEST_DRIVER_REMOVED", old, dict.fromkeys(old), division_id=driver.division_id
                )
            )
        line = (
            f"{ctx.named} | {CLEAR_COMMAND} | Success\n"
            f"  division: {division}\n"
            f"  removed_drivers: {removed}"
        )
        return StepResult(
            result={"saved": True, "removed": removed}, audits=tuple(audits), lines=(line,)
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Clearing the fake drivers from **{payload['division']}**"

    def outcome(ctx: OutcomeContext) -> str:
        saved = saved_result(ctx)
        division = ctx.payload["division"]
        if saved.get("refused"):
            return str(saved["refused"])
        if not saved.get("saved"):
            return NOBODY_CLEARED.format(division=division)
        if not saved["removed"]:
            return f"ℹ️ No fake drivers found in **{division}**."
        return f"✅ Removed **{saved['removed']}** fake driver(s) from **{division}**."

    return ChangeType(
        kind=TEST_ROSTER_CLEAR,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, save)},
        check=check,
        key=lambda payload: f"{TEST_ROSTER_CLEAR}|{json.dumps(payload['division'])}",
        doing=doing,
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


__all__ = [
    "TEST_ROSTER_ADD",
    "TEST_ROSTER_ADD_BULK",
    "TEST_ROSTER_CLEAR",
    "TEST_ROSTER_REMOVE",
    "bulk_payload",
    "standing_refusal_on",
    "test_roster_add_bulk_change",
    "test_roster_add_change",
    "test_roster_clear_change",
    "test_roster_remove_change",
]
