"""`core.driver_move`, `core.driver_sack`, `core.driver_release`, `core.driver_assign`,
`core.driver_unassign`, `core.driver_reject` and `core.driver_reassign`: `/driver move`, `sack`,
`release`, `assign`, `unassign`, `reject` and `reassign`, on the change queue.

Each is a change type of its own on the league lane, registered by the builder. Move, sack and
release are for a committed driver while the season is ongoing: moved to a team of the same
division or another, sacked from every seat, or released from one division and keeping the rest.
Assign and unassign are for a driver whose placement is not yet confirmed, while drivers are being
placed: assigned to a team of a division, or taken out of one. The driver, the season and the
divisions are named by their ids at the ask, a team by its shorthand. A change is four kinds of
job, in this order:

1. **One save** changes the seats (`PlacementService.move_seat_on`, `sack_seats_on`,
   `unassign_on`, `assign_on`) and writes the audit entry (`DRIVER_MOVE`, `DRIVER_SACK`,
   `DRIVER_UNASSIGN`, `DRIVER_ASSIGN`), stamped with the queue's clock, and plans what follows
   from what it read inside it: the roles to take and give, and the lineup of each division
   touched.
2. **The member's roles** (`role_moves.role_move_step`), which end on a fault rather than stop the
   lane, since the seat is the league's record and a role Discord refuses is the member's to be
   given or taken by hand: it is reported in the reply and the line. They come after the save,
   a sack's too.
3. **Each lineup** (`lineup_posting.lineup_step`), which stops the lane where Discord refuses it,
   as every post does, and says what it left undone where a league admin discards it.
4. **The line** that records the change, written last so that it carries what was not done.

A rejection is for a driver approved and not yet placed (Unassigned), while drivers are being
placed: the one save turns them down (`write_transition`), calls the *driver_turned_down* hook
each module signs up to (signup withdraws the approval of their signup), and writes the
`DRIVER_REJECT` audit entry, shaped as a sack's; the one job after it takes the driver role. It
is judged again as it starts for the stage, a driver no longer Unassigned, and a driver whose
current account is not the one asked for and has left the server.

A reassignment makes another account the driver's current one, for a driver being placed, and is a
change of its own shape (`driver_reassign_change`): the accounts are named at the ask, by id, and
the one save makes the new account current, or merges the two drivers where the new one is
another's, writing the `DRIVER_USER_ID_REASSIGN` audit entry and the line, and judges every
refusal again on the save's own connection, so that a signup begun or a second reassignment
between the check and the write cannot slip past. A refusal found there ends the change in its
words, changing nothing and planning nothing, and does not stop the lane. After the save come the
roles, given to the new account and taken from the replaced one, the jobs each module plans for
a driver's account changed (signup's held channel), and the replaced account's portrait, discarded.
The roles and the modules' jobs end on a fault as a role does for a move, and are reported with the
line.

An assignment and an unassignment have no job after the save: the placement they write or remove
is never confirmed, so it holds no role and posts no lineup, which follow when placements are
confirmed. Both are refused a driver holding a confirmed placement, whom `/driver move` and
`/driver release` change instead.

**A fault in the save stops the lane**, as a failed job does, for the reason `/division delete`'s
does: a league admin exists to discard the job and clear the stop, and nothing of the change is
kept. The roles and the lineups are planned by the save and so never follow a discarded one.

**The change is judged by its ids** and every refusal is the check's, at the ask and again as the
change starts, in the league's log channel either time: the season no longer in the stage the
command is made in, a division not in the season, the driver without a profile, and what the
placement service refuses (`PlacementService.judge_move_on`, `guard_move`, `judge_sack_on`,
`judge_release_on`, `guard_assign`, `judge_assign_on`, `judge_unassign_on`). The roles go to the
account the save read from the driver's profile, so a driver given another account meanwhile is
not left with the old one's.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    AuditRecord,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.models.refusal import Refused
from leaguebot.core.models.season import ONGOING_STAGES, SeasonStage
from leaguebot.core.services import lineup_posting
from leaguebot.core.services.posting import standing_posts
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.driver_service import DriverService, write_transition
from leaguebot.core.services.lineup_posting import LineupPoster
from leaguebot.core.services.placement_service import PlacementService
from leaguebot.core.services.role_moves import MOVE_ROLES, PLACEMENT_REASON, role_move_step
from leaguebot.core.services.seasons.season_approval_change import unposted_lineup_line
from leaguebot.core.services.team_role_service import TeamRoleService
from leaguebot.core.utils.hooks import AwaitedHook, Hook
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason
from leaguebot.core.utils.season_gate import PLACEMENT_STAGES

if TYPE_CHECKING:
    from leaguebot.core.services.modules.image_drawing import ImageDrawing

DRIVER_MOVE = "core.driver_move"
DRIVER_SACK = "core.driver_sack"
DRIVER_RELEASE = "core.driver_release"
DRIVER_ASSIGN = "core.driver_assign"
DRIVER_UNASSIGN = "core.driver_unassign"
DRIVER_REJECT = "core.driver_reject"
DRIVER_REASSIGN = "core.driver_reassign"

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

#: `/driver reject` as the line names it.
REJECT_COMMAND = "/driver reject"

#: The reply once a league admin has discarded the stopped save: nobody was turned down.
NOBODY_TURNED_DOWN = (
    "Nothing was turned down: **{driver_name}** is still an Unassigned driver. "
    "Run `/driver reject` again."
)

#: What `/driver reject` answers a driver who is not Unassigned.
NOT_UNASSIGNED_REFUSAL = (
    "⛔ **{driver_name}** is not an Unassigned driver. A placed driver is unassigned first; a "
    "signup still in review is rejected from its panel."
)

#: What a command answers for a past account of a driver whose current account has left the server.
PAST_ACCOUNT_REFUSAL = (
    "⛔ <@{account}> is a past account of a driver whose current account, <@{current}>, is not "
    "in the server."
)

#: `/driver assign` as the line names it.
ASSIGN_COMMAND = "/driver assign"

#: `/driver unassign` as the line names it.
UNASSIGN_COMMAND = "/driver unassign"

#: The reply once a league admin has discarded the stopped save: nobody was placed.
NOTHING_ASSIGNED = (
    "Nothing was assigned: **{driver_name}** is not placed in **{division}**. "
    "Run `/driver assign` again."
)

#: The same for an unassignment: nobody was taken out.
NOTHING_UNASSIGNED = (
    "Nothing was unassigned: **{driver_name}** keeps their seat in **{division}**. "
    "Run `/driver unassign` again."
)

_NOT_ONGOING = "⛔ `{command}` is available only while the season is ongoing."

#: What a command that places drivers answers outside the stages it is made in.
NOT_PLACING_REFUSAL = (
    "⛔ `{command}` is available only while the season is in placements, or mid-season "
    "while the drivers of a closed signup window are being placed."
)


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
    #: Whether the roles and lineups the save plans have jobs of their own: not for a change
    #: that writes only an unconfirmed placement.
    afterwards: bool = True
    #: What Discord's audit log is given for the roles the change takes.
    role_reason: str = PLACEMENT_REASON


async def ongoing_refusal(db_path: str, season_id: int, command: str) -> Verdict | None:
    """The refusal of *command* where season *season_id* is not in an ongoing stage, else None."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
        row = await cursor.fetchone()
    if row is None or SeasonStage(row["stage"]) not in ONGOING_STAGES:
        return Verdict.refuse(_NOT_ONGOING.format(command=command))
    return None


