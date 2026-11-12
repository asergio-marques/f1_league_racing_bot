"""A round's check-in record, for the log channel (#175, #283).

Attendance's own: every driver the check-in recorded, grouped by their answer, and the reserves
where they were distributed. A round's, a division's or a season's cancellation reads it as
attendance's take-down of the round's call does (`check_in_changes.take_down_call`), before the
call comes down. A round ending with every session entered as not held reads it too, through
`read_check_in_on`, which `AttendanceAfterReview.check_in_read_on` calls: planned inside the
save that ends the round, so that results writes the same record beneath its line without
naming attendance's job. `checkin_record` reads `driver_round_attendance` (attendance's own
table) on its own connection, and the driver and team names through core's `driver_service` and
`team_service`, never from `driver_profiles` or `team_instances` themselves (core owns both,
CLAUDE.md).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult
from leaguebot.core.services.drivers import driver_service, team_service
from leaguebot.core.services.seasons import season_service
from leaguebot.core.services.cancellations.cancellation_notice_service import round_label
from leaguebot.core.services.queue.change_types import Step, StepContext, StepView

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService

#: The job attendance plans for a round ending not held, reading the check-in for the log line.
READ_CHECK_IN = "read_check_in"

#: The order the audit lists the answers in, and the words it lists them under.
_ANSWERS = (
    ("ACCEPTED", "accepted"),
    ("TENTATIVE", "tentative"),
    ("DECLINED", "declined"),
    ("NO_RSVP", "no answer"),
)


async def checkin_record(db_path: str, division: Any, round_id: int) -> str:
    """The log-channel record of *round_id*'s check-in in *division*, or "" where it had none.

    *division* is anything with an `id` and a `name`. Read on its own connection and writing
    nothing, so a round that ends cancelled (every session entered as not held) can read the same
    record `/round cancel` writes beneath its line. The round itself is read through core's
    `season_service.round_by_id` (architecture.md, "How modules and core fit together"), never
    from `rounds` directly; a round no longer *division*'s gives "".

    Every driver the check-in recorded, grouped by their answer, and — where the reserves had
    already been distributed — who was sent to which team and who stood by. A driver is named as
    attendance's own log lines name one: their mention, with the name a test driver goes by. A
    round whose call was never posted recorded nobody and is left out.
    """
    round_row = await season_service.round_by_id(db_path, division.id, round_id)
    if round_row is None:
        return ""
    async with get_connection(db_path) as db:
        rows = await (await db.execute(
            """
            SELECT driver_profile_id, rsvp_status, is_standby, assigned_team_id
              FROM driver_round_attendance
             WHERE round_id = ? AND division_id = ?
            """,
            (round_id, division.id),
        )).fetchall()
    if not rows:
        return ""

    names = await driver_service.driver_names_for_profiles(
        db_path, [row["driver_profile_id"] for row in rows]
    )
    teams = await team_service.team_names_for_instances(
        db_path, [row["assigned_team_id"] for row in rows if row["assigned_team_id"]]
    )

    def _sort_key(row: Any) -> int:
        return int(names[row["driver_profile_id"]][0])

    def _name(row: Any) -> str:
        discord_user_id, test_display_name = names[row["driver_profile_id"]]
        return f"<@{discord_user_id}>" + (f" ({test_display_name})" if test_display_name else "")

    ordered = sorted(rows, key=_sort_key)
    lines = [
        f"\n  check-in, {division.name}, "
        f"{round_label(round_row.round_number, round_row.track_name)}:"
    ]
    for status, label in _ANSWERS:
        named = [_name(r) for r in ordered if r["rsvp_status"] == status]
        lines.append(f"\n    {label}: {', '.join(named) if named else 'none'}")
    reserves = [
        f"{_name(r)} to {teams[r['assigned_team_id']]}" if r["assigned_team_id"] else f"{_name(r)} on standby"
        for r in ordered
        if r["assigned_team_id"] or r["is_standby"]
    ]
    if reserves:
        lines.append(f"\n    reserves: {', '.join(reserves)}")
    return "".join(lines)


#: Why a discarded read of a round's check-in is named, beneath the round's log line, as what
#: was not done (`kept_line`, `season_lifecycle_service`).
_NOT_WRITTEN = "not done: the check-in record of round {number} in **{name}** was not written"


def read_check_in_steps(*, modules: "ModuleService") -> dict[str, Step]:
    """The job that reads a round's check-in for the log channel, for a round that ends
    cancelled without a command (every session entered as not held) to plan beside the notices.

    It is an ACT on its own connection with no save open, due while attendance is on, and its
    result is `{"record": text}` (the same record `/round cancel` writes, `checkin_record`;
    empty where the round's call was never posted), so that a caller reads the record from the
    job's own result without naming it. It reads the round named by `round_id` in its own
    payload beside the notices' (`division_id`, `division_name`, `round_number`). The call
    itself is not taken down: it comes down a day after the start, as any round's. A discard
    loses the record alone, named by `not_done` as not written.
    """

    async def read(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        record = await checkin_record(
            ctx.db_path,
            SimpleNamespace(id=int(payload["division_id"]), name=str(payload["division_name"])),
            int(payload["round_id"]),
        )
        return StepResult(result={"record": record})

    async def attendance_on(_ctx: StepContext) -> bool:
        return await modules.is_attendance_enabled()

    async def describe(ctx: StepContext) -> str:
        payload = ctx.step_payload
        return (
            f"reading the check-in record of round {payload['round_number']} in "
            f"**{payload['division_name']}**"
        )

    def not_done(view: StepView) -> str | None:
        return _NOT_WRITTEN.format(
            number=view.payload.get("round_number"), name=view.payload.get("division_name"),
        )

    return {
        READ_CHECK_IN: Step(
            READ_CHECK_IN, StepKind.ACT, read, still_due=attendance_on,
            describe=describe, not_done=not_done,
        ),
    }


async def read_check_in_on(
    _db: Any, ended: dict[str, Any],
) -> tuple[PlannedStep, ...]:
    """Plan the reading of *ended*'s check-in (`division_id`, `division_name`, `round_id`, and a
    round's `round_number`), so that results registers the job without naming attendance. Called
    by `AttendanceAfterReview.check_in_read_on`, which plans nothing while attendance is off."""
    return (PlannedStep(READ_CHECK_IN, dict(ended)),)
