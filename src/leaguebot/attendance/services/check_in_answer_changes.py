"""A driver's check-in answer on the change queue (#439, slice 6b): a press of Accept, Tentative or
Decline asks `attendance.check_in.answer`, whose jobs save the answer and redraw the call when the
change's turn comes.

The press is judged at the moment it was made (Constitution XIII: the choice locks at the
threshold), never at the moment its turn comes: a driver who pressed in time has answered in time
though the queue reaches the press after the deadline. The change's check judges it, as the press
asks and again as the change starts, in the words the button always answered with; the answer's
save judges it once more with the write, since the answer held at that moment decides the lock of
a reserve who has accepted.

- `save_answer` (a save) writes the answer through `answer_rsvp_on`. A press that changes nothing,
  a write that took no row and a lock found at the save write their log line here, and the member's
  reply says what became of it.
- `redraw` (an edit) draws the call again from every answer, with a fresh `RsvpView`, and writes the
  answer's success line, naming a call it could not redraw because it is gone. A burst of answers
  to one call makes one redraw: one whose turn comes with a later answer to the same call in hand
  leaves the drawing to that one. A redraw Discord refuses stops the queue with the answer saved.

`/attendance test rsvp`'s form asks the same kind with every entry of a maintainer's paste
(`ask_check_in_answers`): its `save_answers` job writes each entry through `upsert_rsvp_status_on`
in one save, with no lock judged (a rehearsal sets any state), and the same `redraw` draws the call
once for the lot. The form judges what it can as it is submitted (the lines it cannot read, the
drivers it cannot find) and passes those over; the save passes over an entry it could not write.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp
import aiosqlite
import discord

from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_service import (
    AttendanceService,
    RsvpOutcome,
    rsvp_lock,
)
from leaguebot.attendance.services.check_in_changes import edit_check_in_message
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    in_hand,
)
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.services.driver_service import resolve_driver_profile_id
from leaguebot.core.services.season_lifecycle_service import uncommitted_seat_excluded
from leaguebot.core.utils.log_lines import refusal_line, refuse, reply_reason

log = logging.getLogger(__name__)

CHECK_IN_ANSWER = "attendance.check_in.answer"

SAVE_ANSWER = "save_answer"
SAVE_ANSWERS = "save_answers"
REDRAW = "redraw"

#: What the maintainer's paste is called in the log, in the success line.
BULK_COMMAND = "/attendance test rsvp"

#: An answer as a log line gives it.
STATUS_WORDS = {
    "ACCEPTED": "accepted",
    "TENTATIVE": "tentative",
    "DECLINED": "declined",
    "NO_RSVP": "no answer",
}

#: An answer as the driver is told it.
STATUS_LABELS = {
    "ACCEPTED": "✅ Accepted",
    "TENTATIVE": "❓ Tentative",
    "DECLINED": "❌ Declined",
    "NO_RSVP": "(no response)",
}

#: What a press the locks bar is told, by lock.
LOCK_REPLIES = {
    RsvpOutcome.LOCKED_AT_DEADLINE: "❌ The RSVP deadline has passed. Your response cannot be changed.",
    RsvpOutcome.LOCKED_ACCEPTED: (
        "❌ You have already accepted and the RSVP deadline has passed. "
        "Your response cannot be changed."
    ),
    RsvpOutcome.LOCKED_AT_START: "❌ The round has started. Your response cannot be changed.",
}

_CLOSED = (
    "❌ This check-in is no longer open — the round has been cancelled, or its call has been "
    "taken down. Your answer has not been recorded."
)
_NOT_REGISTERED = "❌ You are not registered as a driver in this server."
_ROUND_GONE = "❌ This round no longer exists."
_NOT_A_MEMBER = "❌ You are not a member of this division."
_BULK_STALE = (
    "❌ The check-in call this form was opened on is no longer the division's open call. "
    "Nothing was set. Run `/attendance test rsvp` again."
)
_NOT_RECORDED = (
    "❌ Your answer could not be recorded. Please tell a league manager, and do not assume you "
    "are signed up for this round."
)


@dataclass(frozen=True)
class _Facts:
    """What the locks are judged on, as the round stands now."""

    found: str
    scheduled_at: datetime
    deadline_hours: int
    is_reserve: bool


def _aware(moment: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(moment) if isinstance(moment, str) else moment
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _facts(db: aiosqlite.Connection, payload: dict[str, Any]) -> _Facts | str:
    """The facts the press is judged on, or the refusal the press is answered with where the round
    or the driver's place in it is gone. Read on *db*."""
    round_id, division_id = int(payload["round_id"]), int(payload["division_id"])
    driver = int(payload["driver_profile_id"])
    cursor = await db.execute(
        "SELECT r.scheduled_at, r.status, ac.rsvp_deadline_hours FROM rounds r "
        "CROSS JOIN attendance_config ac WHERE r.id = ?",
        (round_id,),
    )
    round_row = await cursor.fetchone()
    if round_row is None:
        return _ROUND_GONE
    # A cancelled round's call is taken down with the cancellation (#175), so a press arriving
    # here raced it, or landed on a call the bot could not delete. The withdrawal is read as well
    # as the round's status, because the two are written apart: a call that no longer stands
    # answers nobody, whatever the round says.
    cursor = await db.execute(
        "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ? AND division_id = ?",
        (round_id, division_id),
    )
    if round_row["status"] == RoundStatus.CANCELLED.value or await cursor.fetchone() is None:
        return _CLOSED
    # Only a driver with a confirmed seat in this division may answer its call: one refusal for
    # every way of not being a driver of it (an unconfirmed placement, a seat elsewhere, none).
    cursor = await db.execute(
        f"""
        SELECT ti.is_reserve
          FROM driver_season_assignments dsa
          JOIN team_seats ts ON ts.driver_profile_id = dsa.driver_profile_id
          JOIN team_instances ti ON ti.id = ts.team_instance_id
                                AND ti.division_id = dsa.division_id
         WHERE dsa.driver_profile_id = ? AND dsa.division_id = ?
           AND {uncommitted_seat_excluded("ts")}
        """,  # noqa: S608
        (driver, division_id),
    )
    seat = await cursor.fetchone()
    if seat is None:
        return _NOT_A_MEMBER
    cursor = await db.execute(
        "SELECT rsvp_status FROM driver_round_attendance "
        "WHERE round_id = ? AND division_id = ? AND driver_profile_id = ?",
        (round_id, division_id, driver),
    )
    held = await cursor.fetchone()
    return _Facts(
        found=held["rsvp_status"] if held is not None else "NO_RSVP",
        scheduled_at=_aware(round_row["scheduled_at"]),
        deadline_hours=round_row["rsvp_deadline_hours"] or 0,
        is_reserve=bool(seat["is_reserve"]),
    )