async def placing_refusal(db_path: str, season_id: int, command: str) -> Verdict | None:
    """The refusal of *command* where season *season_id* is not in a stage drivers are placed in,
    else None."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
        row = await cursor.fetchone()
    if row is None or SeasonStage(row["stage"]) not in PLACEMENT_STAGES:
        return Verdict.refuse(NOT_PLACING_REFUSAL.format(command=command))
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


def _change(
    kind: _Kind,
    now: Callable[[], datetime],
    roles: TeamRoleService | None = None,
    lineups: LineupPoster | None = None,
) -> ChangeType:
    """The change type *kind* describes, its jobs as the module says, the lineup's audit stamped by
    *now*, the queue's clock. A change with jobs after its save (`kind.afterwards`) is handed
    *roles*, the team-role service that gives and takes the roles it planned, and *lineups*, the
    poster of the lineups it names."""

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

    steps = {
        SAVE: Step(SAVE, StepKind.SAVE, save),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    if kind.afterwards:
        if roles is None or lineups is None:
            raise TypeError(
                f"{kind.kind} gives roles and posts lineups and is handed no team-role service "
                "or lineup poster"
            )
        steps[MOVE_ROLES] = role_move_step(roles, reason=kind.role_reason)
        steps[POST_LINEUP] = lineup_posting.lineup_step(lineups, now=now)
        steps[standing_posts.DELETE_REPLACED] = standing_posts.delete_replaced_step()
    return ChangeType(
        kind=kind.kind,
        opening=(PlannedStep(SAVE), PlannedStep(CLOSE)),
        steps=steps,
        check=kind.check,
        key=lambda payload: f"{kind.kind}|{json.dumps(payload, sort_keys=True)}",
        doing=kind.doing,
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


# ── /driver move ──────────────────────────────────────────────────────────────────────


def driver_move_change(
    *, placement: PlacementService, roles: TeamRoleService, lineups: LineupPoster,
    now: Callable[[], datetime],
) -> ChangeType:
    """The `core.driver_move` change type, for the builder to register: *placement* is what the
    check judges the move through and the save moves the driver through; the jobs after it give
    the roles through *roles* and post the lineups through *lineups*.

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
        to_division_id = int(payload["to_division_id"])
        from_division_id = int(payload["from_division_id"])
        driver_profile_id = int(payload["driver_profile_id"])
        team_name = str(payload["team_name"])
        try:
            await placement.guard_move(to_division_id, season_id, team_name)
            async with get_connection(ctx.db_path) as db:
                await placement.judge_move_on(
                    db, driver_profile_id=driver_profile_id, season_id=season_id,
                    from_division_id=from_division_id, to_division_id=to_division_id,
                    team_name=team_name,
                )
        except Refused as refusal:
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
        now, roles, lineups,
    )


