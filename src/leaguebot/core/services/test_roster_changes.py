"""The test roster's four commands on the change queue: `core.test_roster_add`,
`core.test_roster_add_bulk`, `core.test_roster_remove` and `core.test_roster_clear`.

Each is a change type of its own on the league lane, registered by the builder, judged without a
write at the ask and again as the change starts, and one SAVE of its rows and its audit entries on
the connection the queue hands it. A fault in a save stops the lane, and the discard reply says
nothing was changed. What the add-bulk, remove and clear kinds judge and write is in their
factories' docstrings; what follows is `core.test_roster_add`'s.

`/test-mode roster add` is **a change carrying the fake driver's name, the team as typed (its
shorthand), the division as typed and the nationality as typed** (None where none was given). The acknowledgement names the team by its full name, which the ask reads
into the payload (`team_full_name`).

**The refusals are the check's**, judged at the ask and again as the change starts, in the league's
log channel either time, in the words the command has always used: test mode not on, the season
not in Placements (test mode replicates the live flow, where drivers are seated in Placements),
a nationality given while test mode's nationality switch is off, and what seating the driver is
refused for (`judge_add_on`): the name, the nationality, no live season, no such division or team,
a template capacity a real placement into the team would be refused for, no free seat. The save
judges them once more on its own connection, ending the change in the same words where one has come
up since, changing nothing and not stopping the lane.

**One SAVE** seats the driver (`seat_on`: the profile, the seat and the season assignment) and
writes the `TEST_DRIVER_ADDED` audit entry, which records the division's stored name, and the log
line, which keeps the name as typed, stamped by the queue's clock. The
synthetic id is allocated inside the save, so two drivers never share one.

**A fault in the save stops the lane**, as a failed job does, for the reason `/division delete`'s
does: a league admin exists to discard the job and clear the stop, and nothing is saved. The reply
once it is discarded says nobody was added.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.placement_service import PlacementService
from leaguebot.core.services.round_add_changes import saved_result
from leaguebot.core.services.season_lifecycle_service import live_season_stage_on
from leaguebot.core.services.test_mode_settings import NOT_IN_TEST_MODE
from leaguebot.core.services.test_roster_service import (
    AddPlan,
    BulkPlan,
    FakeDriver,
    delete_test_drivers_on,
    fake_drivers_in_division_on,
    find_test_driver_on,
    judge_add_on,
    judge_bulk_on,
    locate_division_on,
    locate_on,
    seat_bulk_on,
    seat_on,
)
from leaguebot.core.utils.hooks import AwaitedHook
from leaguebot.core.utils.log_lines import refusal_line, reply_reason
from leaguebot.core.utils.roster_import import (
    ParsedDriver,
    divisions_named,
    format_roster_errors,
)

TEST_ROSTER_ADD = "core.test_roster_add"
TEST_ROSTER_ADD_BULK = "core.test_roster_add_bulk"
TEST_ROSTER_REMOVE = "core.test_roster_remove"
TEST_ROSTER_CLEAR = "core.test_roster_clear"

#: The name of the step that seats the driver and writes its audit entry and its line.
SAVE = "save"

#: `/test-mode roster add` as the line names it.
ADD_COMMAND = "/test-mode roster add"

#: What a roster command answers outside Placements.
NOT_PLACING = "⛔ The test roster can only be changed while the season is in placements."

#: What `/test-mode roster add` answers a nationality while test mode's switch for it is off.
NATIONALITY_SWITCHED_OFF = (
    "⛔ Nationality is switched off for test mode, so one cannot be recorded. "
    "Turn it on with `/test-mode nationality`, or leave the parameter out."
)

#: `/test-mode roster add-bulk` as the line names it.
ADD_BULK_COMMAND = "/test-mode roster add-bulk"

#: The reply once a league admin has discarded the stopped save of a roster: nobody was added.
ROSTER_NOT_ADDED = (
    "Nothing was added: no test driver is seated in {divisions}. "
    "Run `/test-mode roster add-bulk` again."
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
    "Nothing was added: **{driver_name}** is not seated in **{division}**. "
    "Run `/test-mode roster add` again."
)


async def standing_refusal_on(db: aiosqlite.Connection, *, nationality: str | None = None) -> str | None:
    """What a roster command is refused for before it looks at what it names, in its words: test mode
    not on, the season not in Placements, and, for a command given *nationality*, test mode's
    switch for it off."""
    cursor = await db.execute(
        "SELECT test_mode_active, test_mode_nationality_required FROM server_configs"
    )
    config = await cursor.fetchone()
    if config is None or not config["test_mode_active"]:
        return NOT_IN_TEST_MODE
    live = await live_season_stage_on(db)
    if live is None or live[1] is not SeasonStage.PLACEMENTS:
        return NOT_PLACING
    if nationality is not None and not config["test_mode_nationality_required"]:
        return NATIONALITY_SWITCHED_OFF
    return None


def _add_arguments(payload: dict[str, Any]) -> tuple[str, str, str, str | None]:
    return (
        str(payload["driver_name"]),
        str(payload["team_name"]),
        str(payload["division"]),
        payload["nationality"],
    )


def test_roster_add_change(*, placement: Callable[[], PlacementService]) -> ChangeType:
    """The `core.test_roster_add` change type, for the builder to register: *placement* gives the
    placement service the driver is held to the template capacities through, asked for each time it
    is needed."""

    async def judge(db: aiosqlite.Connection, payload: dict[str, Any]) -> AddPlan | str:
        """The plan to seat the driver, or the reply refusing it."""
        refusal = await standing_refusal_on(db, nationality=payload["nationality"])
        if refusal is not None:
            return refusal
        driver_name, team_name, division, nationality = _add_arguments(payload)
        judged = await judge_add_on(
            db, driver_name, team_name, division, nationality, placement()
        )
        return f"⛔ {judged}" if isinstance(judged, str) else judged

    async def fill(ctx: CheckContext) -> None:
        async with get_connection(ctx.db_path) as db:
            located = await locate_on(db, str(ctx.payload["division"]), str(ctx.payload["team_name"]))
        if not isinstance(located, str):
            ctx.payload["team_full_name"] = located.team["full_name"]

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        async with get_connection(ctx.db_path) as db:
            judged = await judge(db, ctx.payload)
        return Verdict.refuse(judged) if isinstance(judged, str) else Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Seat the driver, and write the audit entry and the line."""
        judged = await judge(db, ctx.payload)
        if isinstance(judged, str):
            return StepResult(
                result={"refused": judged},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(judged)),),
            )
        placed = await seat_on(db, judged)
        division = str(ctx.payload["division"])
        new = {
            "driver": placed["display_name"],
            "user_id": str(placed["discord_user_id"]),
            "team": placed["team_name"],
            "division": judged.located.division_name,
            "nationality": placed["nationality"],
        }
        line = (
            f"{ctx.named} | {ADD_COMMAND} | Success\n"
            f"  driver: {placed['display_name']}\n"
            f"  team: {placed['team_name']}\n"
            f"  division: {division}\n"
            f"  nationality: {placed['nationality'] or '(none)'}"
        )
        return StepResult(
            result={"saved": True, **new},
            audits=(
                AuditRecord(
                    "TEST_DRIVER_ADDED", dict.fromkeys(new), new,
                    division_id=judged.located.division_id,
                ),
            ),
            lines=(line,),
        )

    def doing(payload: dict[str, Any]) -> str:
        team = payload.get("team_full_name") or payload["team_name"]
        return (
            f"Adding fake driver **{payload['driver_name']}** to **{team}** "
            f"in **{payload['division']}**"
        )

    def outcome(ctx: OutcomeContext) -> str:
        saved = saved_result(ctx)
        if saved.get("refused"):
            return str(saved["refused"])
        if not saved.get("saved"):
            return NOBODY_ADDED.format(**ctx.payload)
        nationality_line = f"Nationality: **{saved['nationality']}**\n" if saved["nationality"] else ""
        # The division is named because a team name repeats across divisions, so the team alone
        # does not say where the driver landed, which is the one thing the manager is choosing.
        return (
            f"✅ Added fake driver **{saved['driver']}** to **{saved['team']}** "
            f"in **{saved['division']}**.\n"
            f"{nationality_line}"
            f"Mention string (copy-paste into results): `<@{saved['user_id']}>`"
        )

    return ChangeType(
        kind=TEST_ROSTER_ADD,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, save)},
        check=check,
        key=lambda payload: f"{TEST_ROSTER_ADD}|{json.dumps(_add_arguments(payload))}",
        doing=doing,
        outcome=outcome,
        fill=fill,
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
                "team_name": driver.team_name,
                "division_name": driver.division_name,
                "nationality": driver.nationality,
            }
            for driver in drivers
        ]
    }