def _is_bulk(payload: dict[str, Any]) -> bool:
    """Whether the change is a maintainer's paste of many answers, not one driver's press."""
    return "entries" in payload


def _doing(payload: dict[str, Any]) -> str:
    if _is_bulk(payload):
        return (
            f"setting {len(payload['entries'])} test RSVP answer(s) on the check-in call of "
            f"**{payload.get('division_name', '?')}**"
        )
    return (
        f"saving your answer to the check-in call of round {payload.get('round_number', '?')} "
        f"of **{payload.get('division_name', '?')}**"
    )


def _saved(ctx: OutcomeContext) -> dict[str, Any] | None:
    for step in ctx.steps:
        if step.name in (SAVE_ANSWER, SAVE_ANSWERS):
            return step.result
    return None


def _bulk_success_line(ctx: OutcomeContext, *, not_redrawn: bool) -> str:
    saved = _saved(ctx) or {}
    applied, errors = saved.get("applied", []), saved.get("errors", [])
    details = [
        f"division: {ctx.payload['division_name']}",
        f"round_id: {ctx.payload['round_id']}",
        f"updates: {len(applied)}",
        f"changes: {', '.join(applied)}",
    ]
    if errors:
        details.append("passed over: " + "; ".join(errors))
    if not_redrawn:
        details.append("call not redrawn")
    return f"{ctx.named} | {ctx.what} | Success" + "".join(f"\n  {detail}" for detail in details)


