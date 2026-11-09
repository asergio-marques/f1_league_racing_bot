"""`core.driver_move`: `/driver move`, on the change queue.

A change type of its own on the league lane, registered by the builder, for a committed driver
moved to a team of the same division or another while the season is ongoing. The driver, the
season, both divisions and the team are named by their ids and shorthand at the ask. A change is
five kinds of job, in this order:

1. **One save** moves the driver (`PlacementService.move_seat_on`) and writes its `DRIVER_MOVE`
   audit entry, stamped with the queue's clock, and plans what follows from what it read inside
   it: the roles to take and give, and the lineup of each division touched.
2. **The member's roles** (`role_moves.role_move_step`), which end on a fault rather than stop the
   lane, since the seat is the league's record and a role Discord refuses is the member's to be
   given by hand: it is reported in the reply and the line.
3. **Each lineup** (`lineup_posting.lineup_step`), which stops the lane where Discord refuses it,
   as every post does, and says what it left undone where a league admin discards it.
4. **The line** that records the move, written last so that it carries what was not done.

**A fault in the save stops the lane**, as a failed job does, for the reason `/division delete`'s
does: a league admin exists to discard the job and clear the stop, and nothing of the move is
kept. The roles and the lineups are planned by the save and so never follow a discarded one.

**The move is judged by its ids** and every refusal is the check's, at the ask and again as the
change starts, in the league's log channel either time: the season no longer ongoing, the driver
without a profile, a division not in the season, and what the placement service refuses
(`PlacementService.judge_move_on`, `guard_move`). The roles go to the account the save read from
the driver's profile, so a driver given another account meanwhile is not left with the old one's.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
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
from leaguebot.core.models.season import ONGOING_STAGES, SeasonStage
from leaguebot.core.services import lineup_posting, standing_posts
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.placement_service import PlacementService
from leaguebot.core.services.role_moves import MOVE_ROLES, role_move_step
from leaguebot.core.services.season_approval_change import unposted_lineup_line

DRIVER_MOVE = "core.driver_move"
DRIVER_SACK = "core.driver_sack"
DRIVER_RELEASE = "core.driver_release"

#: The names of the jobs, as the stop notice, the outcome and the tests know them.
SAVE = "save"
CLOSE = "close"
POST_LINEUP = lineup_posting.POST_LINEUP

#: `/driver move` as the line names it.
MOVE_COMMAND = "/driver move"

#: `/driver sack` as the line names it.
SACK_COMMAND = "/driver sack"

#: The reply once a league admin has discarded the stopped save: nothing was moved.
NOTHING_MOVED = (
    "Nothing was moved: **{driver_name}** stays where they were. Run `/driver move` again."
)

#: `/driver release` as the line names it.
RELEASE_COMMAND = "/driver release"

#: The same for a sack: nobody was sacked.
NOBODY_SACKED = (
    "Nobody was sacked: **{driver_name}** keeps their seats. Run `/driver sack` again."
)

#: The same for a release: nobody was released from the division.
NOTHING_RELEASED = (
    "Nothing was released: **{driver_name}** keeps their seat in **{division}**. "
    "Run `/driver release` again."
)

_NOT_ONGOING = "⛔ `{command}` is available only while the season is ongoing."


@dataclass(frozen=True)
class _Saved:
    """What a driver change's save wrote and what it leaves for the jobs after it.

    *result* is kept on the save's job for the reply and the line to read; *user_id* is the
    driver's account as the save read it, *revoke* and *grant* the roles to take and give it
    (none for a test driver) and *lineups* the divisions whose lineup is posted, in order.
    """

    result: dict[str, Any]
    audit: AuditRecord
    user_id: int
    revoke: Sequence[int] = ()
    grant: Sequence[int] = ()
    lineups: Sequence[int] = ()


@dataclass(frozen=True)
class _Kind:
    """What tells a driver change from another: its words and its save."""

    kind: str
    command: str
    doing: Callable[[dict[str, Any]], str]
    nothing_done: str
    done: Callable[[dict[str, Any], dict[str, Any], bool], str]
    details: Callable[[dict[str, Any], dict[str, Any]], str]
    check: Callable[[CheckContext], Awaitable[Verdict]]
    apply: Callable[[aiosqlite.Connection, StepContext], Awaitable[_Saved]]


async def ongoing_refusal(db_path: str, season_id: int, command: str) -> Verdict | None:
    """The refusal of *command* where season *season_id* is not in an ongoing stage, else None."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
        row = await cursor.fetchone()
    if row is None or SeasonStage(row["stage"]) not in ONGOING_STAGES:
        return Verdict.refuse(_NOT_ONGOING.format(command=command))
    return None


