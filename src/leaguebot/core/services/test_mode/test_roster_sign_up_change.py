"""`core.test_roster_sign_up_bulk`: `/test-mode roster sign-up-bulk` on the change queue.

A helper for building a test season quickly, not a way round the signup: it creates each fake driver
the pasted list names, as `add-bulk` does (`seat_bulk_on`), signs each up and approves it, and
leaves it Unassigned with no seat, which `/driver assign` gives it as it does any approved driver's.
It obeys the flow: it is refused unless signups are open.

**Core creates the drivers; signup signs them up.** What a signup is, and what moves a driver
through it, is signup's, so the change reaches it only through the two hooks the builder hands it:
*fake_signups_ready_read*, asked for the faults that stand in the way of signing a driver up, and
*fake_signups_approved*, asked in the save for the signup and the driver's passage to Unassigned,
which hands back the audit records of what it did. With no subscriber to the first, core refuses:
nothing can sign a driver up. With none to the second the save raises and rolls back, the lane
stopping, rather than leaving drivers created at Not Signed Up under a success line.

**The refusals are the check's**, judged at the ask and again as the change starts, and once more
in the save before the first write, on the connection the save holds, so that each is judged
against what the save then sees: test mode not on, the faults the database shows in the roster
(`judge_bulk_on`), a nationality given while `/test-mode nationality` is off, and what the signup
module answers. Every fault is named in one reply, and nothing is written. A fault the text shows on
its own is the form's. **After the first write nothing is turned into a refusal**: a fault in a
subscriber raises and rolls the save back, the lane stopping until a league admin discards the job.

**One SAVE** writes every driver, each driver's signup and approval, a `TEST_DRIVER_ADDED` audit
entry for each, then those the signup module hands back (the start and the approval of each
signup, by the admin), and one line, however many drivers there are.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.core.models.test_mode import FakeSignUp
from leaguebot.core.services.rounds.round_add_changes import saved_result
from leaguebot.core.services.test_mode.test_roster_changes import SAVE, standing_refusal_on
from leaguebot.core.services.test_mode.test_roster_service import (
    BulkPlan,
    judge_bulk_on,
    seat_bulk_on,
)
from leaguebot.core.utils.roster_import import ParsedDriver, format_sign_up_errors
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
from leaguebot.framework.utils.hooks import AwaitedHook
from leaguebot.framework.utils.log_lines import refusal_line, reply_reason

TEST_ROSTER_SIGN_UP_BULK = "core.test_roster_sign_up_bulk"

#: `/test-mode roster sign-up-bulk` as the line names it.
SIGN_UP_BULK_COMMAND = "/test-mode roster sign-up-bulk"

#: The reply once a league admin has discarded the stopped save: nobody was added.
NOBODY_SIGNED_UP = (
    "Nothing was added: no fake driver was created. Run `/test-mode roster sign-up-bulk` again."
)

#: What is asked of the bot where no module can sign a fake driver up.
NO_SIGNUP = "Nothing can sign fake drivers up: the signup module is not part of this bot."


def sign_up_payload(drivers: list[ParsedDriver]) -> dict[str, Any]:
    """The request the form makes: every driver the list names, as the list names them, with the
    nationality it gave (None where blank)."""
    return {
        "drivers": [
            {
                "line": driver.line,
                "discord_user_id": driver.discord_user_id,
                "driver_name": driver.driver_name,
                "nationality": driver.nationality,
            }
            for driver in drivers
        ]
    }


def _drivers_of(payload: dict[str, Any]) -> list[ParsedDriver]:
    return [ParsedDriver(**driver) for driver in payload["drivers"]]


async def _nationality_faults_on(
    db: aiosqlite.Connection, drivers: list[ParsedDriver]
) -> list[str]:
    """A fault for each row naming a nationality while `/test-mode nationality` is off: the switch
    stands in for the league's own setting under test mode, and a switch off means no nationality is
    collected."""
    cursor = await db.execute("SELECT test_mode_nationality_required FROM server_configs")
    row = await cursor.fetchone()
    if row is not None and row["test_mode_nationality_required"]:
        return []
    return [
        f"Line {driver.line}: a nationality is given, but `/test-mode nationality` is off. "
        "Leave it blank, or turn it on first."
        for driver in drivers if driver.nationality is not None
    ]


def test_roster_sign_up_bulk_change(
    *,
    fake_signups_ready_read: AwaitedHook,
    fake_signups_approved: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The `core.test_roster_sign_up_bulk` change type, for the builder to register:
    *fake_signups_ready_read* and *fake_signups_approved* are the hooks signup signs up to, and
    *now* the queue's clock, read when the save runs."""

    async def judge(db: aiosqlite.Connection, payload: dict[str, Any]) -> BulkPlan | str:
        """The plan to create and sign up the list, or the reply refusing it, every fault named."""
        refusal = await standing_refusal_on(db)
        if refusal is not None:
            return refusal
        drivers = _drivers_of(payload)
        faults: list[str] = []
        answers = await fake_signups_ready_read.call(db)
        faults += [fault for answer in answers for fault in answer] if answers else [NO_SIGNUP]
        judged = await judge_bulk_on(db, drivers)
        plan = None if isinstance(judged, list) else judged
        faults += judged if isinstance(judged, list) else []
        faults += await _nationality_faults_on(db, drivers)
        return format_sign_up_errors(faults) if plan is None or faults else plan

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            judged = await judge(db, ctx.payload)
        return Verdict.refuse(judged) if isinstance(judged, str) else Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Create every driver, sign each up and approve it, and write the audit entries and the
        line."""
        judged = await judge(db, ctx.payload)
        if isinstance(judged, str):
            return StepResult(
                result={"refused": judged},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(judged)),),
            )
        created = await seat_bulk_on(db, judged)
        audits = []
        fakes = []
        for driver in judged.drivers:
            new = {"driver": driver.driver_name, "user_id": str(driver.discord_user_id)}
            audits.append(AuditRecord("TEST_DRIVER_ADDED", dict.fromkeys(new), new))
            fakes.append(
                FakeSignUp(str(driver.discord_user_id), driver.driver_name, driver.nationality)
            )
        signed_up = await fake_signups_approved.call(db, fakes, now())
        if not signed_up:
            # The readiness read was answered, so a subscriber stood; a save that found none to
            # record the signups raises and rolls back rather than leaving drivers created at Not
            # Signed Up under a success line.
            raise LookupError("nothing is signed up to record a fake driver's signup")
        for handed in signed_up:
            audits.extend(handed)
        line = (
            f"{ctx.named} | {SIGN_UP_BULK_COMMAND} | Success\n"
            f"  drivers_signed_up: {created}"
        )
        return StepResult(
            result={"saved": True, "signed_up": created}, audits=tuple(audits), lines=(line,)
        )

    def doing(payload: dict[str, Any]) -> str:
        count = len(payload["drivers"])
        return f"Signing up {count} fake driver{'' if count == 1 else 's'}"

    def outcome(ctx: OutcomeContext) -> str:
        saved = saved_result(ctx)
        if saved.get("refused"):
            return str(saved["refused"])
        if not saved.get("saved"):
            return NOBODY_SIGNED_UP
        return (
            f"✅ Signed up and approved **{saved['signed_up']}** fake driver(s). "
            "Close signups with `/signup close`, then seat each with `/driver assign`."
        )

    return ChangeType(
        kind=TEST_ROSTER_SIGN_UP_BULK,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, save)},
        check=check,
        key=lambda payload: f"{TEST_ROSTER_SIGN_UP_BULK}|{json.dumps(payload['drivers'])}",
        doing=doing,
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


__all__ = [
    "NOBODY_SIGNED_UP",
    "TEST_ROSTER_SIGN_UP_BULK",
    "sign_up_payload",
    "test_roster_sign_up_bulk_change",
]
