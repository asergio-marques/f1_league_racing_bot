"""`core.test_mode_toggle`: `/test-mode toggle`, on the change queue.

A change type of its own on the league lane, registered by the builder, **carrying the state it
sets** (`{"on": True}` or `{"on": False}`), which the command reads once, to say which way it goes,
as `/test-mode nationality` does (`test_mode_settings.py`). The acknowledgement names the
direction, and a second press while the first waits, asking the same with nothing else asked on
the lane since, is refused by the queue as a repeat: two presses racing can no longer both take
one path.

**The refusals are the check's**, judged at the ask and again as the change starts, in the
league's log channel either time: no season in Configuration, and, to turn test mode on, a real
driver on the server. Test mode is chosen for a season, while it is in Configuration; and a real
roster and test mode may not share a server, since switching test mode off deletes every fake
driver and while it is on the signup and placement paths refuse real drivers outright.

**Turning test mode on is one SAVE**: the flag, the modules' part through the hook for test mode
turned on (results seeds its points configurations), handed the save's own connection and the
live season's id, and the `TEST_MODE_SET` audit entry, stamped by the queue's clock. A fault in a
module's part rolls the whole save back and **stops the lane**, as a failed job does, for the
reason `/season setup`'s does (`season_setup_change.py`): a league admin exists to discard the
job and clear the stop, and test mode is left as it was, with nothing seeded.

**A save that finds the flag already where the change was asked to put it** writes nothing, and
says so.

**The log line is written as the change finishes**, from what the save recorded, so that it is
one line and says what became of the change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import aiosqlite

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
from leaguebot.core.services.round_add_changes import saved_result
from leaguebot.core.services.season_lifecycle_service import (
    live_season_stage,
    live_season_stage_on,
)
from leaguebot.core.services.test_mode_service import count_live_real_drivers
from leaguebot.core.utils.hooks import AwaitedHook

TEST_MODE_TOGGLE = "core.test_mode_toggle"

#: The name of the step that sets the flag and writes its audit entry.
SAVE = "save"

#: `/test-mode toggle` as the line names it.
COMMAND = "/test-mode toggle"

#: The audit entry's change type.
AUDIT_TYPE = "TEST_MODE_SET"

NOT_IN_CONFIGURATION = (
    "⛔ Test mode can only be switched while a season is in configuration. "
    "Start one with `/season setup`; a season whose configuration is confirmed "
    "keeps test mode as it stands until the season ends."
)
REAL_DRIVERS = (
    "⛔ Test mode cannot be enabled while this server has **{count}** real driver(s).\n"
    "Test mode is for an empty league — disabling it deletes every fake "
    "driver, and while it is on no real driver may sign up or be placed."
)
ENABLED = (
    "✅ Test mode **enabled**. "
    "Use `/test-mode advance` to step through phases, "
    "or `/test-mode review` to inspect season status."
)
NOTHING_CHANGED = "ℹ️ Nothing changed: test mode is already **{state}**."

#: The reply once a league admin has discarded the stopped save: nothing was changed.
NOT_CHANGED = "Nothing was changed: test mode is still **{state}**. Run `/test-mode toggle` again."

#: The save's results, as `outcome` and the line read them.
ON = "on"
UNCHANGED = "unchanged"


def _words(on: bool) -> str:
    return "on" if on else "off"


async def _check(ctx: CheckContext) -> Verdict:
    """The refusals, in the order they come, each read from the database."""
    live = await live_season_stage(ctx.db_path)
    if live is None or live[1] is not SeasonStage.CONFIGURATION:
        return Verdict.refuse(NOT_IN_CONFIGURATION)
    if ctx.payload["on"]:
        real_drivers = await count_live_real_drivers(ctx.db_path)
        if real_drivers:
            return Verdict.refuse(REAL_DRIVERS.format(count=real_drivers))
    return Verdict.go()


def _save(
    test_mode_turned_on: AwaitedHook,
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Set the flag, with the modules' part and the audit entry."""
        on = bool(ctx.payload["on"])
        cursor = await db.execute("SELECT test_mode_active FROM server_configs")
        row = await cursor.fetchone()
        if row is None or bool(row["test_mode_active"]) == on:
            return StepResult(result={"outcome": UNCHANGED})
        await db.execute("UPDATE server_configs SET test_mode_active = 1")
        live = await live_season_stage_on(db)
        handed_back = await test_mode_turned_on.call(db, live[0]) if live is not None else []
        return StepResult(
            result={"outcome": ON, "seeded": [name for names in handed_back for name in names]},
            audits=(
                AuditRecord(AUDIT_TYPE, {"test_mode_active": False}, {"test_mode_active": True}),
            ),
        )

    return save


def _doing(payload: dict[str, object]) -> str:
    return f"Turning test mode {_words(bool(payload['on']))}"


def _outcome(ctx: OutcomeContext) -> str:
    saved = saved_result(ctx)
    on = bool(ctx.payload["on"])
    found = saved.get("outcome")
    if found == UNCHANGED:
        return NOTHING_CHANGED.format(state=_words(on))
    if found != ON:
        return NOT_CHANGED.format(state=_words(not on))
    seeded = saved["seeded"]
    return ENABLED + (f"\n📌 Added default point configs: **{', '.join(seeded)}**" if seeded else "")


def _finished_lines(ctx: OutcomeContext) -> tuple[str, ...]:
    """The one line of the change, once it is done: Success, or Nothing changed where the flag
    already stood; none where the save never finished."""
    saved = saved_result(ctx)
    found = saved.get("outcome")
    if found == UNCHANGED:
        return (f"{ctx.named} | {COMMAND} | Nothing changed",)
    if found != ON:
        return ()
    seeded = saved["seeded"]
    return (
        f"{ctx.named} | {COMMAND} | Success\n  test_mode: enabled"
        + (f"\n  seeded_configs: {', '.join(seeded)}" if seeded else ""),
    )


def test_mode_toggle_change(*, test_mode_turned_on: AwaitedHook) -> ChangeType:
    """The `core.test_mode_toggle` change type, for the builder to register: *test_mode_turned_on*
    is the hook each module's part of turning test mode on is signed up to."""

    async def describe(ctx: StepContext) -> str:
        return _doing(ctx.payload).lower()

    return ChangeType(
        kind=TEST_MODE_TOGGLE,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _save(test_mode_turned_on), describe=describe)},
        check=_check,
        key=lambda payload: f"{TEST_MODE_TOGGLE}|{payload['on']}",
        doing=_doing,
        outcome=_outcome,
        lane=lambda _payload: LEAGUE_LANE,
        finished_lines=_finished_lines,
    )


__all__ = [
    "NOT_IN_CONFIGURATION",
    "REAL_DRIVERS",
    "TEST_MODE_TOGGLE",
    "test_mode_toggle_change",
]