async def profile_refusal(db_path: str, payload: dict[str, Any]) -> Verdict | None:
    """The refusal of a driver with no profile, else None."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM driver_profiles WHERE id = ?", (int(payload["driver_profile_id"]),)
        )
        found = await cursor.fetchone()
    if found is None:
        return Verdict.refuse(f"⛓ No driver profile found for **{payload['driver_name']}**.")
    return None


async def division_refusal(
    db_path: str, season_id: int, divisions: Sequence[tuple[int, str]]
) -> Verdict | None:
    """The refusal of the first of *divisions*, each an id and the name it was typed as, that is
    not in season *season_id*, else None."""
    async with get_connection(db_path) as db:
        for division_id, name in divisions:
            cursor = await db.execute(
                "SELECT 1 FROM divisions WHERE id = ? AND season_id = ?", (division_id, season_id)
            )
            if await cursor.fetchone() is None:
                return Verdict.refuse(f"⛔ Division **{name}** not found in the active season.")
    return None


def _view(ctx: OutcomeContext, name: str) -> StepView | None:
    """The last job named *name*."""
    return next((view for view in reversed(ctx.steps) if view.name == name), None)


def _saved_result(ctx: OutcomeContext) -> dict[str, Any] | None:
    """What the save returned, or None where it did not go through: a league admin discarded it."""
    view = _view(ctx, SAVE)
    if view is None or not view.done or not view.result or not view.result.get("saved"):
        return None
    return view.result


def _roles_refused(ctx: OutcomeContext) -> bool:
    """Whether Discord refused the member's roles, which the role step ended on."""
    return any(view.name == MOVE_ROLES and (view.result or {}).get("problem") for view in ctx.steps)


def _not_done(ctx: OutcomeContext) -> list[str]:
    """What the jobs after the save left undone, in the order they ran: a role Discord refused,
    which the role step ended on, then each lineup or deletion a league admin discarded."""
    lines: list[str] = []
    for view in ctx.steps:
        result = view.result or {}
        if view.name == MOVE_ROLES and result.get("problem"):
            lines.append(str(result["problem"]))
        elif view.name == POST_LINEUP and result.get("discarded"):
            lines.append(unposted_lineup_line(str(view.payload["division_name"])))
        elif view.name == standing_posts.DELETE_REPLACED:
            line = standing_posts.not_deleted(view)
            if line is not None:
                lines.append(line)
    return lines


async def _division_names(db: aiosqlite.Connection, division_ids: Sequence[int]) -> list[str]:
    names = []
    for division_id in division_ids:
        cursor = await db.execute("SELECT name FROM divisions WHERE id = ?", (division_id,))
        row = await cursor.fetchone()
        names.append(str(row["name"]) if row is not None else str(division_id))
    return names