def _drivers_of(payload: dict[str, Any]) -> list[ParsedDriver]:
    return [ParsedDriver(**driver) for driver in payload["drivers"]]


def test_roster_add_bulk_change(*, placement: Callable[[], PlacementService]) -> ChangeType:
    """The `core.test_roster_add_bulk` change type, for the builder to register: *placement* gives
    the placement service each division is held to the template capacities through, asked for
    each time it is needed.

    **The refusals are the check's**, judged at the ask and again as the change starts: test mode
    not on, the season not in Placements, and every fault the database shows in the roster
    (`judge_bulk_on`), listed together as the form has always listed them. A fault the file shows
    on its own is refused by the form, before the ask. **One SAVE** judges once more on its own
    connection, seats every driver (`seat_bulk_on`) and writes a `TEST_DRIVER_ADDED` audit entry
    for each, on the division they are seated in, in the file's order, and the line. A fault in
    the save stops the lane, as in `/test-mode roster add`.
    """

    async def judge(db: aiosqlite.Connection, payload: dict[str, Any]) -> BulkPlan | str:
        """The plan to seat the roster, or the reply refusing it."""
        refusal = await standing_refusal_on(db)
        if refusal is not None:
            return refusal
        judged = await judge_bulk_on(db, _drivers_of(payload), placement())
        return format_roster_errors(judged) if isinstance(judged, list) else judged

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        async with get_connection(ctx.db_path) as db:
            judged = await judge(db, ctx.payload)
        return Verdict.refuse(judged) if isinstance(judged, str) else Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Seat every driver, and write the audit entries and the line."""
        judged = await judge(db, ctx.payload)
        if isinstance(judged, str):
            return StepResult(
                result={"refused": judged},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(judged)),),
            )
        seated = await seat_bulk_on(db, judged)
        audits = []
        for planned in judged.seats:
            new = {
                "driver": planned.driver.driver_name,
                "user_id": str(planned.driver.discord_user_id),
                "team": planned.team_full_name,
                "division": planned.division_name,
                "nationality": planned.nationality,
            }
            audits.append(
                AuditRecord(
                    "TEST_DRIVER_ADDED", dict.fromkeys(new), new, division_id=planned.division_id
                )
            )
        divisions = divisions_named(_drivers_of(ctx.payload))
        line = (
            f"{ctx.named} | {ADD_BULK_COMMAND} | Success\n"
            f"  drivers_seated: {seated}\n"
            f"  divisions: {', '.join(divisions)}"
        )
        return StepResult(
            result={"saved": True, "seated": seated, "divisions": divisions},
            audits=tuple(audits),
            lines=(line,),
        )

    def divisions_in(payload: dict[str, Any]) -> list[str]:
        return divisions_named(_drivers_of(payload))

    def doing(payload: dict[str, Any]) -> str:
        count = len(payload["drivers"])
        named = ", ".join(f"**{name}**" for name in divisions_in(payload))
        return f"Seating {count} test driver{'' if count == 1 else 's'} in {named}"

    def outcome(ctx: OutcomeContext) -> str:
        saved = saved_result(ctx)
        if saved.get("refused"):
            return str(saved["refused"])
        if not saved.get("saved"):
            return ROSTER_NOT_ADDED.format(
                divisions=", ".join(f"**{name}**" for name in divisions_in(ctx.payload))
            )
        divisions = saved["divisions"]
        return (
            f"✅ Seated **{saved['seated']}** test driver(s) across "
            f"{len(divisions)} division(s): {', '.join(divisions)}.\n"
            f"`/test-mode roster list` shows a division's drivers with the mentions "
            f"result submission wants."
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
        "nationality": found.nationality,
    }


def test_roster_remove_change(*, profiles_deleted: AwaitedHook) -> ChangeType:
    """The `core.test_roster_remove` change type, for the builder to register: *profiles_deleted*
    is the hook each module signs up to so that it deletes its own rows of the drivers deleted.

    A change type of its own on the league lane, **carrying the synthetic id typed** (`user_id`),
    which the form of the command has read as a number. The ask reads the driver's name and
    division into the payload (`fill`), for the reply of a save a league admin discarded.

    **The refusals are the check's**, judged at the ask and again as the change starts: test mode
    not on, the season not in Placements, and no fake driver holding the id (a real driver's id
    is one). **One SAVE** judges once more on its own connection, deletes the driver, keeping
    their history, and writes the `TEST_DRIVER_REMOVED` audit entry, on the division they were
    seated in, and the line. A fault in the save stops the lane.
    """

    async def judge(db: aiosqlite.Connection, payload: dict[str, Any]) -> FakeDriver | str:
        refusal = await standing_refusal_on(db)
        if refusal is not None:
            return refusal
        found = await find_test_driver_on(db, int(payload["user_id"]))
        return f"⛔ {found}" if isinstance(found, str) else found

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


def test_roster_clear_change(*, profiles_deleted: AwaitedHook) -> ChangeType:
    """The `core.test_roster_clear` change type, for the builder to register: *profiles_deleted*
    is the hook each module signs up to so that it deletes its own rows of the drivers deleted.

    A change type of its own on the league lane, **carrying the division as typed**.

    **The refusals are the check's**, judged at the ask and again as the change starts, for run
    against a live season the command would delete real drivers: test mode not on, the season not
    in Placements, and no such division in the live season. **One SAVE** judges once more on its
    own connection, deletes every fake driver seated in the division, keeping their history, and
    writes a `TEST_DRIVER_REMOVED` audit entry for each, in the order they were created, and the
    line. A division holding no fake driver is an ordinary answer: it is said, and the line says
    nothing changed, with no audit entry. A fault in the save stops the lane.
    """

    async def judge(db: aiosqlite.Connection, payload: dict[str, Any]) -> tuple[int, str] | str:
        refusal = await standing_refusal_on(db)
        if refusal is not None:
            return refusal
        located = await locate_division_on(db, str(payload["division"]))
        return f"⛔ {located}" if isinstance(located, str) else located

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
        division_id, _name = judged
        division = str(ctx.payload["division"])
        drivers = await fake_drivers_in_division_on(db, division_id)
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
    "NATIONALITY_SWITCHED_OFF",
    "NOT_PLACING",
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
