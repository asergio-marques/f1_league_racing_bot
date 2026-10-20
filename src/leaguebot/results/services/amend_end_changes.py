"""Ending an amendment on the change queue: `results.amend.cancel` (#439, 6b, C62) and
`results.amend.lapse` (C63, C64).

The **Cancel Amendment** button asks the queue for this change as the member who pressed it,
acknowledged privately as under way. It is the league's, whole (a command's jobs go on one lane):
the amendment is not one conversation's to end from within, since it writes the round, so its
channel's deletion stops the league's lane where Discord refuses it, and not the amendment's.

**The check** is judged at the press and again when the change comes up to run. A closed or gone
amendment is too late to cancel; an amendment whose stage one is being recorded, or a stage of
which is being approved, on the queue ahead of the cancel (`stage_in_hand`, stopped included) is left alone until that
is done; one asked after it is not ahead, and is found no longer due once the cancel has ended
the amendment (results specification :491, "Leave it while stuck").

**The jobs.** `names` resolves the standings names a full tie is settled by, outside any save;
`revert` is one save: the round put back from the amendment's snapshot with its standings
(`revert_abandoned_amendment_on`, a no-op where stage one never wrote), the drafts dropped, the
amendment's row closed (which ends the conversation, so a paste still waiting on its lane is found
no longer due) and the cancel written in the standard form; and `delete_channel` takes the channel
down. The row stays, closed, as it does for every amendment that ended: restart recovery and the next
amendment of the round find it so, and the channel's deletion does not forget it.

**`results.amend.lapse`** ends an amendment nobody answered or approved, as the bot's change, on
the league's lane, asked by `ask_lapse` for one of three causes: the five minutes a paste or a
choice of configuration has from when its prompt appeared (`TIMER`, whose callback is
`amend_lapse_timer`), the half-hour the stages have (`SWEEP`, `sweep_expired_amendments`) and a
restart that found the amendment open (`RESTART`, `_recover_orphaned_amend_channels`). Its jobs are
the cancel's, ending with `record_lapse`, which names who started the amendment in the line the lapse
leaves in the log channel; the cancel's save and the lapse's differ in the wording of that line (a
restart that found nothing to put back says it ended the amendment, not that it lapsed, for the row
cannot say whether the corrections were never entered or had been approved), and in a row that is
already closed, kept only because its channel could not be deleted, which a restart finishes by
deleting the channel and forgetting the row, saying nothing.

**The lapse is not due** where the amendment has ended (but a restart, finishing a closed row whose channel's deletion is not in hand on the amendment's
own lane, which retries it), where
a stage of it is in hand on the queue, stopped included, the recording of stage one and the putting up
of a stage's prompt among them (results specification :491: "Leave it while stuck"), where a timer
finds stage one already written (the half-hour sweep has the amendment then) or finds a later prompt
has replaced the one it was armed for, and where a paste or a choice typed in time waits on the
amendment's lane (Q18): "in time" is typed before the five minutes ran out, from when the prompt
appeared, however long its turn then waits (the turn itself finds a paste or a choice typed after
the five minutes no longer due, so the lapse has the amendment). Nor is it due where the same lapse
is already in hand: the check says so, for the queue's own repeat rule would take a lapse still
waiting for an earlier prompt for a repeat of the next prompt's, and the amendment would be left
without a timer. A request not due is dropped, as the check says.

**The manager is told.** The lapse asked by the timer updates the reply of the manager who opened the
amendment, which the queue keeps for it (`ChangeQueue.keep_reply`, armed by the opening's `arm_lapse`
job) as long as that reply can still be updated: an amendment of several sessions can outlast it, and
the log channel alone tells then.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    unfinished,
)
from leaguebot.core.utils.log_lines import abandoned_line, name_of_member
from leaguebot.results.services import result_submission_service as submissions
from leaguebot.results.services.amend_changes import COMMAND, LAPSE_KIND, WAIT, lapse_key
from leaguebot.results.services.amend_turn_change import (
    CLOSE_KIND,
    CONFIG_KIND,
    KIND as TURN_KIND,
)
from leaguebot.results.services.amend_views import CANCEL_KIND
from leaguebot.results.services.review_posting import (
    DELETE_CHANNEL,
    NAMES,
    display_names,
    posting_steps,
)

log = logging.getLogger(__name__)

KIND = CANCEL_KIND
REVERT = "revert"
RECORD_LAPSE = "record_lapse"
#: The job that takes down the channel of an amendment that had already ended and forgets its row.
FORGET_CHANNEL = "forget_channel"

#: What asked for a lapse: the amendment's five-minute timer, the half-hour sweep, a restart.
TIMER = "timer"
SWEEP = "sweep"
RESTART = "restart"

#: Why a cancel of an amendment that has ended is refused.
TOO_LATE = (
    "ℹ️ Too late to cancel — the amendment is already being committed, or is no longer open."
)
#: Why a cancel is refused while a stage of the amendment is on the queue.
STAGE_IN_HAND = (
    "⏳ A stage of this amendment is being approved, so it cannot be cancelled now. If its "
    "approval is stopped, press Retry or Discard on its notice in the log channel."
)

__all__ = [
    "FORGET_CHANNEL", "KIND", "LAPSE_KIND", "RECORD_LAPSE", "RESTART", "REVERT", "STAGE_IN_HAND",
    "SWEEP", "TIMER", "TOO_LATE", "amend_cancel_change", "amend_lapse_change", "amend_lapse_timer",
    "ask_lapse",
]


def _names_step() -> Step:
    """The shared `names` job, tolerant of a fault: the names a full tie is settled by, which the
    revert's standings are recomputed under, being the lesser fault to lose. Where they cannot be
    had the job completes with none, and the standings are put back ordered by user id rather than
    not put back at all (decided 2026-09-15: the round is back whatever the order of a tie)."""
    shared = posting_steps()[NAMES]

    async def run(ctx: StepContext) -> StepResult:
        try:
            return await shared.run(ctx)
        except Exception:  # noqa: BLE001 — a tie ordered by id is the lesser fault
            log.exception("could not resolve the standings names of division %s",
                          ctx.step_payload.get("division_id"))
            return StepResult(result={"names": None})

    return dataclasses.replace(shared, run=run)


def amend_cancel_change(*, now: Callable[[], datetime]) -> ChangeType:
    """The `results.amend.cancel` change type, for the builder to register.

    *now* is the queue's clock, read when the amendment's row is closed. The payload is
    ``amend_id``, ``round_id``, ``division_id`` and ``channel_id`` of the amendment.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT round_id, closed_at FROM round_amend_channels WHERE id = ?",
                    (int(ctx.payload["amend_id"]),),
                )
            ).fetchone()
        if row is None or row["closed_at"] is not None:
            return Verdict.refuse(TOO_LATE)
        if await submissions.stage_in_hand(
            ctx.db_path, int(row["round_id"]), before=ctx.change_id
        ):
            return Verdict.refuse(STAGE_IN_HAND)
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (
            PlannedStep(NAMES, {"division_id": int(payload["division_id"])}),
            PlannedStep(REVERT),
        )

    async def revert(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        amend_id, round_id = int(payload["amend_id"]), int(payload["round_id"])
        division_id, channel_id = int(payload["division_id"]), int(payload["channel_id"])
        row = await (
            await db.execute(
                "SELECT r.round_number, d.name AS division_name "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
        assert row is not None  # the check found the amendment standing
        reverted = await submissions.revert_abandoned_amendment_on(
            db, round_id, display_names(ctx, division_id)
        )
        await db.execute("DELETE FROM amend_drafts WHERE amend_id = ?", (amend_id,))
        await db.execute(
            "UPDATE round_amend_channels SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
            (now().isoformat(), amend_id),
        )
        what = f"`/{COMMAND}` of round {row['round_number']} ({row['division_name']})"
        detail = (
            submissions.ROUND_PUT_BACK if reverted
            else "Nothing was written. " + submissions.AMENDMENT_RE_RUN
        )
        return StepResult(
            result={"reverted": reverted},
            lines=(abandoned_line(ctx.named, what, lapsed=False, detail=detail),),
            then=(PlannedStep(DELETE_CHANNEL, {
                "channel_id": channel_id, "round_id": round_id,
                "what": "amendment channel", "reason": "Amendment cancelled",
            }),),
        )

    async def describe_revert(_ctx: StepContext) -> str:
        return "putting the amended round back"

    def outcome(ctx: OutcomeContext) -> str:
        lines: list[str] = []
        for view in ctx.steps:
            result = view.result or {}
            if view.name == REVERT:
                if "discarded" in result:
                    return (
                        "⚠️ The amendment was not cancelled, and nothing was put back: press "
                        "Cancel Amendment again."
                    )
                if view.done:
                    lines.append(
                        "↩️ Amendment cancelled. "
                        + (submissions.ROUND_PUT_BACK if result.get("reverted")
                           else "Nothing was written.")
                    )
            elif view.name == DELETE_CHANNEL and "discarded" in result:
                lines.append(
                    f"⚠️ The amendment channel <#{view.payload.get('channel_id')}> was not "
                    "deleted: delete it by hand."
                )
        return "\n".join(lines)

    return ChangeType(
        kind=KIND,
        opening=(),
        plan=plan,
        steps={
            NAMES: _names_step(),
            REVERT: Step(REVERT, StepKind.SAVE, revert, describe=describe_revert),
            DELETE_CHANNEL: dataclasses.replace(posting_steps()[DELETE_CHANNEL], record=None),
        },
        check=check,
        key=lambda payload: f"{KIND}:{payload['amend_id']}",
        doing=lambda _payload: "Cancelling an amendment of a round's results",
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# The lapse
# ---------------------------------------------------------------------------

#: What the manager who opened an amendment is told when its five minutes ran out.
EXPIRED = (
    "⏱️ Amendment expired — no answer came within 5 minutes, so it has been closed and nothing "
    "was written. Run `/results rounds amend` again to start over."
)

_AMENDMENT_FOR_LAPSE = (
    "SELECT rac.id, rac.round_id, rac.channel_id, rac.started_by, rac.session_types, "
    "rac.closed_at, rac.pre_amendment_state, rac.prompt_posted_at, r.division_id "
    "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id WHERE rac.id = ?"
)


async def ask_lapse(
    queue: ChangeQueue, db_path: str, amend_id: int, *, cause: str, armed_for: str | None = None
) -> int | None:
    """Ask the queue, as the bot, for the lapse of amendment *amend_id* because of *cause*
    (`TIMER`, `SWEEP` or `RESTART`); its id, or None where the amendment is gone or the lapse is
    not due or already asked. A timer's *armed_for* is the time its prompt appeared, as the timer
    was armed for it."""
    async with get_connection(db_path) as db:
        row = await (await db.execute(_AMENDMENT_FOR_LAPSE, (amend_id,))).fetchone()
    if row is None:
        return None
    payload: dict[str, Any] = {
        "amend_id": amend_id, "round_id": int(row["round_id"]),
        "division_id": int(row["division_id"]), "channel_id": int(row["channel_id"]),
        "started_by": row["started_by"], "cause": cause, "armed_for": armed_for,
    }
    return await queue.ask(
        LAPSE_KIND, payload, what=f"ending round {row['round_id']}'s amendment ({cause})",
        origin=ChangeOrigin.BOT,
    )


def amend_lapse_timer(
    db_path: str, queue_of: Callable[[], ChangeQueue]
) -> Callable[[int | str], Awaitable[None]]:
    """The callback the builder registers for the `amend_lapse` timer.

    It is handed ``<amendment>:<when the prompt appeared>``, reads the amendment's row again, and
    asks the lapse only where the amendment is still open and its latest prompt is the one the
    timer was armed for: a prompt shown since has armed a timer of its own. *queue_of* gives the
    queue when the timer fires, which a restart has rebuilt since it was armed.
    """

    async def fired(key: int | str) -> None:
        amend_id, _, armed_for = str(key).partition(":")
        async with get_connection(db_path) as db:
            row = await (await db.execute(_AMENDMENT_FOR_LAPSE, (int(amend_id),))).fetchone()
        if row is None or row["closed_at"] is not None or row["prompt_posted_at"] != armed_for:
            return
        await ask_lapse(queue_of(), db_path, int(amend_id), cause=TIMER, armed_for=armed_for)

    return fired


def _sessions_of(row: Any) -> list[str]:
    try:
        return list(json.loads(row["session_types"]))
    except (TypeError, ValueError):
        return []


def amend_lapse_change(*, now: Callable[[], datetime]) -> ChangeType:
    """The `results.amend.lapse` change type, for the builder to register.

    *now* is the queue's clock, read when the amendment's row is closed. The payload is
    ``amend_id``, ``round_id``, ``division_id``, ``channel_id``, ``started_by`` (the member who
    opened the amendment), ``cause`` and, for a timer, ``armed_for``.
    """

    async def paste_waits(ctx: CheckContext, row: Any) -> bool:
        """Whether a paste or a choice typed in time waits on the amendment's lane."""
        deadline = (
            None if row["prompt_posted_at"] is None
            else datetime.fromisoformat(row["prompt_posted_at"]) + WAIT
        )
        for kind in (TURN_KIND, CONFIG_KIND):
            for waiting in await unfinished(ctx.db_path, [kind]):
                if int(waiting["channel_id"]) != int(row["channel_id"]):
                    continue
                typed = waiting.get("typed_at")
                # A choice carries the time it was pressed; one with none (a press whose time
                # Discord did not give) was pressed on a prompt still standing.
                if typed is None or deadline is None or datetime.fromisoformat(typed) <= deadline:
                    return True
        return False

    async def _already_asked(ctx: CheckContext) -> bool:
        """Whether a lapse of this amendment for the same prompt (a timer's ``armed_for``; the
        sweep's and a restart's have none) is in hand ahead of this one. Not the queue's repeat
        rule, which would take a lapse still waiting for an earlier prompt for a repeat of the next
        prompt's and swallow it: the amendment would then be left with no timer."""
        payload = ctx.payload
        return any(
            int(each["amend_id"]) == int(payload["amend_id"])
            and each.get("armed_for") == payload.get("armed_for")
            for each in await unfinished(ctx.db_path, [LAPSE_KIND], before=ctx.change_id)
        )

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        async with get_connection(ctx.db_path) as db:
            row = await (await db.execute(_AMENDMENT_FOR_LAPSE, (int(payload["amend_id"]),))).fetchone()
        if row is None:
            return Verdict.not_due("the amendment is gone")
        if row["closed_at"] is not None:
            # Kept only because its channel could not be deleted: a restart finishes that, unless
            # the amendment's own lane still holds the deletion, which is that lane's to retry.
            if payload["cause"] == RESTART:
                if any(
                    int(each["channel_id"]) == int(row["channel_id"])
                    for each in await unfinished(ctx.db_path, [CLOSE_KIND])
                ):
                    return Verdict.not_due("its channel's deletion is in hand on its own lane")
                return Verdict.go()
            return Verdict.not_due("the amendment has ended")
        if await submissions.stage_in_hand(ctx.db_path, int(row["round_id"])):
            return Verdict.not_due("a stage of the amendment is in hand on the change queue")
        if await _already_asked(ctx):
            return Verdict.not_due("the same lapse is already in hand")
        if payload["cause"] == TIMER:
            if row["pre_amendment_state"] is not None:
                return Verdict.not_due("stage one is recorded, so the half-hour has the amendment")
            if row["prompt_posted_at"] != payload.get("armed_for"):
                return Verdict.not_due("a later prompt has replaced the one the timer was armed for")
        if await paste_waits(ctx, row):
            return Verdict.not_due("a paste typed in time waits on the amendment's lane")
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (
            PlannedStep(NAMES, {"division_id": int(payload["division_id"])}),
            PlannedStep(REVERT),
        )

    async def revert(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        amend_id, round_id = int(payload["amend_id"]), int(payload["round_id"])
        division_id, channel_id = int(payload["division_id"]), int(payload["channel_id"])
        cause = payload["cause"]
        row = await (
            await db.execute(
                "SELECT rac.closed_at, rac.session_types, r.round_number, "
                "d.name AS division_name, s.season_number "
                "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id "
                "JOIN divisions d ON d.id = r.division_id JOIN seasons s ON s.id = d.season_id "
                "WHERE rac.id = ?",
                (amend_id,),
            )
        ).fetchone()
        assert row is not None  # the check found the amendment standing
        delete = {
            "channel_id": channel_id, "round_id": round_id, "what": "amendment channel",
            "reason": "Amendment abandoned",
        }
        if row["closed_at"] is not None:
            return StepResult(
                result={"already_closed": True}, then=(PlannedStep(FORGET_CHANNEL, delete),)
            )
        if cause == SWEEP and await submissions._claim_amendment_on(db, round_id) is None:
            return StepResult(result={"held": True})
        reverted = await submissions.revert_abandoned_amendment_on(
            db, round_id, display_names(ctx, division_id)
        )
        await db.execute("DELETE FROM amend_drafts WHERE amend_id = ?", (amend_id,))
        await db.execute(
            "UPDATE round_amend_channels SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
            (now().isoformat(), amend_id),
        )
        what = f"`/{COMMAND}` of round {row['round_number']} ({row['division_name']})"
        return StepResult(
            result={
                "reverted": reverted, "what": what, "sessions": _sessions_of(row),
                "season_number": int(row["season_number"]),
            },
            then=(PlannedStep(RECORD_LAPSE), PlannedStep(DELETE_CHANNEL, delete)),
        )

    async def record_lapse(ctx: StepContext) -> StepResult:
        """Tell the log channel what became of the amendment: the round is back, and the
        league hears it whether or not the channel's deletion, the job after, goes through at
        once. A log line is posted by the router, which tries it again on its own queue."""
        reverted_view = next(view for view in ctx.steps if view.name == REVERT)
        result = reverted_view.result or {}
        cause, reverted = ctx.payload["cause"], bool(result.get("reverted"))
        what, sessions = str(result["what"]), list(result["sessions"])
        named = await name_of_member(ctx.bot, ctx.payload.get("started_by"))
        shown = ", ".join(str(each).replace("_", " ").title() for each in sessions)
        if cause == RESTART and not reverted:
            # Nothing to put back means one of two things, and the row cannot say which: the
            # corrected results were never entered, or the amendment had been approved and its
            # channels were being rebuilt when the bot stopped (#345).
            line = (
                f"ℹ️ {what}, started by {named}, was ended by a restart\n"
                f"  Sessions: {shown}\n"
                "  Amendment channel deleted; nothing needed putting back. If the "
                "amendment had not been approved, re-run /results rounds amend. If it had, "
                "its channels may be part-rebuilt: run /results rounds sync and "
                "/results standings sync."
            )
        elif cause == RESTART:
            line = abandoned_line(named, what, lapsed=True, detail=(
                f"Sessions: {shown}\n"
                "Amendment channel deleted, and the round put back as it was. Please "
                "re-run /results rounds amend."
            ))
        elif cause == SWEEP:
            line = abandoned_line(named, what, lapsed=True, detail=(
                f"season: {result['season_number']}, sessions: "
                f"{submissions._sessions_text(sessions)}\n"
                "The amendment's report and appeal stages were not approved in time, so the "
                f"round has been put back as it was. {submissions.AMENDMENT_RE_RUN}"
            ))
        else:
            line = abandoned_line(
                named, what, lapsed=True,
                detail=f"Nothing was written. {submissions.AMENDMENT_RE_RUN}",
            )
        await ctx.bot.output_router.post_log(line)
        return StepResult(result={"recorded": True})

    async def describe_record(_ctx: StepContext) -> str:
        return "recording in the log channel that the amendment ended"

    async def describe_revert(_ctx: StepContext) -> str:
        return "ending the amendment and putting the round back"

    def outcome(ctx: OutcomeContext) -> str:
        lines: list[str] = []
        if ctx.payload["cause"] == TIMER and any(
            view.name == REVERT and view.done and (view.result or {}).get("reverted") is not None
            for view in ctx.steps
        ):
            lines.append(EXPIRED)
        for view in ctx.steps:
            if view.name in (DELETE_CHANNEL, FORGET_CHANNEL) and "discarded" in (view.result or {}):
                lines.append(
                    f"⚠️ The amendment channel <#{view.payload.get('channel_id')}> was not "
                    "deleted: delete it by hand."
                )
        return "\n".join(lines)

    return ChangeType(
        kind=LAPSE_KIND,
        opening=(),
        plan=plan,
        steps={
            NAMES: _names_step(),
            REVERT: Step(REVERT, StepKind.SAVE, revert, describe=describe_revert),
            DELETE_CHANNEL: dataclasses.replace(posting_steps()[DELETE_CHANNEL], record=None),
            RECORD_LAPSE: Step(RECORD_LAPSE, StepKind.ACT, record_lapse, describe=describe_record),
            FORGET_CHANNEL: dataclasses.replace(
                posting_steps()[DELETE_CHANNEL], name=FORGET_CHANNEL
            ),
        },
        check=check,
        key=lambda payload: lapse_key(int(payload["amend_id"])),
        repeatable=True,
        doing=lambda _payload: "Ending an amendment nobody answered",
        outcome=outcome,
    )