def _change(kind: _Kind, placement: PlacementService) -> ChangeType:
    """The change type *kind* describes, its jobs as the module says."""

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save; see the module."""
        saved = await kind.apply(db, ctx)
        names = await _division_names(db, saved.lineups)
        steps: list[PlannedStep] = []
        if saved.revoke or saved.grant:
            steps.append(
                PlannedStep(
                    MOVE_ROLES,
                    {
                        "user_id": saved.user_id,
                        "revoke": list(saved.revoke),
                        "grant": list(saved.grant),
                    },
                )
            )
        steps.extend(
            PlannedStep(POST_LINEUP, {"division_id": division_id, "division_name": name})
            for division_id, name in zip(saved.lineups, names)
        )
        return StepResult(
            result={**saved.result, "saved": True},
            audits=(saved.audit,),
            then=tuple(steps),
        )

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the line that records the change, with what was not done."""
        result = _saved_result(ctx)
        if result is None:
            return StepResult(result={"closed": True})
        line = (
            f"{ctx.named} | {kind.command} | Success"
            f"{kind.details(ctx.payload, result)}"
            + "".join(f"\n  not done: {each}" for each in _not_done(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    async def describe_close(ctx: StepContext) -> str:
        return f"recording `{kind.command}` of **{ctx.payload['driver_name']}**"

    def outcome(ctx: OutcomeContext) -> str:
        result = _saved_result(ctx)
        if result is None:
            return kind.nothing_done.format(**ctx.payload)
        problems = _not_done(ctx)
        reply = kind.done(ctx.payload, result, _roles_refused(ctx))
        if problems:
            reply += "\n⚠️ Done, but on Discord:\n" + "\n".join(f"• {each}" for each in problems)
        return reply

    return ChangeType(
        kind=kind.kind,
        opening=(PlannedStep(SAVE), PlannedStep(CLOSE)),
        steps={
            SAVE: Step(SAVE, StepKind.SAVE, save),
            MOVE_ROLES: role_move_step(placement),
            POST_LINEUP: lineup_posting.lineup_step(placement),
            standing_posts.DELETE_REPLACED: standing_posts.delete_replaced_step(),
            CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
        },
        check=kind.check,
        key=lambda payload: f"{kind.kind}|{json.dumps(payload, sort_keys=True)}",
        doing=kind.doing,
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


# ── /driver move ──────────────────────────────────────────────────────────────────────


def driver_move_change(*, placement: PlacementService) -> ChangeType:
    """The `core.driver_move` change type, for the builder to register: *placement* is what the
    check judges the move through, the save moves the driver through, and the jobs after it give
    the roles and post the lineups through.

    The payload names the driver (`driver_profile_id`, `discord_user_id`, `driver_name`), the
    season, the divisions (`from_division_id` and `to_division_id`, with their names as typed,
    `from_division` and `to_division`) and the team (`team_name`, the shorthand, and
    `team_full_name`).
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        season_id = int(payload["season_id"])
        refused = (
            await ongoing_refusal(ctx.db_path, season_id, MOVE_COMMAND)
            or await division_refusal(
                ctx.db_path, season_id,
                [(int(payload["from_division_id"]), payload["from_division"]),
                 (int(payload["to_division_id"]), payload["to_division"])],
            )
            or await profile_refusal(ctx.db_path, payload)
        )
        if refused is not None:
            return refused
        try:
            async with get_connection(ctx.db_path) as db:
                await placement.judge_move_on(
                    db, driver_profile_id=int(payload["driver_profile_id"]),
                    season_id=season_id, from_division_id=int(payload["from_division_id"]),
                    to_division_id=int(payload["to_division_id"]),
                    team_name=str(payload["team_name"]),
                )
            await placement.guard_move(
                int(payload["to_division_id"]), season_id, str(payload["team_name"])
            )
        except ValueError as refusal:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> _Saved:
        payload = ctx.payload
        moved = await placement.move_seat_on(
            db, driver_profile_id=int(payload["driver_profile_id"]),
            season_id=int(payload["season_id"]),
            from_division_id=int(payload["from_division_id"]),
            to_division_id=int(payload["to_division_id"]), team_name=str(payload["team_name"]),
        )
        audit = moved["audit"]
        return _Saved(
            result={
                key: moved[key] for key in ("from_division", "to_division", "from_team", "to_team")
            },
            audit=AuditRecord(
                "DRIVER_MOVE", audit["old_value"], audit["new_value"], audit["division_id"]
            ),
            user_id=moved["user_id"], revoke=moved["revoke"], grant=moved["grant"],
            lineups=moved["lineups"],
        )

    def doing(payload: dict[str, Any]) -> str:
        return (
            f"Moving **{payload['driver_name']}** to **{payload['team_full_name']}** in "
            f"**{payload['to_division']}**"
        )

    def done(payload: dict[str, Any], saved: dict[str, Any], _roles_refused: bool) -> str:
        return (
            f"✅ Moved **{payload['driver_name']}** from **{saved['from_team']}** in "
            f"**{saved['from_division']}** to **{saved['to_team']}** in **{saved['to_division']}**."
        )

    def details(payload: dict[str, Any], saved: dict[str, Any]) -> str:
        return (
            f"\n  user: {payload['driver_name']} (<@{payload['discord_user_id']}>)"
            f"\n  from: {saved['from_team']}, {saved['from_division']}"
            f"\n  to: {saved['to_team']}, {saved['to_division']}"
        )

    return _change(
        _Kind(
            kind=DRIVER_MOVE, command=MOVE_COMMAND, doing=doing, nothing_done=NOTHING_MOVED,
            done=done, details=details, check=check, apply=apply,
        ),
        placement,
    )


# ── /driver sack ──────────────────────────────────────────────────────────────────────


def driver_sack_change(*, placement: PlacementService) -> ChangeType:
    """The `core.driver_sack` change type, for the builder to register: *placement* is what the
    check judges the sack through, the save sacks the driver through, and the jobs after it take
    the roles and post the lineups through.

    The payload names the driver (`driver_profile_id`, `discord_user_id`, `driver_name`) and the
    season (`season_id`). The roles come after the save, which reads them as it frees the seats.
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        refused = await ongoing_refusal(
            ctx.db_path, int(payload["season_id"]), SACK_COMMAND
        ) or await profile_refusal(ctx.db_path, payload)
        if refused is not None:
            return refused
        try:
            async with get_connection(ctx.db_path) as db:
                await placement.judge_sack_on(
                    db, driver_profile_id=int(payload["driver_profile_id"]),
                    season_id=int(payload["season_id"]),
                )
        except ValueError as refusal:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> _Saved:
        payload = ctx.payload
        sacked = await placement.sack_seats_on(
            db, driver_profile_id=int(payload["driver_profile_id"]),
            season_id=int(payload["season_id"]),
        )
        return _Saved(
            result={},
            audit=AuditRecord(
                "DRIVER_SACK", sacked.audit["old_value"], sacked.audit["new_value"], None
            ),
            user_id=sacked.user_id, revoke=sacked.revoke_role_ids, lineups=sacked.divisions,
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Sacking **{payload['driver_name']}**"

    def done(payload: dict[str, Any], _saved: dict[str, Any], roles_refused: bool) -> str:
        removed = "Season assignments" if roles_refused else "All roles and season assignments"
        return f"✅ **{payload['driver_name']}** has been sacked. {removed} removed."

    def details(payload: dict[str, Any], _saved: dict[str, Any]) -> str:
        return f"\n  user: {payload['driver_name']} (<@{payload['discord_user_id']}>)"

    return _change(
        _Kind(
            kind=DRIVER_SACK, command=SACK_COMMAND, doing=doing, nothing_done=NOBODY_SACKED,
            done=done, details=details, check=check, apply=apply,
        ),
        placement,
    )


# ── /driver release ───────────────────────────────────────────────────────────────────


def driver_release_change(*, placement: PlacementService) -> ChangeType:
    """The `core.driver_release` change type, for the builder to register: *placement* is what
    the check judges the release through, the save releases the driver through, and the jobs
    after it take the roles and post the division's lineup through.

    The payload names the driver (`driver_profile_id`, `discord_user_id`, `driver_name`), the
    season (`season_id`) and the division (`division_id`, with its name as typed, `division`).
    The roles come after the save, which reads them as it frees the seat.
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        season_id = int(payload["season_id"])
        refused = (
            await ongoing_refusal(ctx.db_path, season_id, RELEASE_COMMAND)
            or await division_refusal(
                ctx.db_path, season_id, [(int(payload["division_id"]), payload["division"])]
            )
            or await profile_refusal(ctx.db_path, payload)
        )
        if refused is not None:
            return refused
        try:
            async with get_connection(ctx.db_path) as db:
                await placement.judge_release_on(
                    db, driver_profile_id=int(payload["driver_profile_id"]),
                    division_id=int(payload["division_id"]), season_id=season_id,
                )
        except ValueError as refusal:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> _Saved:
        payload = ctx.payload
        released = await placement.unassign_on(
            db, driver_profile_id=int(payload["driver_profile_id"]),
            division_id=int(payload["division_id"]), season_id=int(payload["season_id"]),
        )
        audit = released["audit"]
        return _Saved(
            result={"division_name": released["division_name"]},
            audit=AuditRecord(
                "DRIVER_UNASSIGN", audit["old_value"], audit["new_value"], audit["division_id"]
            ),
            user_id=released["user_id"], revoke=released["revoke"], lineups=released["lineups"],
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Releasing **{payload['driver_name']}** from **{payload['division']}**"

    def done(payload: dict[str, Any], saved: dict[str, Any], _roles_refused: bool) -> str:
        return f"✅ Released **{payload['driver_name']}** from **{saved['division_name']}**."

    def details(payload: dict[str, Any], saved: dict[str, Any]) -> str:
        return (
            f"\n  user: {payload['driver_name']} (<@{payload['discord_user_id']}>)"
            f"\n  division: {saved['division_name']}"
        )

    return _change(
        _Kind(
            kind=DRIVER_RELEASE, command=RELEASE_COMMAND, doing=doing,
            nothing_done=NOTHING_RELEASED, done=done, details=details, check=check, apply=apply,
        ),
        placement,
    )


__all__ = [
    "DRIVER_MOVE", "DRIVER_RELEASE", "DRIVER_SACK", "NOBODY_SACKED", "NOTHING_MOVED",
    "NOTHING_RELEASED", "driver_move_change", "driver_release_change", "driver_sack_change",
]