# ── /driver sack ──────────────────────────────────────────────────────────────────────


def driver_sack_change(
    *, placement: PlacementService, roles: TeamRoleService, lineups: LineupPoster,
    now: Callable[[], datetime],
) -> ChangeType:
    """The `core.driver_sack` change type, for the builder to register: *placement* is what the
    check judges the sack through and the save sacks the driver through; the jobs after it take
    the roles through *roles* and post the lineups through *lineups*.

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
        driver_profile_id = int(payload["driver_profile_id"])
        season_id = int(payload["season_id"])
        try:
            async with get_connection(ctx.db_path) as db:
                await placement.judge_sack_on(
                    db, driver_profile_id=driver_profile_id, season_id=season_id,
                )
        except Refused as refusal:
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
        now, roles, lineups,
    )


# ── /driver release ───────────────────────────────────────────────────────────────────


def driver_release_change(
    *, placement: PlacementService, roles: TeamRoleService, lineups: LineupPoster,
    now: Callable[[], datetime],
) -> ChangeType:
    """The `core.driver_release` change type, for the builder to register: *placement* is what
    the check judges the release through and the save releases the driver through; the jobs
    after it take the roles through *roles* and post the division's lineup through *lineups*.

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
        driver_profile_id = int(payload["driver_profile_id"])
        division_id = int(payload["division_id"])
        try:
            async with get_connection(ctx.db_path) as db:
                await placement.judge_release_on(
                    db, driver_profile_id=driver_profile_id, division_id=division_id,
                    season_id=season_id,
                )
        except Refused as refusal:
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
        now, roles, lineups,
    )


# ── /driver assign ────────────────────────────────────────────────────────────────────