def _success_line(ctx: OutcomeContext, *, not_redrawn: bool) -> str:
    if _is_bulk(ctx.payload):
        return _bulk_success_line(ctx, not_redrawn=not_redrawn)
    saved = _saved(ctx) or {}
    status = ctx.payload["status"]
    was = STATUS_WORDS.get(saved.get("found", "NO_RSVP"), saved.get("found", "NO_RSVP"))
    line = f"{ctx.named} | {ctx.what} | Success\n  answer: {STATUS_WORDS[status]} (was: {was})"
    return line + "\n  call not redrawn" if not_redrawn else line


def check_in_answer_steps(service: AttendanceService) -> dict[str, Step]:
    """The jobs of a check-in answer, *service* being attendance's."""

    async def save_answer(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        status = payload["status"]
        facts = await _facts(db, payload)
        if isinstance(facts, str):
            return StepResult(
                result={"outcome": "REFUSED", "reply": facts},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(facts)),),
            )
        answer = await service.answer_rsvp_on(
            db, int(payload["round_id"]), int(payload["division_id"]),
            int(payload["driver_profile_id"]), status,
            now=_aware(payload["pressed_at"]), scheduled_at=facts.scheduled_at,
            deadline_hours=facts.deadline_hours, is_reserve=facts.is_reserve,
        )
        result: dict[str, Any] = {"outcome": answer.outcome.name, "found": answer.found}
        if answer.outcome in LOCK_REPLIES:
            reply = LOCK_REPLIES[answer.outcome]
            return StepResult(
                result={**result, "reply": reply},
                lines=(refusal_line(ctx.named, ctx.what, reply_reason(reply)),),
            )
        if answer.outcome is RsvpOutcome.UNCHANGED:
            return StepResult(
                result=result,
                lines=(
                    f"{ctx.named} | {ctx.what} | Nothing changed\n"
                    f"  answer: {STATUS_WORDS[status]}",
                ),
            )
        if answer.outcome is RsvpOutcome.NOT_RECORDED:
            log.error(
                "check-in answer: recorded no answer for driver %s on round %s / division %s",
                payload["driver_profile_id"], payload["round_id"], payload["division_id"],
            )
            return StepResult(
                result=result,
                lines=(
                    f"{ctx.named} | {ctx.what} | Failed\n"
                    f"  answer: {STATUS_WORDS[status]}\n  not recorded",
                ),
            )
        return StepResult(result=result)

    async def save_answers(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        now = _aware(payload["pressed_at"])
        applied: list[str] = []
        errors: list[str] = list(payload["passed_over"])
        for entry in payload["entries"]:
            saved = await service.upsert_rsvp_status_on(
                db, int(payload["round_id"]), int(payload["division_id"]),
                int(entry["driver_profile_id"]), entry["status"], now=now,
            )
            if saved:
                applied.append(f"`{entry['shown']}` → {entry['status'].lower()}")
            else:
                errors.append(
                    f"Line {entry['line']}: the answer for `{entry['shown']}` could not be saved"
                )
        result: dict[str, Any] = {"applied": applied, "errors": errors}
        if applied:
            return StepResult(result=result)
        # Nothing was written: the paste is refused as a paste with nothing applied is, and the
        # call is not drawn again.
        return StepResult(
            result=result,
            lines=(refusal_line(ctx.named, payload["form"], "\n".join(errors)),),
        )

    async def describe_save(ctx: StepContext) -> str:
        return _doing(ctx.payload)

    async def redraw_due(ctx: StepContext) -> bool:
        saved = _saved(ctx)
        if saved is None:
            return False
        return saved.get("outcome") == RsvpOutcome.RECORDED.name or bool(saved.get("applied"))

    async def redraw(ctx: StepContext) -> StepResult:
        round_id, division_id = int(ctx.payload["round_id"]), int(ctx.payload["division_id"])
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT message_id, channel_id FROM rsvp_embed_messages "
                "WHERE round_id = ? AND division_id = ?",
                (round_id, division_id),
            )
            standing = await cursor.fetchone()
        gone = StepResult(
            result={"gone": True}, lines=(_success_line(ctx, not_redrawn=True),)
        )
        if standing is None:
            return gone
        # A later answer to this call in hand draws it again from every answer, this one's
        # included, so a burst of presses makes one redraw.
        for payload, _job in await in_hand(ctx.db_path, [CHECK_IN_ANSWER], excluding=ctx.change_id):
            if payload["round_id"] == round_id and payload["division_id"] == division_id:
                return StepResult(
                    result={"later": True}, lines=(_success_line(ctx, not_redrawn=False),)
                )
        channel = as_text_channel(ctx.bot.get_channel(int(standing["channel_id"])))
        if channel is None:
            return gone
        try:
            message = await channel.fetch_message(int(standing["message_id"]))
            embed = await rsvp_service._rebuild_embed_for_round(round_id, division_id, ctx.bot)
            await edit_check_in_message(
                message, embed=embed, view=rsvp_service.RsvpView(round_id=round_id)
            )
        except discord.NotFound:
            raise
        except (discord.HTTPException, aiohttp.ClientError, OSError) as exc:
            raise StepFailedOnDiscord(
                f"the check-in call could not be redrawn: {str(exc) or type(exc).__name__}"
            ) from exc
        return StepResult(
            result={"redrawn": True}, lines=(_success_line(ctx, not_redrawn=False),)
        )

    async def describe_redraw(ctx: StepContext) -> str:
        if _is_bulk(ctx.payload):
            return f"redrawing the check-in call of **{ctx.payload.get('division_name', '?')}**"
        return (
            f"redrawing the check-in call of round {ctx.payload.get('round_number', '?')} of "
            f"**{ctx.payload.get('division_name', '?')}**"
        )

    return {
        SAVE_ANSWER: Step(SAVE_ANSWER, StepKind.SAVE, save_answer, describe=describe_save),
        SAVE_ANSWERS: Step(SAVE_ANSWERS, StepKind.SAVE, save_answers, describe=describe_save),
        REDRAW: Step(
            REDRAW, StepKind.EDIT, redraw, still_due=redraw_due, describe=describe_redraw,
            gone_line=lambda ctx: _success_line(ctx, not_redrawn=True),
        ),
    }


