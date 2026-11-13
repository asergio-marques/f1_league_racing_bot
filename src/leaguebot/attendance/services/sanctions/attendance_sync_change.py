"""`/attendance sync` as one change on the queue: `attendance.sync`.

The sync recovers a run of sanctions that did not all apply: every finalised round from the one
named on is recalculated from its results, the sanctions the latest of them owes are applied, and
their cards, the lineups and the sheets are posted. Its jobs, in this order:

1. **The reserve's capacity** (`CHECK_RESERVE`), read before the save: the guards that an
   autoreserve's move into the division's Reserve team meets read templates off the loop, which a
   save cannot. They concern the team and not the driver, so the one answer serves every
   autoreserve of the run; a driver it refuses is a sanction not applied.
2. **One save** (`SYNC_SAVE`): the recalculation and every sanction it owes, on the one
   connection, so that a stop leaves both or neither. Who is owed is read there, on the totals
   just written. A sanction that does not apply (`SanctionNotApplicable`, or the capacity the
   first job found refused) is named and the next driver taken, and the change still finishes:
   the sync is the recovery for sanctions that did not all apply, and one that cannot never
   stops the others (the attendance specification, "Resynchronising attendance"). What the save
   plans after itself is each saved sanction's roles, the banner and the cards, the lineups and
   the sheets, all jobs of this change and so finished once the bot is back.
3. **The roles** of a driver sanctioned (`role_moves.role_move_step`) end on a fault and never stop
   the lane, as they always have for the sync: the sanction stands and the fault goes to the
   host's log.
4. **The cards, the lineups and the sheets**, results' and core's and attendance's own jobs, which
   stop the lane where Discord refuses them, to be tried again as text or discarded.
5. **The line** (`SYNC_CLOSE`), written last so that it carries what a Discard left undone.

**A second sync announces nothing already announced:** a driver already sacked is no longer owed
a sanction, and one already in Reserve is passed over, so it finds nothing to apply and plans no
card. It posts the sheet again, as the sync always has.

**The refusals are the check,** made as the sync is asked and again as it starts, in the words
the command has always used (`sync_verdict`): the module on, the season ongoing, no amendment
open in the division, the round's penalties approved and the channels the recalculation posts to
reachable. A sync that fails the second check ends refused and the lane goes on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.attendance.services.scoring import attendance_recompute, attendance_scoring
from leaguebot.attendance.services.sanctions import attendance_sanctions
from leaguebot.attendance.services.sheet import sheet_posting
from leaguebot.attendance.services.settings.channel_settings import NOT_ENABLED
from leaguebot.attendance.services.settings.module_switch_settings import attendance_enabled_on
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.models.refusal import Refused
from leaguebot.core.models.season import ONGOING_STAGES
from leaguebot.core.services.drivers import lineup_posting
from leaguebot.core.services.drivers.role_moves import role_move_step
from leaguebot.core.services.posting import standing_posts
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.seasons import season_service
from leaguebot.core.services.seasons.season_approval_change import unposted_lineup_line
from leaguebot.core.utils.league_server import league_guild
from leaguebot.results.services.review.review_verdicts import (
    ANNOUNCE_HEADING,
    ANNOUNCE_SANCTION,
    not_done as announcements_not_done,
    verdict_steps,
)
from leaguebot.results.services.round_amendment.amendment_writes import (
    amendment_wait_text,
    open_amendment_in_division,
)

if TYPE_CHECKING:
    from leaguebot.core.services.drivers.lineup_posting import LineupPoster
    from leaguebot.core.services.drivers.placement_service import PlacementService
    from leaguebot.core.services.drivers.team_role_service import TeamRoleService
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.utils.league_bot import LeagueBot
    from leaguebot.results.services.module.attendance_hook import AttendanceAfterReview

log = logging.getLogger(__name__)

SYNC = "attendance.sync"

#: The names of the jobs, as the stop notice, the outcome and the tests know them.
CHECK_RESERVE = "sync_check_reserve"
SYNC_SAVE = "sync_save"
SYNC_ROLES = "sync_roles"
SYNC_CLOSE = "sync_close"

#: What Discord's audit log is given for the roles a sanction takes.
ROLES_REASON = "Attendance sanction"

#: `/attendance sync` as the line names it.
COMMAND = "/attendance sync"

NOT_ONGOING = "⛔ `/attendance sync` is available only while the season is ongoing."

#: The candidate the reserve's capacity is asked of: the guards concern the team, not a driver.
_RESERVE_PROBE = {"sanction": "AUTORESERVE", "driver_profile_id": 0, "driver_user_id": 0}


def amendment_verdict(held: dict[str, Any] | None, division_name: str) -> Verdict | None:
    """The refusal of a sync while a round of the division is amended, given the amendment open
    there (`open_amendment_in_division`), else None.

    The recalculation reads the results, which hold an open amendment's corrections before they
    are approved, and publishes the sheet and applies the sanctions it finds, neither of which a
    cancelled or lapsed amendment then takes back.
    """
    if held is None:
        return None
    return Verdict.refuse(
        f"⏸️ Round {held['round_number']} of **{division_name}** is being amended "
        f"in <#{held['channel_id']}>, and its corrections are not approved yet, so its "
        f"attendance cannot be recalculated until that ends — {amendment_wait_text()}. "
        "Run this again then."
    )


def round_verdict(status: str | None, division_name: str, round_number: int) -> Verdict | None:
    """The refusal of a round the division has not got (*status* None) or one whose penalties are
    not approved yet, else None."""
    if status is None:
        return Verdict.refuse(f"❌ **{division_name}** has no round {round_number}.")
    if status not in ("AWAITING_APPEAL_VERDICTS", "FINAL"):
        return Verdict.refuse(
            f"⛔ Round {round_number} of **{division_name}** has not had its penalties "
            "approved yet, so it holds no attendance to recalculate."
        )
    return None


def faults_verdict(faults: Sequence[str]) -> Verdict | None:
    """The refusal of a sync whose recalculation could not be posted, given the faults
    `recalculation_faults` found, else None."""
    if not faults:
        return None
    return Verdict.refuse(
        "⛔ Nothing was changed — the attendance could not be posted:\n• "
        + "\n• ".join(faults),
        reason="the attendance could not be posted:\n"
        + "\n".join(f"• {fault}" for fault in faults),
    )


async def sync_verdict(bot: "LeagueBot", db_path: str, payload: dict[str, Any]) -> Verdict | None:
    """The refusal of the sync *payload* asks, judged on the database as it stands, else None: the
    check of the change, as it is asked and as it starts."""
    async with get_connection(db_path) as db:
        if not await attendance_enabled_on(db):
            return Verdict.refuse(NOT_ENABLED)
    season = await season_service.live_season(db_path)
    if season is None or season.stage not in ONGOING_STAGES:
        return Verdict.refuse(NOT_ONGOING)
    name = str(payload["division"])
    held = await open_amendment_in_division(db_path, int(payload["division_id"]))
    refusal = amendment_verdict(held, name)
    if refusal is not None:
        return refusal
    found = await season_service.round_by_id(
        db_path, int(payload["division_id"]), int(payload["round_id"])
    )
    refusal = round_verdict(
        None if found is None else str(found.status), name, int(payload["round_number"])
    )
    if refusal is not None:
        return refusal
    faults = await attendance_recompute.recalculation_faults(
        db_path, season.id, await league_guild(bot), bot
    )
    return faults_verdict(faults)


def _view(ctx: OutcomeContext, name: str) -> StepView | None:
    """The last job named *name*."""
    return next((view for view in reversed(ctx.steps) if view.name == name), None)


def _saved(ctx: OutcomeContext) -> dict[str, Any] | None:
    """What the save returned, or None where it did not go through: a league admin discarded it."""
    view = _view(ctx, SYNC_SAVE)
    if view is None or not view.done or not view.result or not view.result.get("saved"):
        return None
    return view.result


def _driver(entry: dict[str, Any], *, quoted: bool = False) -> str:
    """The driver of a sanction as a line names them: their mention, wrapped where the log
    channel must not notify them, and their name where a test driver has one."""
    mention = f"<@{entry['driver_user_id']}>"
    named = f"`{mention}`" if quoted else mention
    return f"{named} ({entry['name']})" if entry.get("name") else named


def _failure(entry: dict[str, Any], *, quoted: bool = False) -> str:
    return f"{_driver(entry, quoted=quoted)} — {entry['sanction']}: {entry['reason']}"


def not_done_lines(ctx: OutcomeContext) -> list[str]:
    """What a league admin's Discard left undone, in the order the jobs stood: the cards and the
    banner (results' words), a lineup, a sheet and the deletion of a post replaced."""
    lines = list(announcements_not_done(ctx))
    for view in ctx.steps:
        result = view.result or {}
        if view.name == lineup_posting.POST_LINEUP and "discarded" in result:
            lines.append(unposted_lineup_line(str(view.payload["division_name"])))
        elif view.name == sheet_posting.POST_SHEET and "discarded" in result:
            lines.append(
                f"⚠️ The attendance sheet of {view.payload['division_name']} was not "
                f"posted. Run `{COMMAND}` again to post it."
            )
        elif view.name == standing_posts.DELETE_REPLACED:
            line = standing_posts.not_deleted(view)
            if line is not None:
                lines.append(line)
    return lines


def _noted_in_the_host_log(step: Step) -> Step:
    """*step*, the roles' job, which ends on a fault rather than stop the lane, noting the fault
    in the host's log naming the member: the sync's sanction stands whatever Discord refuses."""
    ends = step.on_fault
    assert ends is not None

    def on_fault(ctx: StepContext, error: BaseException) -> StepResult:
        done = ends(ctx, error)
        log.warning("a sanction's roles of member %s were not changed: %s",
                    ctx.step_payload["user_id"], (done.result or {}).get("problem"))
        return done

    return replace(step, on_fault=on_fault)


async def reserve_capacity(
    db_path: str, placement: "PlacementService", round_id: int, division_id: int
) -> dict[str, Any]:
    """What the capacity of the division's Reserve team says of an autoreserve's move, read
    before the save (`CHECK_RESERVE`): ``{}`` where there is nothing to refuse or the division has
    no Reserve team (the save names that), else ``{"refusal": the words}``. Empty where no
    autoreserve threshold is set. The guards concern the team and not the driver, so the one
    answer serves every autoreserve of the division."""
    autoreserve, _autosack = await attendance_sanctions._thresholds(db_path)
    if not autoreserve:
        return {}
    try:
        await attendance_sanctions.check_sanction(
            db_path, placement, round_id, division_id, _RESERVE_PROBE,
        )
    except attendance_sanctions.SanctionNotApplicable:
        return {}
    except Refused as error:
        return {"refusal": str(error)}
    return {}


async def _posts(
    db: aiosqlite.Connection, division_id: int, name: str, latest: int,
    applied: list[dict[str, Any]],
) -> list[PlannedStep]:
    """The cards, the lineups and the sheets the sanctions applied owe: the sheet of the
    division, marking who was sanctioned, once, and a sacked driver's other divisions."""
    sanctioned: dict[int, set[int]] = {
        division_id: {entry["driver_profile_id"] for entry in applied}
    }
    for entry in applied:
        for other in entry["candidate"]["other_divisions"]:
            sanctioned.setdefault(int(other), set()).add(entry["driver_profile_id"])
    names = {division_id: name}
    for other in sanctioned:
        if other not in names:
            found = await season_service.division_on(db, other)
            names[other] = found.name if found is not None else str(other)
    posts: list[PlannedStep] = []
    if applied:
        facts = await season_service.round_facts_on(db, latest)
        heading: dict[str, Any] = {"round_id": latest, "over": "sanctions"}
        if facts is not None:
            heading["round_number"] = facts.round_number
        posts.append(PlannedStep(ANNOUNCE_HEADING, heading))
        posts.extend(
            PlannedStep(ANNOUNCE_SANCTION, {
                "round_id": latest, "division_id": division_id, "division": name,
                "candidate": entry["candidate"], "heading_round_id": latest,
                "applied": True,
            })
            for entry in applied
        )
        posts.extend(
            PlannedStep(lineup_posting.POST_LINEUP,
                        {"division_id": each, "division_name": names[each]})
            for each in sanctioned
        )
    posts.append(PlannedStep(sheet_posting.POST_SHEET, {
        "division_id": division_id, "division_name": name, "round_id": latest,
        "sanctioned": sorted(sanctioned[division_id]),
    }))
    for other in sanctioned:
        if other == division_id:
            continue
        their_latest = await attendance_scoring.latest_scored_round(db, other)
        if their_latest is None:
            continue  # the division has posted no sheet yet, so there is none to correct
        posts.append(PlannedStep(sheet_posting.POST_SHEET, {
            "division_id": other, "division_name": names[other], "round_id": their_latest,
            "sanctioned": sorted(sanctioned[other]),
        }))
    return posts


async def recalculate_division_on(
    db: aiosqlite.Connection,
    *,
    db_path: str,
    placement: "PlacementService",
    acting: Any,
    now: datetime,
    division_id: int,
    division_name: str,
    season_id: int,
    from_round_id: int,
    capacity: dict[str, Any],
) -> StepResult:
    """One division's recalculation and the sanctions it owes, on *db*: every approved round from
    *from_round_id* on is recalculated, the sanctions the latest of them owes are applied (those
    that cannot be are named and the next driver taken), and the roles, cards, lineups and sheets
    that follow are planned as later jobs.

    The save of `/attendance sync` and, once for each division, of a penalty changed while the
    season is ongoing (`penalty_recalculation`): the one copy of what a recalculation saves.
    *acting* is the bot's user, which sanctions are recorded as, *now* the queue's clock and
    *capacity* the answer of `reserve_capacity`. The result holds ``saved``, ``latest``,
    ``applied`` and ``failed``; the lines are the sanctions' log lines.
    """
    touched = await attendance_recompute._recalculate_forward(
        db_path, from_round_id, division_id, recompute="all", db=db
    )
    latest = touched[-1]
    owed, signed_off = await attendance_sanctions.owed_sanctions(
        db_path, latest, division_id, season_id, db=db
    )
    lines: list[str] = [
        f"ATTENDANCE_AUTOSACK | No-op | driver_profile_id={profile_id} "
        f"already NOT_SIGNED_UP (total={total})"
        for profile_id, total in signed_off
    ]
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    role_jobs: list[PlannedStep] = []
    for candidate in owed:
        profile_id = int(candidate["driver_profile_id"])
        entry = {
            "driver_profile_id": profile_id,
            "driver_user_id": int(candidate["driver_user_id"]),
            "name": await attendance_sanctions._display_name_on(
                db, int(candidate["driver_user_id"])
            ),
            "sanction": "autosack" if candidate["sanction"] == "AUTOSACK" else "autoreserve",
        }
        if candidate["sanction"] == "AUTORESERVE" and capacity.get("refusal"):
            failed.append({**entry, "reason": str(capacity["refusal"])})
            continue
        try:
            done = await attendance_sanctions.apply_sanction_on(
                db, placement, latest, division_id, candidate,
                actor_id=acting.id, actor_name=str(acting), now=now,
            )
        except (attendance_sanctions.SanctionNotApplicable, Refused) as error:
            failed.append({**entry, "reason": str(error)})
            continue
        applied.append({**entry, "candidate": candidate})
        lines.append(done["line"])
        if done["roles"]:
            role_jobs.append(PlannedStep(SYNC_ROLES, {
                "user_id": done["roles"]["user_id"],
                "revoke": done["roles"]["revoke"], "grant": done["roles"]["grant"],
            }))
    then = [*role_jobs, *await _posts(db, division_id, division_name, latest, applied)]
    return StepResult(
        result={"saved": True, "latest": latest, "applied": applied, "failed": failed},
        lines=tuple(lines),
        then=tuple(then),
    )


def sanction_reply_lines(applied: list[dict[str, Any]], failed: list[dict[str, Any]]) -> list[str]:
    """What a reply says of the sanctions a recalculation owed, as the sync and a penalty's change
    both say it: those applied, or that none was owed, and those still not applied."""
    reply: list[str] = []
    if applied:
        reply.append("Sanctions applied:")
        reply += [f"• {_driver(entry)} — {entry['sanction']}" for entry in applied]
        reply.append("Their announcements follow on the change queue.")
    elif not failed:
        reply.append("No sanction was owed.")
    if failed:
        reply.append("⚠️ Still not applied:")
        reply += [f"• {_failure(entry)}" for entry in failed]
    return reply


def posting_steps(
    *,
    roles: "TeamRoleService",
    lineups: "LineupPoster",
    attendance: "AttendanceAfterReview",
    modules: "ModuleService",
    now: Callable[[], datetime],
) -> dict[str, Step]:
    """The jobs a recalculation plans after its save, by name: a sanctioned driver's roles, the
    cards and their heading, the lineups, the sheets and the deletion of a post replaced. The
    sync's and a penalty's change both run them."""
    announce = verdict_steps(now, attendance)
    return {
        SYNC_ROLES: _noted_in_the_host_log(role_move_step(roles, SYNC_ROLES, reason=ROLES_REASON)),
        ANNOUNCE_HEADING: announce[ANNOUNCE_HEADING],
        ANNOUNCE_SANCTION: announce[ANNOUNCE_SANCTION],
        lineup_posting.POST_LINEUP: lineup_posting.lineup_step(lineups, now=now),
        sheet_posting.POST_SHEET: sheet_posting.sheet_step(modules),
        standing_posts.DELETE_REPLACED: standing_posts.delete_replaced_step(),
    }


def attendance_sync_change(
    *,
    placement: "PlacementService",
    roles: "TeamRoleService",
    lineups: "LineupPoster",
    attendance: "AttendanceAfterReview",
    modules: "ModuleService",
    now: Callable[[], datetime],
) -> ChangeType:
    """`attendance.sync`, see the module. *placement* applies the sanctions on the save, *roles*
    gives and takes a sanctioned driver's roles, *lineups* posts the lineups, *attendance* is the
    hook results' jobs post the cards through, *modules* says whether attendance is on and *now*
    is the queue's clock."""

    async def check(ctx: CheckContext) -> Verdict:
        return await sync_verdict(ctx.bot, ctx.db_path, ctx.payload) or Verdict.go()

    async def check_reserve(ctx: StepContext) -> StepResult:
        """The capacity the Reserve team is refused, if it is, read before the save."""
        return StepResult(result=await reserve_capacity(
            ctx.db_path, placement, int(ctx.payload["round_id"]), int(ctx.payload["division_id"])
        ))

    async def describe_check(ctx: StepContext) -> str:
        return f"checking the Reserve team of **{ctx.payload['division']}** can take a driver"

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The recalculation and the sanctions it owes, on the one connection; see the module."""
        payload = ctx.payload
        capacity = next(
            (view.result or {} for view in ctx.steps if view.name == CHECK_RESERVE), {}
        )
        acting = ctx.bot.user
        # Sanctions are enforced by a bot that has logged in, which is when it has a user.
        assert acting is not None, "sanctions enforced before the bot logged in"
        return await recalculate_division_on(
            db, db_path=ctx.db_path, placement=placement, acting=acting, now=now(),
            division_id=int(payload["division_id"]), division_name=str(payload["division"]),
            season_id=int(payload["season_id"]), from_round_id=int(payload["round_id"]),
            capacity=capacity,
        )

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the line that records the change, with what was not done."""
        saved = _saved(ctx)
        if saved is None:
            return StepResult(result={"closed": True})
        failed = saved["failed"]
        payload = ctx.payload
        lines = [
            f"{ctx.named} | {COMMAND} | {'Incomplete' if failed else 'Success'}",
            f"  division: {payload['division']}",
            f"  from round: {payload['round_number']}",
            f"  sanctions applied: {len(saved['applied'])}",
            *(f"  {_failure(entry)}" for entry in failed),
            *(f"  {each}" for each in not_done_lines(ctx)),
        ]
        if failed:
            lines.append(
                "  " + attendance_sanctions.sync_hint_text(
                    str(payload["division"]), int(payload["round_number"])
                )
            )
        return StepResult(result={"closed": True}, lines=("\n".join(lines),))

    async def describe_close(ctx: StepContext) -> str:
        return f"recording `{COMMAND}` of **{ctx.payload['division']}**"

    def outcome(ctx: OutcomeContext) -> str:
        payload = ctx.payload
        saved = _saved(ctx)
        if saved is None:
            return (
                f"Nothing was recalculated: attendance of **{payload['division']}** stays as it "
                f"was. Run `{COMMAND}` again."
            )
        reply = [
            f"✅ Attendance of **{payload['division']}** recalculated from round "
            f"{payload['round_number']} on."
        ]
        reply += sanction_reply_lines(saved["applied"], saved["failed"])
        return "\n".join(reply)

    steps = {
        CHECK_RESERVE: Step(CHECK_RESERVE, StepKind.ACT, check_reserve, describe=describe_check),
        SYNC_SAVE: Step(SYNC_SAVE, StepKind.SAVE, save),
        **posting_steps(
            roles=roles, lineups=lineups, attendance=attendance, modules=modules, now=now
        ),
        SYNC_CLOSE: Step(SYNC_CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=SYNC,
        opening=(PlannedStep(CHECK_RESERVE), PlannedStep(SYNC_SAVE), PlannedStep(SYNC_CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{SYNC}:{payload['division_id']}:{payload['round_id']}",
        doing=lambda payload: (
            f"Recalculating the attendance of **{payload['division']}** from round "
            f"{payload['round_number']} on and applying the sanctions it owes"
        ),
        outcome=outcome,
    )