def driver_assign_change(
    *, placement: PlacementService, now: Callable[[], datetime]
) -> ChangeType:
    """The `core.driver_assign` change type, for the builder to register: *placement* is what the
    check judges the assignment through and the save assigns the driver through.

    The payload names the driver (`driver_profile_id`, `discord_user_id`, `driver_name`), the
    season (`season_id`), the division (`division_id`, with its name as typed, `division`) and
    the team (`team_name`, the shorthand, and `team_full_name`). The placement is written
    unconfirmed, so the save plans no role and no lineup.
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        season_id = int(payload["season_id"])
        division_id = int(payload["division_id"])
        refused = (
            await placing_refusal(ctx.db_path, season_id, ASSIGN_COMMAND)
            or await division_refusal(
                ctx.db_path, season_id, [(division_id, payload["division"])]
            )
            or await profile_refusal(ctx.db_path, payload)
        )
        if refused is not None:
            return refused
        team_name = str(payload["team_name"])
        driver_profile_id = int(payload["driver_profile_id"])
        try:
            await placement.guard_assign(division_id, season_id, team_name, driver_profile_id)
            async with get_connection(ctx.db_path) as db:
                await placement.judge_assign_on(
                    db, driver_profile_id=driver_profile_id, division_id=division_id,
                    team_name=team_name, season_id=season_id, uncommitted_only=True,
                )
        except Refused as refusal:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> _Saved:
        payload = ctx.payload
        assigned = await placement.assign_on(
            db, driver_profile_id=int(payload["driver_profile_id"]),
            division_id=int(payload["division_id"]), team_name=str(payload["team_name"]),
            season_id=int(payload["season_id"]), uncommitted_only=True,
        )
        audit = assigned["audit"]
        return _Saved(
            result={key: assigned[key] for key in ("team_name", "division_name")},
            audit=AuditRecord(
                "DRIVER_ASSIGN", audit["old_value"], audit["new_value"], audit["division_id"]
            ),
            user_id=int(payload["discord_user_id"]),
        )

    def doing(payload: dict[str, Any]) -> str:
        return (
            f"Assigning **{payload['driver_name']}** to **{payload['team_full_name']}** in "
            f"**{payload['division']}**"
        )

    def done(payload: dict[str, Any], saved: dict[str, Any], _roles_refused: bool) -> str:
        return (
            f"✅ Assigned **{payload['driver_name']}** to **{saved['team_name']}** in "
            f"**{saved['division_name']}**."
        )

    def details(payload: dict[str, Any], saved: dict[str, Any]) -> str:
        return (
            f"\n  user: {payload['driver_name']} (<@{payload['discord_user_id']}>)"
            f"\n  team: {saved['team_name']}"
            f"\n  division: {saved['division_name']}"
        )

    return _change(
        _Kind(
            kind=DRIVER_ASSIGN, command=ASSIGN_COMMAND, doing=doing,
            nothing_done=NOTHING_ASSIGNED, done=done, details=details, check=check, apply=apply,
            afterwards=False,
        ),
        now,
    )


# ── /driver unassign ──────────────────────────────────────────────────────────────────


def driver_unassign_change(
    *, placement: PlacementService, now: Callable[[], datetime]
) -> ChangeType:
    """The `core.driver_unassign` change type, for the builder to register: *placement* is what
    the check judges the unassignment through and the save removes the placement through.

    The payload names the driver (`driver_profile_id`, `discord_user_id`, `driver_name`), the
    season (`season_id`) and the division (`division_id`, with its name as typed, `division`).
    A confirmed placement is refused, so the save plans no role and no lineup.
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        season_id = int(payload["season_id"])
        division_id = int(payload["division_id"])
        refused = (
            await placing_refusal(ctx.db_path, season_id, UNASSIGN_COMMAND)
            or await division_refusal(
                ctx.db_path, season_id, [(division_id, payload["division"])]
            )
            or await profile_refusal(ctx.db_path, payload)
        )
        if refused is not None:
            return refused
        driver_profile_id = int(payload["driver_profile_id"])
        try:
            async with get_connection(ctx.db_path) as db:
                await placement.judge_unassign_on(
                    db, driver_profile_id=driver_profile_id, division_id=division_id,
                    season_id=season_id, uncommitted_only=True,
                )
        except Refused as refusal:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> _Saved:
        payload = ctx.payload
        removed = await placement.unassign_on(
            db, driver_profile_id=int(payload["driver_profile_id"]),
            division_id=int(payload["division_id"]), season_id=int(payload["season_id"]),
            uncommitted_only=True,
        )
        audit = removed["audit"]
        return _Saved(
            result={key: removed[key] for key in ("division_name", "team_name")},
            audit=AuditRecord(
                "DRIVER_UNASSIGN", audit["old_value"], audit["new_value"], audit["division_id"]
            ),
            user_id=removed["user_id"],
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Unassigning **{payload['driver_name']}** from **{payload['division']}**"

    def done(payload: dict[str, Any], saved: dict[str, Any], _roles_refused: bool) -> str:
        team_part = f"from **{saved['team_name']}** " if saved.get("team_name") else ""
        return f"✅ Removed **{payload['driver_name']}** {team_part}in **{saved['division_name']}**."

    def details(payload: dict[str, Any], saved: dict[str, Any]) -> str:
        return (
            f"\n  user: {payload['driver_name']} (<@{payload['discord_user_id']}>)"
            f"\n  division: {saved['division_name']}"
        )

    return _change(
        _Kind(
            kind=DRIVER_UNASSIGN, command=UNASSIGN_COMMAND, doing=doing,
            nothing_done=NOTHING_UNASSIGNED, done=done, details=details, check=check,
            apply=apply, afterwards=False,
        ),
        now,
    )


# ── /driver reject ────────────────────────────────────────────────────────────────────


async def _account_gone_refusal(ctx: CheckContext) -> Verdict | None:
    """The refusal of a driver whose current account is not the one the change was asked for and
    is not in the server, else None: the driver role could not be taken from a member who is not
    there, and the command names the account in use."""
    payload = ctx.payload
    async with get_connection(ctx.db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id FROM driver_profiles WHERE id = ?",
            (int(payload["driver_profile_id"]),),
        )
        row = await cursor.fetchone()
    if row is None or str(row["discord_user_id"]) == str(payload["discord_user_id"]):
        return None
    current = int(row["discord_user_id"])
    guild = await league_guild(ctx.bot)
    if guild is None or guild.get_member(current) is not None:
        return None
    try:
        await guild.fetch_member(current)
    except discord.HTTPException:
        return Verdict.refuse(
            PAST_ACCOUNT_REFUSAL.format(account=payload["discord_user_id"], current=current)
        )
    return None


def driver_reject_change(
    *, roles: TeamRoleService, lineups: LineupPoster, driver_turned_down: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The `core.driver_reject` change type, for the builder to register: *roles* is what the
    job after the save takes the driver role through, *lineups* what posts the lineup, and
    *driver_turned_down* the hook the save calls, given its connection and the driver's profile id,
    for each module to let go of what it holds for them.

    The payload names the driver (`driver_profile_id`, `discord_user_id`, `driver_name`) and the
    season (`season_id`). The driver role is read inside the save and taken after it, from the
    account the save read from the driver's profile.
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals, in the order they come, each read from the database."""
        payload = ctx.payload
        refused = await profile_refusal(ctx.db_path, payload) or await _account_gone_refusal(ctx)
        if refused is None:
            refused = await placing_refusal(
                ctx.db_path, int(payload["season_id"]), REJECT_COMMAND
            )
        if refused is not None:
            return refused
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT current_state FROM driver_profiles WHERE id = ?",
                (int(payload["driver_profile_id"]),),
            )
            row = await cursor.fetchone()
        if row is None or DriverState(row["current_state"]) is not DriverState.UNASSIGNED:
            return Verdict.refuse(NOT_UNASSIGNED_REFUSAL.format(**payload))
        return Verdict.go()

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> _Saved:
        profile_id = int(ctx.payload["driver_profile_id"])
        cursor = await db.execute(
            "SELECT discord_user_id, current_state FROM driver_profiles WHERE id = ?",
            (profile_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"no driver profile {profile_id}")
        state = DriverState(row["current_state"])
        await write_transition(db, profile_id, state, DriverState.NOT_SIGNED_UP)
        await driver_turned_down.call(db, profile_id)
        cursor = await db.execute("SELECT driver_role_id FROM server_configs")
        config = await cursor.fetchone()
        role_id = config["driver_role_id"] if config is not None else None
        return _Saved(
            result={},
            audit=AuditRecord(
                "DRIVER_REJECT",
                {"state": state.value},
                {"new_state": DriverState.NOT_SIGNED_UP.value},
                None,
            ),
            user_id=int(row["discord_user_id"]),
            revoke=[int(role_id)] if role_id else (),
        )

    def doing(payload: dict[str, Any]) -> str:
        return f"Turning down **{payload['driver_name']}**"

    def done(payload: dict[str, Any], _saved: dict[str, Any], _roles_refused: bool) -> str:
        return f"✅ Turned down **{payload['driver_name']}**. They are no longer signed up."

    def details(payload: dict[str, Any], _saved: dict[str, Any]) -> str:
        return f"\n  user: {payload['driver_name']} (<@{payload['discord_user_id']}>)"

    return _change(
        _Kind(
            kind=DRIVER_REJECT, command=REJECT_COMMAND, doing=doing,
            nothing_done=NOBODY_TURNED_DOWN, done=done, details=details, check=check, apply=apply,
            role_reason="Driver rejected",
        ),
        now, roles, lineups,
    )


# ── /driver reassign ──────────────────────────────────────────────────────────────────

#: `/driver reassign` as the line names it.
REASSIGN_COMMAND = "/driver reassign"

#: The step that discards the replaced account's portrait.
DISCARD_PORTRAIT = "discard_portrait"

#: What Discord's audit log is given for the roles a reassignment takes.
REASSIGN_REASON = "Driver's current account changed"

#: The reply once a league admin has discarded the stopped save: no account was changed.
NO_ACCOUNT_CHANGED = (
    "No account was changed: <@{new_user_id}> is not the driver's current account. "
    "Run `/driver reassign` again."
)


def _reassign_reply(saved: dict[str, Any]) -> str:
    """The reply of a reassignment made: how the driver was changed, and the driver as they stand."""
    past = ", ".join(f"<@{each}>" for each in saved["past"]) or "—"
    return (
        f"✅ Driver {saved['how']}.\n"
        f"   Current account : <@{saved['current']}>\n"
        f"   Past accounts   : {past}\n"
        f"   State           : {saved['state']}\n"
        f"   Former driver   : {'Yes' if saved['former'] else 'No'}"
    )


def driver_reassign_change(
    *,
    driver: DriverService,
    placement: PlacementService,
    roles: TeamRoleService,
    profiles_merged: AwaitedHook,
    divisions_raced_read: AwaitedHook,
    driver_account_changed: AwaitedHook,
    driver_account_changed_steps: Hook,
    drawing: "ImageDrawing",
    now: Callable[[], datetime],
) -> ChangeType:
    """The `core.driver_reassign` change type, for the builder to register: *driver* is what the
    check judges the reassignment through and the save writes it through, *placement* what the
    roles are read and given through, *profiles_merged* the hook a merge of two drivers calls
    inside the save, *divisions_raced_read* the hook the modules are asked which divisions each
    side raced in, in the check and in the save, *driver_account_changed* the hook each module's
    part of the change is planned through, called inside the save with the save's connection and
    the account replaced and the new one, handing back the jobs it plans,
    *driver_account_changed_steps* the step definitions of those jobs, by name, *drawing* the
    image module's drawing, which discards the portrait, and *now* the queue's clock.

    The payload names the accounts (`old_user_id`, any account of the driver's, and `new_user_id`,
    both as strings) and the new member (`new_name`, as the line names them). The roles are read
    inside the save, from what the driver's standing entitles them to.
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals of the service, each in its words, read from the database."""
        old_user_id = str(ctx.payload["old_user_id"])
        new_user_id = str(ctx.payload["new_user_id"])
        try:
            async with get_connection(ctx.db_path) as db:
                await driver.judge_reassign_on(
                    db, old_user_id, new_user_id, divisions_raced_read=divisions_raced_read)
        except Refused as refusal:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save; see the module."""
        if ctx.actor_id is None or ctx.actor_name is None:
            raise RuntimeError("reassigning a driver is the act of a member, and none is recorded")
        old_user_id, new_user_id = str(ctx.payload["old_user_id"]), str(ctx.payload["new_user_id"])
        try:
            judged = await driver.judge_reassign_on(
                db, old_user_id, new_user_id, divisions_raced_read=divisions_raced_read)
        except Refused as refusal:
            reply = f"⛔ {refusal}"
            return StepResult(
                result={"refused": reply},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(reply)),),
            )
        outcome = await driver.reassign_judged_on(
            db, judged, new_user_id, ctx.actor_id, ctx.actor_name,
            profiles_merged=profiles_merged, now=now(),
        )
        replaced = outcome.replaced_account
        profile = outcome.profile
        how = "switched back to a past account" if outcome.switched_back else "given a new account"
        if outcome.merged_accounts:
            how = (
                "merged with the driver on "
                + ", ".join(f"<@{each}>" for each in outcome.merged_accounts)
                + ", and given that account"
            )
        steps: list[PlannedStep] = []
        roles = sorted(await placement.driver_role_ids_on(db, profile.id))
        if roles:
            steps.append(PlannedStep(MOVE_ROLES, {"user_id": int(new_user_id), "grant": roles}))
            steps.append(PlannedStep(MOVE_ROLES, {"user_id": int(replaced), "revoke": roles}))
        steps.extend(
            job
            for planned in await driver_account_changed.call(db, replaced, new_user_id)
            for job in planned
        )
        steps.append(PlannedStep(DISCARD_PORTRAIT, {"user_id": replaced}))
        return StepResult(
            result={
                "saved": True,
                "how": how,
                "current": new_user_id,
                "past": [each for each in outcome.accounts if each != new_user_id],
                "state": profile.current_state.value,
                "former": bool(profile.former_driver),
                "replaced": replaced,
                "accounts": list(outcome.accounts),
            },
            audits=(AuditRecord("DRIVER_USER_ID_REASSIGN", replaced, new_user_id, None),),
            then=tuple(steps),
        )

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the line that records the change, with what was not done."""
        saved = _saved_result(ctx)
        if saved is None:
            return StepResult(result={"closed": True})
        line = (
            f"{ctx.named} | {REASSIGN_COMMAND} | Success\n"
            f"  replaced: <@{saved['replaced']}>\n"
            f"  current: {ctx.payload['new_name']} (<@{ctx.payload['new_user_id']}>)\n"
            f"  accounts: {', '.join(saved['accounts'])}"
            + "".join(f"\n  not done: {each}" for each in _problems(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    async def discard_portrait(ctx: StepContext) -> StepResult:
        await drawing.discard_portraits([str(ctx.step_payload["user_id"])])
        return StepResult(result={})

    async def describe_close(ctx: StepContext) -> str:
        return f"recording `{REASSIGN_COMMAND}` of the driver on <@{ctx.payload['old_user_id']}>"

    async def describe_discard(ctx: StepContext) -> str:
        return f"discarding the portrait of <@{ctx.step_payload['user_id']}>"

    def doing(payload: dict[str, Any]) -> str:
        return (
            f"Making <@{payload['new_user_id']}> the current account of the driver on "
            f"<@{payload['old_user_id']}>"
        )

    def outcome(ctx: OutcomeContext) -> str:
        view = _view(ctx, SAVE)
        refused = (view.result or {}).get("refused") if view is not None else None
        if refused:
            return str(refused)
        saved = _saved_result(ctx)
        if saved is None:
            return NO_ACCOUNT_CHANGED.format(**ctx.payload)
        reply = _reassign_reply(saved)
        problems = _problems(ctx)
        if problems:
            reply += "\n⚠️ Done, but on Discord:\n" + "\n".join(f"• {each}" for each in problems)
        return reply

    steps = {
        SAVE: Step(SAVE, StepKind.SAVE, save),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
        MOVE_ROLES: role_move_step(roles, reason=REASSIGN_REASON, give_what_remains=True),
        DISCARD_PORTRAIT: Step(
            DISCARD_PORTRAIT, StepKind.ACT, discard_portrait, describe=describe_discard
        ),
        **{
            name: step
            for defined in driver_account_changed_steps.call()
            for name, step in defined.items()
        },
    }
    return ChangeType(
        kind=DRIVER_REASSIGN,
        opening=(PlannedStep(SAVE), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{DRIVER_REASSIGN}|{json.dumps(payload, sort_keys=True)}",
        doing=doing,
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


def _problems(ctx: OutcomeContext) -> list[str]:
    """What the jobs after a reassignment's save left undone, in the order they ran: each role
    Discord refused and each module's job that ended on a fault, which named its problem."""
    return [
        str(view.result["problem"]) for view in ctx.steps if view.result and view.result.get("problem")
    ]


__all__ = [
    "DRIVER_ASSIGN", "DRIVER_MOVE", "DRIVER_REASSIGN", "DRIVER_REJECT", "DRIVER_RELEASE",
    "DRIVER_SACK", "DRIVER_UNASSIGN", "NO_ACCOUNT_CHANGED", "NOBODY_SACKED", "NOBODY_TURNED_DOWN",
    "NOT_PLACING_REFUSAL", "NOT_UNASSIGNED_REFUSAL", "PAST_ACCOUNT_REFUSAL", "NOTHING_ASSIGNED",
    "NOTHING_MOVED", "NOTHING_RELEASED", "NOTHING_UNASSIGNED", "driver_assign_change",
    "driver_move_change", "driver_reassign_change", "driver_reject_change",
    "driver_release_change", "driver_sack_change", "driver_unassign_change",
]