def _bulk_outcome(ctx: OutcomeContext, saved: dict[str, Any]) -> str:
    """What the maintainer is told of a paste: the entries applied, those passed over, and the
    call drawn again on the queue."""
    applied, errors = saved.get("applied", []), saved.get("errors", [])
    lines: list[str] = []
    if applied:
        lines.append(
            f"✅ Applied {len(applied)} update(s) in **{ctx.payload['division_name']}**:\n"
            + "\n".join(f"  {entry}" for entry in applied)
        )
        redrawn = any(
            step.name == REDRAW and step.result and step.result.get("gone") is None
            for step in ctx.steps
        )
        lines.append(
            "🔄 The check-in call is redrawn on the change queue." if redrawn
            else "ℹ️ The check-in call could not be redrawn: it is no longer there."
        )
    if errors:
        lines.append("⚠️ Errors:\n" + "\n".join(f"  • {e}" for e in errors))
    return "\n".join(lines)


def check_in_answer_change(service: AttendanceService) -> ChangeType:
    """`attendance.check_in.answer`: a member's change that saves one driver's answer to a round's
    check-in call and redraws the call. Repeatable: a driver may answer again."""

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        if _is_bulk(payload):
            # The form judged test mode and the module as it was submitted; the call is judged
            # again here, since the paste was written against that call.
            async with get_connection(ctx.db_path) as db:
                cursor = await db.execute(
                    "SELECT message_id FROM rsvp_embed_messages "
                    "WHERE round_id = ? AND division_id = ?",
                    (int(payload["round_id"]), int(payload["division_id"])),
                )
                standing = await cursor.fetchone()
            if standing is None or int(standing["message_id"]) != int(payload["embed_message_id"]):
                return Verdict.refuse(_BULK_STALE)
            return Verdict.go()
        async with get_connection(ctx.db_path) as db:
            facts = await _facts(db, payload)
        if isinstance(facts, str):
            return Verdict.refuse(facts)
        locked = rsvp_lock(
            facts.found, is_reserve=facts.is_reserve, now=_aware(payload["pressed_at"]),
            scheduled_at=facts.scheduled_at, deadline_hours=facts.deadline_hours,
        )
        return Verdict.go() if locked is None else Verdict.refuse(LOCK_REPLIES[locked])

    def outcome(ctx: OutcomeContext) -> str:
        saved = _saved(ctx)
        if saved is None:
            if _is_bulk(ctx.payload):
                return "⛔ Nothing was set: the paste was no longer owed when it came to run."
            return "⛔ Your answer was not recorded: it was no longer owed when it came to run."
        if _is_bulk(ctx.payload):
            return _bulk_outcome(ctx, saved)
        if "reply" in saved:
            return str(saved["reply"])
        label = STATUS_LABELS.get(ctx.payload["status"], ctx.payload["status"])
        if saved["outcome"] == RsvpOutcome.UNCHANGED.name:
            return f"ℹ️ You are already marked as **{label}**."
        if saved["outcome"] == RsvpOutcome.NOT_RECORDED.name:
            return _NOT_RECORDED
        return f"✅ Your RSVP has been updated to **{label}**."

    return ChangeType(
        kind=CHECK_IN_ANSWER,
        opening=(PlannedStep(SAVE_ANSWER), PlannedStep(REDRAW)),
        steps=check_in_answer_steps(service),
        check=check,
        key=lambda payload: (
            f"{CHECK_IN_ANSWER}:{payload['round_id']}:"
            f"{'paste' if _is_bulk(payload) else payload['driver_profile_id']}"
        ),
        plan=lambda payload: (
            (PlannedStep(SAVE_ANSWERS), PlannedStep(REDRAW)) if _is_bulk(payload)
            else (PlannedStep(SAVE_ANSWER), PlannedStep(REDRAW))
        ),
        doing=_doing,
        outcome=outcome,
        repeatable=True,
    )


