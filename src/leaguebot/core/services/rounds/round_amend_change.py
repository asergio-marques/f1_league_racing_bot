"""Amending a round on the change queue.

`/round amend` offers its confirmation. Confirm reads the round for its number and asks the queue
for this change, which is judged when it is asked and again when it comes up to run, in the words
Confirm used: a round that has gone, a window that has passed or results entered while the amendment
waited refuse it with the same reply. It imports nothing from a module and no cog: what it needs of
weather and attendance reaches it through the hooks the builder makes and signs them up to (each
module's part of the judgement, which reads and keeps its own windows, the save, the posts and
timed work, the clean-ups, the steps they define and the words for a step left undone), and it
reads the season through the
service it is handed.

**The refusal because a cancellation is in hand is made only as the change is asked for**
(`CheckContext.change_id` is None), through `cancellation_changes.cancellation_holding_amendment`.
Run, it cannot fire: whatever was ahead of the amendment on the queue has finished, and a
cancellation queued behind it must not make the amendment refuse itself. Two requests asked in the
same instant can both pass; the queue then runs them in order and the second reads what the first
wrote, a renumbering included.

**The same steps arm a round again after a refused cancellation** (`round_rearm_change`): a
cancellation refused at its save has already removed the timed work of its rounds, and asks one
`season.round.rearm` for each round that is still to be run or awaiting its results, which judges
the round as it stands when it runs and arms it as an amended round is armed, catching up what fell
due meanwhile. For a round already raced, or one that ended cancelled for every session being
entered as not held, it arms the next-day clean-ups alone (the payload's `cleanups_only`). Neither
of its two jobs can be discarded.

`amendment_in_hand` reads the queue for an amendment of a division, so that a cancellation is
refused while one is waiting, running or stopped. It is read from the queue, never from memory
(architecture.md, "How a change is carried out").
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import (
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import (
    ROUND_CANCELLABLE,
    ROUND_RACED_AWAITING_VERDICTS,
    Round,
    RoundFormat,
    RoundLead,
    RoundStatus,
    read_scheduled_at,
    stored_scheduled_at,
    utc_text,
)
from leaguebot.core.services.rounds.amendment_rules_service import (
    AmendmentVerdict,
    judged_with_the_modules,
    status_refusal,
)
from leaguebot.core.services.rounds.round_moments import read_round_leads, results_left_to_advance
from leaguebot.core.services.rounds.round_steps import (
    JUDGE,
    judge_kept,
    named_now,
    round_number_now as number_now,
)
from leaguebot.core.services.cancellations import cancellation_notice_service as notices
from leaguebot.core.services.cancellations.cancellation_changes import ROUND_REARM, cancellation_holding_amendment
from leaguebot.framework.services.queue.audit_service import record_change_on
from leaguebot.framework.services.queue.queue_records import (
    in_hand,
)
from leaguebot.framework.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.seasons.season_service import renumber_rounds_on, round_called
from leaguebot.core.utils.formatting import format_round_list
from leaguebot.framework.utils.hooks import AwaitedHook, Hook
from leaguebot.framework.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.core.services.rounds.round_jobs import RoundJobs
    from leaguebot.core.services.seasons.season_service import SeasonService

__all__ = [
    "ROUND_AMEND",
    "amendment_in_hand",
    "payload_changes",
    "round_amend_change",
    "round_rearm_change",
]

ROUND_AMEND = "season.round.amend"

#: The core job names, as the stop notice, the tests and `StepView` know them. The jobs each module
#: defines are named by that module, which words them too (`amendment_step_failure`).
UNARM = "unarm"
APPLY = "apply"
ARM = "arm"
CLOSE = "close"

AMENDED = "✅ Round amended successfully."
NOTHING_AMENDED = (
    "Nothing was amended: round {number} in **{division}** stands as it was. Run "
    "`/round amend` again."
)
SAVE_DISCARDED = (
    "Nothing was amended: round {number} in **{division}** stands as it was, its timed work "
    "armed again. Run `/round amend` again."
)

#: How a field of a round is named in the success line, where it differs from its column.
_PARAMETER_OF = {"track_name": "track"}


def _as_logged(field: str, value: object) -> str:
    """*value* of *field* as the success line shows it: a moment in UTC without its offset, a
    format by its label (`RoundFormat.label`: "Sprint", never ``SPRINT``), any other value as it
    stands."""
    if value is None:
        return "none"
    if field == "scheduled_at":
        return utc_text(read_scheduled_at(str(value)))
    if field == "format":
        return RoundFormat(str(value)).label
    return str(value)

_EMPTY = StepView(name="", payload={}, result=None, done=False)

ROUND_GONE = "⛔ That round no longer exists. **Nothing has been changed.**"
_NOTHING_CHANGED = "**Nothing has been changed.** Run `/round amend` again to start over."


def no_longer_amendable(reasons: Iterable[str]) -> str:
    """The refusal of an amendment the rules no longer allow, in the words Confirm uses."""
    return (
        "⛔ This round can no longer be amended:\n"
        + "\n".join(f"• {reason}" for reason in reasons)
        + f"\n\n{_NOTHING_CHANGED}"
    )


def payload_changes(amendments: Iterable[tuple[str, object]]) -> list[list[str]]:
    """The amendments a member confirmed, as the change's payload holds them: ``[field, value]``,
    the moment as its stored text and the format as its value."""
    changes: list[list[str]] = []
    for field, value in amendments:
        if isinstance(value, datetime):
            text = stored_scheduled_at(value)
        elif isinstance(value, RoundFormat):
            text = value.value
        else:
            text = str(value)
        changes.append([field, text])
    return changes


def amended_values(changes: Iterable[Sequence[str]]) -> dict[str, Any]:
    """The payload's changes as the rules judge them: the moment a datetime, the format a
    `RoundFormat`."""
    values: dict[str, Any] = {}
    for field, text in changes:
        if field == "scheduled_at":
            values[field] = datetime.fromisoformat(text)
        elif field == "format":
            values[field] = RoundFormat(text)
        else:
            values[field] = text
    return values


async def amendment_in_hand(db_path: str, division_id: int) -> int | None:
    """The first job of an amendment of a round of *division_id* that is queued, running or
    stopped on a failure, 0 where only its close is left, or None where none is in hand."""
    for payload, job in await in_hand(db_path, (ROUND_AMEND,)):
        if payload.get("division_id") == division_id:
            return job or 0
    return None


def round_amend_change(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    round_jobs: "RoundJobs",
    amendment_steps: Hook,
    amendment_step_failure: Hook,
    amendment_judged: AwaitedHook,
    round_amended: AwaitedHook,
    round_armed: AwaitedHook,
    round_clean_ups_due: AwaitedHook,
    round_leads_read: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that amends a round; see the module.

    The payload is ``{"round_id", "round_number", "division_id", "division_name", "changes"}``,
    *changes* being ``[[field, value]]`` (`payload_changes`). It names no season: a payload that
    does is read as holding every division of that season (`review_changes.division_job_in_hand`).
    The builder hands in the *modules* (each post's `still_due`), the *seasons* service, the
    *round_jobs*, *amendment_judged* (the hook each module's part of the judgement is asked
    through, each reading its own windows), *round_amended* (the moment inside the save), *round_armed* and
    *round_clean_ups_due* (the moments the round is armed again, at which the modules plan their
    posts and their clean-ups), *round_leads_read* (the timers the modules want for a round, of
    which a clean-ups-only re-arm arms the clean-ups) and the queue's clock *now*.
    """
    return _round_change(
        rearm=False, modules=modules, seasons=seasons, round_jobs=round_jobs,
        amendment_steps=amendment_steps, amendment_step_failure=amendment_step_failure,
        amendment_judged=amendment_judged, round_amended=round_amended,
        round_armed=round_armed, round_clean_ups_due=round_clean_ups_due,
        round_leads_read=round_leads_read, now=now,
    )