async def ask_check_in_answer(
    queue: ChangeQueue,
    db_path: str,
    interaction: discord.Interaction,
    *,
    round_id: int,
    status: str,
    button: str,
) -> None:
    """Ask the queue to save the answer *status* the press of *button* gives to *round_id*'s call.

    The driver and the round are read here, to name the press in the log as every refusal of it is
    named: "the “Accept” button of the check-in call for round 3 of Division 1". A driver with no
    profile and a round that is gone are refused at once; every other refusal is the change's
    check's, as the press asks and again as it comes to run. The press carries the moment it was
    made, which is the moment its answer is judged at.
    """
    async with get_connection(db_path) as db:
        profile_id = await resolve_driver_profile_id(interaction.user.id, db)
        cursor = await db.execute(
            "SELECT r.division_id, r.round_number, d.name AS division_name FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
            (round_id,),
        )
        round_row = await cursor.fetchone()
    if profile_id is None:
        await refuse(interaction, _NOT_REGISTERED, what=f"{button} of a check-in call")
        return
    if round_row is None:
        await refuse(interaction, _ROUND_GONE, what=f"{button} of a check-in call")
        return
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": round_row["division_id"],
        "driver_profile_id": profile_id, "status": status,
        "pressed_at": _aware(interaction.created_at).isoformat(),
        "round_number": round_row["round_number"], "division_name": round_row["division_name"],
    }
    what = (
        f"{button} of the check-in call for round {round_row['round_number']} "
        f"of {round_row['division_name']}"
    )
    await queue.ask(
        CHECK_IN_ANSWER, payload, interaction=interaction, what=what, origin=ChangeOrigin.MEMBER
    )


async def ask_check_in_answers(
    queue: ChangeQueue,
    interaction: discord.Interaction,
    *,
    form: str,
    division_name: str,
    division_id: int,
    round_id: int,
    embed_message_id: int,
    entries: list[dict[str, Any]],
    passed_over: list[str],
    pressed_at: datetime,
) -> None:
    """Ask the queue to set every one of *entries* on *round_id*'s call and redraw it once.

    Each entry is `{"line", "shown", "driver_profile_id", "status"}`: the line of the paste it was
    on, the id as typed, and the driver and answer it gives. *passed_over* are the lines the form
    could not use, worded as the reply lists them. *form* names the form for a refusal's line;
    the change is "/attendance test rsvp" in every other. *pressed_at* is the one moment every
    answer is saved at, as the form read it.
    """
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": division_id, "division_name": division_name,
        "embed_message_id": embed_message_id, "entries": entries, "passed_over": passed_over,
        "pressed_at": _aware(pressed_at).isoformat(), "form": form,
    }
    await queue.ask(
        CHECK_IN_ANSWER, payload, interaction=interaction, what=BULK_COMMAND,
        refusal_what=form, origin=ChangeOrigin.MEMBER,
    )