def round_rearm_change(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    round_jobs: "RoundJobs",
    amendment_steps: Hook,
    amendment_step_failure: Hook,
    amendment_judged: AwaitedHook,
    round_amended: AwaitedHook,
    round_armed: AwaitedHook,
    round_clean_ups_due: AwaitedHook,
    round_leads_read: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The change that arms a round's timed work again after a refused cancellation; see the module.

    The payload is ``{"round_id", "round_number", "division_id", "division_name", "command"}``,
    *command* naming the cancellation that was refused, and ``"cleanups_only": True`` for a round
    already raced or ended cancelled as not held, whose next-day clean-ups alone are armed. It opens with the amendment's own
    judging and arming, unchanged but for when they are due and that neither can be discarded.
    The bot asks it, on the league's lane, one for each round.
    """
    return _round_change(
        rearm=True, modules=modules, seasons=seasons, round_jobs=round_jobs,
        amendment_steps=amendment_steps, amendment_step_failure=amendment_step_failure,
        amendment_judged=amendment_judged, round_amended=round_amended,
        round_armed=round_armed, round_clean_ups_due=round_clean_ups_due,
        round_leads_read=round_leads_read, now=now,
    )


def _round_change(
    *,
    rearm: bool,
    modules: "ModuleService",
    seasons: "SeasonService",
    round_jobs: "RoundJobs",
    amendment_steps: Hook,
    amendment_step_failure: Hook,
    amendment_judged: AwaitedHook,
    round_amended: AwaitedHook,
    round_armed: AwaitedHook,
    round_clean_ups_due: AwaitedHook,
    round_leads_read: AwaitedHook,
    now: Callable[[], datetime],
) -> ChangeType:
    """The steps of `round_amend_change`, and with *rearm* the change that arms a round again.

    Only the check, the due-ness of the judging and the arming, their refusal to be discarded, the
    change's kind, key and words differ between the two; every other step is the amendment's.
    """

    async def rearm_check(ctx: CheckContext) -> Verdict:
        """A re-arming is due while the round can still be cancelled, or, for its clean-ups alone,
        while it is raced or ended cancelled as not held; a round gone or past that is dropped."""
        if await rearm_due(ctx):
            return Verdict.go()
        return Verdict.not_due("the round can no longer be armed again")

    async def rearm_due(ctx: StepContext | CheckContext) -> bool:
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            return False
        if not ctx.payload.get("cleanups_only"):
            return rnd.status in ROUND_CANCELLABLE
        if rnd.status in ROUND_RACED_AWAITING_VERDICTS or rnd.status == RoundStatus.FINAL.value:
            return True
        if rnd.status != RoundStatus.CANCELLED.value:
            return False
        # A round that ended cancelled for every session being entered as not held keeps its
        # clean-ups, a day after it; a round called off has none.
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT not_held FROM rounds WHERE id = ?", (rnd.id,)
            )
            row = await cursor.fetchone()
        return row is not None and bool(row["not_held"])

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        rnd = await seasons.get_round(int(payload["round_id"]))
        if rnd is None:
            return Verdict.refuse(ROUND_GONE)
        verdict = await judged_with_the_modules(
            amendment_judged, rnd, amended_values(payload["changes"]), now=now(), db=None,
            held=await seasons.get_division_rounds(rnd.division_id),
        )
        if not verdict.allowed:
            return Verdict.refuse(
                no_longer_amendable(verdict.refusals),
                reason="it can no longer be amended:\n" + "\n".join(verdict.refusals),
            )
        if ctx.change_id is None:
            held = await cancellation_holding_amendment(ctx.db_path, rnd)
            if held is not None:
                return Verdict.refuse(held)
        return Verdict.go()

    # ── Reading the change as its jobs have left it ─────────────────────────────

    def view(ctx: OutcomeContext, name: str) -> StepView:
        return next((each for each in reversed(ctx.steps) if each.name == name), _EMPTY)

    def discarded(each: StepView) -> bool:
        return "discarded" in (each.result or {})

    def judged(ctx: OutcomeContext) -> dict[str, Any] | None:
        """What `judge` kept, or None where it was discarded or has not run."""
        result = view(ctx, JUDGE).result
        return result if result and result.get("judged") else None

    def unarmed(ctx: OutcomeContext) -> bool:
        result = view(ctx, UNARM).result
        return bool(result and result.get("unarmed"))

    def saved(ctx: OutcomeContext) -> bool:
        result = view(ctx, APPLY).result
        return bool(result and result.get("saved"))

    async def round_number_now(ctx: OutcomeContext) -> Any:
        """The number the round bears now, the press's where it cannot be read."""
        return await number_now(seasons, ctx)

    def named(text: str) -> Callable[[StepContext], Awaitable[str]]:
        return named_now(seasons, text)

    # ── The jobs ────────────────────────────────────────────────────────────────

    async def judge(ctx: StepContext) -> StepResult:
        """Keep what each module keeps of its part of the judgement, which the later jobs judge by,
        the round being still there.

        It writes nothing and refuses nothing, and reads no module's windows itself: each module's
        part is asked outside any save and keeps its own windows in what it keeps (`AmendmentPart`),
        which is stored as it is given, opaquely, and handed back to the module by the jobs that
        follow. The amendment is judged when it is asked and again in the save, against what the
        save reads at the queue's clock (`judged_again`), so that its refusal is spoken once, at
        the save's own moment, however long a stop between them lasts. What the amendment withdraws
        and what becomes of the check-in call are decided by the save and the phases to draw at once
        by the arming."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        # A re-arming carries no change: each module is asked of the round as it stands.
        verdict = await judged_with_the_modules(
            amendment_judged, rnd, amended_values(ctx.payload.get("changes", ())),
            now=now(), db=None,
        )
        return StepResult(result={"judged": True, "kept": verdict.kept})

    async def judge_went_through(ctx: StepContext) -> bool:
        return judged(ctx) is not None

    async def unarm(ctx: StepContext) -> StepResult:
        round_jobs.cancel_round(int(ctx.payload["round_id"]))
        return StepResult(result={"unarmed": True})

    async def apply_due(ctx: StepContext) -> bool:
        """The save is due only where the judgement and the removal of the timed work went
        through, not where either was discarded."""
        return judged(ctx) is not None and unarmed(ctx)

    async def judged_again(
        db: aiosqlite.Connection,
        row: aiosqlite.Row,
        changes: Iterable[Sequence[str]],
    ) -> AmendmentVerdict:
        """*changes* judged once more, against the round as the save reads it, each module's part
        asked on the save's connection *db* (which sees what the save has written and not yet
        committed), each reading its own windows there, and at the queue's clock.

        `judge` reached its verdict when it ran, and a job after it can stop and be tried again
        hours later: the save would otherwise write an amendment whose window has since passed,
        or plan what follows it from windows that have since gone by."""
        rnd = Round(
            id=int(row["id"]),
            division_id=int(row["division_id"]),
            round_number=int(row["round_number"]),
            format=RoundFormat(row["format"]),
            track_name=row["track_name"],
            scheduled_at=read_scheduled_at(row["scheduled_at"]),
            status=row["status"],
        )
        return await judged_with_the_modules(
            amendment_judged, rnd, amended_values(changes), now=now(), db=db,
            held=await seasons.get_division_rounds_on(db, rnd.division_id),
        )

    async def apply(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save: the audit, the round's fields, what the amendment withdraws, the
        division's renumbering and the success line, reading nothing but the connection.

        It judges the amendment again before it writes (`judged_again`), and refuses, writing
        nothing, where the round's status or that judgement says it may no longer be made. What
        follows the save is planned from that judgement, made when the save runs, and kept with
        it for the arming to read: what each module's part of the save kept (`round_amended`: the
        forecasts it withdraws and those of them that were posted, whether the check-in was
        reopened and whether a call stood). Planned from `judge`'s instead,
        a save tried again after a window passed would withdraw a forecast that now stands, or
        leave a call to a schedule whose moment has gone."""
        if ctx.actor_id is None or ctx.actor_name is None:
            raise RuntimeError("amending a round is the act of a member, and none is recorded")
        round_id = int(ctx.payload["round_id"])
        cursor = await db.execute(
            "SELECT id, status, round_number, division_id, track_name, format, scheduled_at "
            "FROM rounds WHERE id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
        refusal = None
        verdict: AmendmentVerdict | None = None
        if row is None:
            refusal = ROUND_GONE
        elif row["status"] not in ROUND_CANCELLABLE:
            refusal = no_longer_amendable([status_refusal(row["status"])])
        else:
            verdict = await judged_again(db, row, ctx.payload["changes"])
            if not verdict.allowed:
                refusal = no_longer_amendable(verdict.refusals)
        if row is None or verdict is None or refusal is not None:
            # A backstop: the check passed, and something wrote the database after it.
            return StepResult(
                result={"refused": refusal},
                lines=(refusal_line(ctx.named, "`/round amend`", reply_reason(refusal or "")),),
            )

        division_id = int(row["division_id"])
        stamp = now()
        changed = ""
        for field, new in ctx.payload["changes"]:
            old = row[field]
            await record_change_on(
                db, actor_id=ctx.actor_id, actor_name=ctx.actor_name,
                change_type=f"round.{field}", old_value=old, new_value=new, now=stamp,
                division_id=division_id,
            )
            # Each field by a statement naming its column, so that nothing is spliced in.
            if field == "track_name":
                await db.execute("UPDATE rounds SET track_name = ? WHERE id = ?", (new, round_id))
            elif field == "format":
                await db.execute("UPDATE rounds SET format = ? WHERE id = ?", (new, round_id))
            elif field == "scheduled_at":
                await db.execute(
                    "UPDATE rounds SET scheduled_at = ? WHERE id = ?", (new, round_id)
                )
            else:
                raise ValueError(f"Field {field!r} is not amendable")
            if old != new:
                changed += (
                    f"\n  {_PARAMETER_OF.get(field, field)}: "
                    f"{_as_logged(field, old)} → {_as_logged(field, new)}"
                )
        # What each module's part of the judgement kept for the save: the phases weather withdraws
        # and whether attendance reopens the check-in.
        kept: dict[str, Any] = {}
        for part in await round_amended.call(db, round_id, **verdict.kept):
            kept.update(part or {})
        if any(field == "scheduled_at" for field, _ in ctx.payload["changes"]):
            await renumber_rounds_on(db, division_id)
        values = amended_values(ctx.payload["changes"])
        return StepResult(
            result={
                **verdict.kept,
                **kept,
                "saved": True,
                "round_number": row["round_number"],
                "track": values.get("track_name") or row["track_name"] or "Unknown",
            },
            lines=(
                f"{ctx.named} | /round amend | Success\n"
                f"  round {row['round_number']} (round_id: {round_id}){changed}",
            ),
        )

    async def arm_due(ctx: StepContext) -> bool:
        """The arming is due where the timed work was removed and the round may still be
        cancelled, whatever became of the save: a round cancelled, or whose results were entered,
        while the amendment waited or stood stopped is armed with nothing."""
        if not unarmed(ctx):
            return False
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        return rnd is not None and rnd.status in ROUND_CANCELLABLE

    async def arm(ctx: StepContext) -> StepResult:
        """Arm the round's timed work as the round stands now, at the windows `judge` read, and
        catch up what fell due while it stood removed, as a restart does.

        The amendment can stand stopped for hours, at the save or before it, and a discard or a
        refusal arms the round again at its old moment: a timer armed for a moment already past
        would only fire late. So, judged at the queue's clock: a round still to be run
        whose moment has passed has the lead marked to run at once, results' submission, run at
        once through the round jobs, and what each module owes is its own answer to the round
        armed again (`plan_posts`): the forecast phases due are drawn at once, and the call, where
        none stands and its check-in is not over, is posted where it has fallen due.

        Under test mode the results submission is not run at once: `/test-mode advance` runs a test
        season's rounds in their turn. Where results is on and weather off the lead marked as left
        to the advance is dropped (`results_left_to_advance`) and no `results` job is armed at all,
        as the season's approval arms none; otherwise the round's job is taken down again where its
        moment has passed, which a job armed with no lateness limit would run at once. Its call is
        still posted (`post_call`)."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT d.tier, s.season_number, "
                "EXISTS (SELECT 1 FROM server_configs c WHERE c.test_mode_active = 1) AS test_mode "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
                (rnd.id,),
            )
            found = await cursor.fetchone()
        if found is None:
            raise LookupError(f"division {rnd.division_id} is no longer there")
        season_number, tier = int(found["season_number"]), int(found["tier"])
        if rearm and ctx.payload.get("cleanups_only"):
            # A round raced, or ended cancelled as not held: only the clean-ups of a day after it
            # are owed, armed through the round jobs and, where the day has come, run as the
            # restart's catch-up runs them. The modules answer the leads they want and core
            # arms the clean-ups among them: a module that is off answers none, and core reads
            # no module's switch.
            planned = await plan_clean_ups(ctx, rnd, kept={})
            leads = await read_round_leads(round_leads_read)
            clean_ups = tuple(lead for lead in leads if lead.clean_up)
            round_jobs.arm_leads(rnd, clean_ups, season_number, tier, now())
            return StepResult(result={"armed": True, "round_number": rnd.round_number}, then=planned)
        planned, jobs = await plan_posts(ctx, rnd)
        leads = tuple(lead for each in jobs for lead in each)
        if results_left_to_advance(
            test_mode=bool(found["test_mode"]),
            results_on=await modules.is_enabled("results"),
            weather_on=await modules.is_enabled("weather"),
        ):
            # Under test mode with results on and weather off the round is left to
            # `/test-mode advance`, as the season's approval leaves its rounds.
            leads = tuple(lead for lead in leads if not lead.left_to_advance)
        round_jobs.arm_leads(rnd, leads, season_number, tier, now())
        if rnd.status == RoundStatus.NOT_RUN.value:
            at = rnd.scheduled_at
            for lead in leads:
                if not lead.run_at_once or at + lead.offset > now():
                    continue
                if not found["test_mode"]:
                    # After the arming above, whose job for this moment it replaces.
                    round_jobs.run_lead_now(rnd, lead, season_number, tier)
                else:
                    # Test mode: the arming above made a job for a moment already past, which the
                    # scheduler runs at once, so it comes down again and the advance opens the round.
                    round_jobs.cancel_round(rnd.id, only=frozenset({lead.kind}))
        return StepResult(result={"armed": True, "round_number": rnd.round_number}, then=planned)

    async def plan_clean_ups(
        ctx: StepContext, rnd: Round, *, kept: Mapping[str, Any]
    ) -> tuple[PlannedStep, ...]:
        """The clean-ups a day after the round, which the modules plan where that day has come:
        the forecast (whichever of Phases 1 to 3 still stands), then the standing check-in call.
        Read when the arming runs, as the arming reads the round."""
        async with get_connection(ctx.db_path) as db:
            handed = await round_clean_ups_due.call(db, rnd.id, rnd=rnd, kept=kept, now=now)
        return tuple(step for steps in handed for step in steps or ())

    async def plan_posts(
        ctx: StepContext, rnd: Round
    ) -> tuple[
        tuple[PlannedStep, ...], tuple[tuple[RoundLead, ...], ...]
    ]:
        """The posts the amendment owes and the timed work to arm, as each module answers the
        round armed again (`RoundArming`): its steps, which the queue runs in today's order (the
        check-in call taken down and posted again, its deadline run, each withdrawn forecast that
        was posted deleted, the notice that the forecasts no longer stand, each phase due drawn
        at once) and then the clean-ups due a day after the round (`plan_clean_ups`), and its
        jobs, which `arm` hands the round jobs. Planned once the round is armed, which is where the
        amendment stood before them, but read before the timed work is scheduled; what the save
        withdraws, from what each module's part of it kept (`apply`), and only where it was saved.

        What each module plans and arms, and how it reads its own tables, is its own."""
        # What `judge` kept, which holds each module's windows as it read them, and, where the
        # save was made, what the save kept over it: handed back to the modules as it was given.
        kept = {**judge_kept(ctx.steps), **((view(ctx, APPLY).result or {}) if saved(ctx) else {})}
        async with get_connection(ctx.db_path) as db:
            handed = await round_armed.call(db, rnd.id, rnd=rnd, kept=kept, now=now)
        answers = [each for each in handed if each is not None]
        planned = [step for each in answers for step in each.steps]
        jobs = tuple(each.jobs for each in answers if each.jobs is not None)
        return (*planned, *await plan_clean_ups(ctx, rnd, kept=kept)), jobs

    async def never_runs(ctx: StepContext) -> str:
        return (
            f"round {await round_number_now(ctx)} in **{ctx.payload['division_name']}** "
            "would never run"
        )

    async def would_be_left_unarmed(ctx: StepContext) -> str:
        """What dropping a re-arming's judging or arming would leave: the timed work removed for
        a cancellation that was refused, and nothing to run the round (or, for a round already
        raced, to take its call and forecasts down), so it is retried, never discarded."""
        called = await round_called(ctx.db_path, int(ctx.payload["round_id"]))
        if ctx.payload.get("cleanups_only"):
            return f"the call and forecasts of {called} would never be taken down"
        return f"{called} would be left with no timed work, never run"

    def armed_again(ctx: OutcomeContext) -> tuple[str, ...]:
        """The one line a re-arming writes to the log channel once its arming has run, with what
        a league admin discarded of its catch-up beneath it; none where it was dropped."""
        result = view(ctx, ARM).result
        if not result or not result.get("armed"):
            return ()
        called = f"round {result['round_number']} in **{ctx.payload['division_name']}**"
        command = str(ctx.payload.get("command", "/round cancel"))
        what = (
            "next-day clean-ups of {} are" if ctx.payload.get("cleanups_only")
            else "timed work of {} is"
        ).format(called)
        return (
            f"⏱️ The {what} armed again, `{command}` having been refused"
            + not_done(ctx),
        )

    async def close_due(ctx: StepContext) -> bool:
        """The close is due where the amendment was saved, and where a job before the save was
        discarded, to read the number the round bears then for the reply."""
        return saved(ctx) or any(
            discarded(view(ctx, name)) for name in (JUDGE, UNARM, APPLY)
        )

    async def close(ctx: StepContext) -> StepResult:
        """Read the division's rounds for the reply, and the number the round bears now: an
        amendment ahead of this one can have renumbered the division since Confirm."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        rounds = [] if rnd is None else await seasons.get_division_rounds(rnd.division_id)
        return StepResult(
            result={
                "round_list": format_round_list(rounds) if rounds and saved(ctx) else "",
                "round_number": ctx.payload["round_number"] if rnd is None else rnd.round_number,
            }
        )

    def outcome(ctx: OutcomeContext) -> str:
        number = (view(ctx, CLOSE).result or {}).get("round_number", ctx.payload["round_number"])
        words = {"number": number, "division": ctx.payload["division_name"]}
        if discarded(view(ctx, JUDGE)) or discarded(view(ctx, UNARM)):
            return NOTHING_AMENDED.format(**words)
        # The arming runs whatever became of the save, and what it catches up can be discarded
        # too: it is named beneath the refusal or the discarded save as beneath a success.
        refused = (view(ctx, APPLY).result or {}).get("refused")
        if refused:
            return str(refused) + not_done(ctx)
        if discarded(view(ctx, APPLY)):
            return SAVE_DISCARDED.format(**words) + not_done(ctx)
        listed = (view(ctx, CLOSE).result or {}).get("round_list")
        return AMENDED + (f"\n\n{listed}" if listed else "") + not_done(ctx)

    def not_done(ctx: OutcomeContext) -> str:
        """What the amendment could not do, one entry for each post a league admin discarded, in
        the order of the jobs, each with what puts it right."""
        division = str(ctx.payload["division_name"])
        failures: list[notices.NoticeFailure] = []
        for each in ctx.steps:
            if not discarded(each):
                continue
            # Each module words the jobs it defines: the first to know the job answers, `()`
            # where the job leaves nothing of its own to name.
            undone = next(
                (
                    answer for answer in amendment_step_failure.call(each, ctx.steps)
                    if answer is not None
                ),
                (),
            )
            if undone:
                failures.append(notices.NoticeFailure(division, *undone))
        if not failures:
            return ""
        return "\n⚠️ **Not done**\n" + "\n".join(f"  • {each.describe()}" for each in failures)

    steps: dict[str, Step] = {
        JUDGE: Step(
            JUDGE, StepKind.ACT, judge,
            describe=named("judging the amendment of round {number} in **{division}**"),
        ),
        UNARM: Step(
            UNARM, StepKind.ACT, unarm, still_due=judge_went_through,
            describe=named("removing the timed work of round {number} in **{division}**"),
        ),
        APPLY: Step(
            APPLY, StepKind.SAVE, apply, still_due=apply_due,
            describe=named("amending round {number} in **{division}**"),
        ),
        ARM: Step(
            ARM, StepKind.ACT, arm, still_due=arm_due, undiscardable=never_runs,
            describe=named("arming the timed work of round {number} in **{division}** again"),
        ),
        # What each module defines of the amendment, by name: it plans them as the round is armed.
        **{name: step for defined in amendment_steps.call() for name, step in defined.items()},
        CLOSE: Step(
            CLOSE, StepKind.ACT, close, still_due=close_due,
            describe=named("listing the rounds of **{division}** for the reply"),
        ),
    }
    if rearm:
        for gone in (UNARM, APPLY, CLOSE):
            del steps[gone]
        steps[JUDGE] = dataclasses.replace(
            steps[JUDGE], still_due=rearm_due, undiscardable=would_be_left_unarmed,
            describe=named("judging whether the timed work of round {number} in **{division}** can be armed again"),
        )
        steps[ARM] = dataclasses.replace(
            steps[ARM], still_due=rearm_due, undiscardable=would_be_left_unarmed,
        )
        return ChangeType(
            kind=ROUND_REARM,
            opening=(PlannedStep(JUDGE), PlannedStep(ARM)),
            steps=steps,
            check=rearm_check,
            key=lambda payload: f"{ROUND_REARM}:{payload['round_id']}",
            doing=lambda payload: (
                f"Arming the timed work of round {payload['round_number']} in "
                f"**{payload['division_name']}** again"
            ),
            outcome=lambda _ctx: "",
            finished_lines=armed_again,
        )
    return ChangeType(
        kind=ROUND_AMEND,
        opening=tuple(PlannedStep(name) for name in (JUDGE, UNARM, APPLY, ARM, CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: (
            f"{ROUND_AMEND}:{payload['round_id']}:{json.dumps(payload['changes'])}"
        ),
        doing=lambda payload: (
            f"Amending round {payload['round_number']} in **{payload['division_name']}**"
        ),
        outcome=outcome,
    )
