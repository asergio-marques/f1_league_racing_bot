"""Opening a round's penalty review, carried out on the change queue (#439, slice 2).

`results.review.open` opens, or opens again, a round's report stage: the interim results and
standings go out, then the penalty review prompt. It used to run inside the paste or the button
that asked for it (`enter_penalty_state`), committing between its posts and keeping what it was
doing in memory; a stop part-way left a review no restart could tell from a first paste cut short.
It is now a list of jobs the queue saves and resumes:

1. **`names`**, which resolves the drivers' display names from Discord, so that the save after it
   awaits nothing but its connection.
2. **`open`**, one save: the round's standings snapshots, the review flag, the round's status
   (guarded to the states before the review, so a resubmission cannot drag a round that has
   reached appeals backwards), and, for a return, the resubmission's flag and prompt cleared. The
   flag goes up before anything is posted: a stop during posting has to look like a penalty-review
   orphan on the next restart, not a mid-submission one. It plans every job after it.
3. **The posting jobs** (`review_posting`), where the interim results are to be published.
4. **`post_review_prompt`**, after them ("Prompt waits"): a post Discord refuses stops the queue
   and is tried again, and the prompt is not posted until the results are up. Its `record` saves
   the prompt's message id, and `results_posted` where the results were published and none of
   their jobs was discarded.
5. For a restart, **`delete_message`** for the old prompt, once the new one stands; for a
   resubmission's return, **`take_down_cancel`** (an `EDIT`) taking the Cancel button off its
   announcement, and for a cancel, **`post_cancel_line`**.
6. **`close`**, one save, writing the log line: for a paste, that the review is open; for a
   cancel, the cancel's line, now after the prompt is back; for a lapse or a failure, the line of
   the resubmission that ended.

**The check refuses a member and drops the bot.** A member's request is refused where the round
cannot enter a review, its submission channel's row is closed or the channel is gone, or the
round's reports are already being approved. The bot's request (a lapse, a failure, a restart) is
no longer due in those cases and is dropped; only a channel missing or unusable stops the queue,
with the check's reason, since the manager can set it right and press Retry.

The payload is ``round_id``, ``label``, ``publish`` (whether the interim results go out),
``returning`` (``"cancelled"``, ``"lapsed"`` or ``"failed"`` for a resubmission's return),
``cancel_message_id`` and ``old_prompt_id``.
"""
from __future__ import annotations

from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import ROUND_CANCELLABLE, RoundStatus
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    unfinished,
)
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.services.season_service import set_round_status_on
from leaguebot.core.utils.log_lines import abandoned_line
from leaguebot.results.services import review_posting
from leaguebot.results.services.result_submission_service import (
    _build_penalty_review_state,
    held_by_amendment,
    send_cancelled_line,
    send_review_prompt,
    take_down_cancel_message,
)
from leaguebot.results.services.results_post_service import _delete_posting
from leaguebot.results.services.review_posting import (
    DELETE_MESSAGE,
    NAMES,
    POST_SESSION_RESULTS,
    POST_STANDINGS,
    _channel,
    _league_guild,
    display_names,
    plan_posts,
    posting_steps,
)
from leaguebot.results.services.standings_service import compute_and_persist_round_on

__all__ = ["KIND", "review_open_change"]

KIND = "results.review.open"
REPORTS_APPROVE = "results.reports.approve"

_OPEN = "open"
_POST_REVIEW_PROMPT = "post_review_prompt"
_TAKE_DOWN_CANCEL = "take_down_cancel"
_POST_CANCEL_LINE = "post_cancel_line"
_CLOSE = "close"

#: Where a round may stand for its review to open: before it, or in the report stage already.
_OPENABLE = ROUND_CANCELLABLE | {RoundStatus.AWAITING_REPORT_VERDICTS.value}


async def _round_and_channel(db_path: str, round_id: int) -> aiosqlite.Row | None:
    async with get_connection(db_path) as db:
        return await (
            await db.execute(
                "SELECT r.status, r.division_id, r.round_number, d.name AS division_name, "
                "       rsc.channel_id, rsc.closed "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                "LEFT JOIN round_submission_channels rsc ON rsc.round_id = r.id "
                "WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()


def review_open_change() -> ChangeType:
    """The change that opens a round's penalty review; see the module."""

    async def check(ctx: CheckContext) -> Verdict:
        member = ctx.origin is ChangeOrigin.MEMBER

        def no(reason: str) -> Verdict:
            # A member is refused; the bot's request has nothing left to do.
            return Verdict.refuse(f"⚠️ {reason}", reason) if member else Verdict.not_due(reason)

        round_id = int(ctx.payload["round_id"])
        row = await _round_and_channel(ctx.db_path, round_id)
        if row is None:
            return no("The round is no longer there.")
        if row["status"] not in _OPENABLE:
            return no(f"The round is {str(row['status']).replace('_', ' ').lower()}, so its "
                      f"penalty review cannot be opened.")
        if row["channel_id"] is None or row["closed"]:
            return no("The round's submission channel is closed.")
        approving = await unfinished(ctx.db_path, [REPORTS_APPROVE], excluding=ctx.change_id)
        if any(p.get("round_id") == round_id for p in approving):
            return no("The round's reports are being approved.")
        # Another round's amendment holds the division: opening this review would publish its
        # unapproved standings, and leave them published if it were cancelled or lapsed. The
        # amend command is refused while this change is in hand, so this is the paste's own
        # check, made again where the standings are posted.
        held = await held_by_amendment(
            ctx.db_path, round_id, int(row["division_id"]),
            then="The review opens once that ends.",
        )
        if held is not None:
            return no(held)
        guild = await _league_guild(ctx.bot)
        if as_text_channel(guild.get_channel(int(row["channel_id"]))) is None:
            reason = (
                f"The submission channel <#{row['channel_id']}> no longer exists or cannot be "
                f"posted in. Set it right, then retry."
            )
            return Verdict.refuse(f"⚠️ {reason}", reason)
        return Verdict.go()

    async def open_review(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id = int(payload["round_id"])
        row = await (
            await db.execute(
                "SELECT r.division_id, rsc.channel_id FROM rounds r "
                "JOIN round_submission_channels rsc ON rsc.round_id = r.id WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
        if row is None:
            raise LookupError(f"round {round_id} has no submission channel to open a review in")
        division_id = int(row["division_id"])
        publish = bool(payload.get("publish", True))
        label = payload.get("label") or "Provisional Results"
        if publish:
            await compute_and_persist_round_on(db, round_id, division_id, display_names(ctx))
        returning = payload.get("returning")
        await db.execute(
            "UPDATE round_submission_channels SET in_penalty_review = 1"
            + (", resubmitting = 0, resubmit_prompt_message_id = NULL" if returning else "")
            + " WHERE round_id = ?",
            (round_id,),
        )
        # The results are in, so the round now waits on report verdicts and can no longer be
        # cancelled: the drivers have reports and appeals to lodge. Written in the save that
        # sets the flag, so the round and its channel never contradict each other.
        await set_round_status_on(
            db, round_id, RoundStatus.AWAITING_REPORT_VERDICTS, only_from=ROUND_CANCELLABLE
        )

        channel_id = int(row["channel_id"])
        then: list[PlannedStep] = []
        if publish:
            then.extend(await plan_posts(db, round_id, label=label))
        then.append(PlannedStep(_POST_REVIEW_PROMPT))
        if payload.get("old_prompt_id") is not None:
            then.append(PlannedStep(DELETE_MESSAGE, {
                "channel_id": channel_id, "message_id": int(payload["old_prompt_id"]),
                "what": "penalty review prompt",
            }))
        if payload.get("cancel_message_id") is not None:
            then.append(PlannedStep(_TAKE_DOWN_CANCEL, {
                "channel_id": channel_id, "message_id": int(payload["cancel_message_id"]),
            }))
        if returning == "cancelled":
            then.append(PlannedStep(_POST_CANCEL_LINE, {"channel_id": channel_id}))
        then.append(PlannedStep(_CLOSE))
        return StepResult(result={"opened": True}, then=tuple(then))

    async def post_review_prompt(ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await _round_and_channel(ctx.db_path, round_id)
        if row is None or row["channel_id"] is None:
            raise LookupError(f"round {round_id} has no submission channel to post the prompt in")
        guild = await _league_guild(ctx.bot)
        channel = _channel(guild, int(row["channel_id"]), "submission")
        # A try that posted the prompt and failed to save its id left the message standing.
        kept = (ctx.kept or {}).get("message_id")
        if kept is not None:
            await _delete_posting(channel, int(kept), [int(kept)], label="penalty review prompt",
                                  failures=[])
        state = await _build_penalty_review_state(
            ctx.bot, round_id, int(row["division_id"]), channel.id
        )
        message = await send_review_prompt(ctx.bot, channel, state)
        return StepResult(result={"message_id": message.id, "channel_id": channel.id})

    async def record_prompt(
        db: aiosqlite.Connection, ctx: StepContext, result: StepResult
    ) -> None:
        """Save the prompt's id, so the next restart can replace it, and `results_posted` where
        the interim results went out and no job posting them was discarded."""
        discarded = any(
            "discarded" in (view.result or {})
            for view in ctx.steps
            if view.name in (POST_SESSION_RESULTS, POST_STANDINGS)
        )
        posted = bool(ctx.payload.get("publish", True)) and not discarded
        await db.execute(
            "UPDATE round_submission_channels SET prompt_message_id = ?, "
            "results_posted = CASE WHEN ? THEN 1 ELSE results_posted END WHERE round_id = ?",
            (result.result["message_id"], int(posted), int(ctx.payload["round_id"])),
        )

    async def take_down_cancel(ctx: StepContext) -> StepResult:
        guild = await _league_guild(ctx.bot)
        channel = as_text_channel(guild.get_channel(int(ctx.step_payload["channel_id"])))
        if channel is not None:
            await take_down_cancel_message(
                channel.get_partial_message(int(ctx.step_payload["message_id"]))
            )
        return StepResult()

    async def post_cancel_line(ctx: StepContext) -> StepResult:
        guild = await _league_guild(ctx.bot)
        channel = _channel(guild, int(ctx.step_payload["channel_id"]), "submission")
        await send_cancelled_line(channel)
        return StepResult()

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        row = await (
            await db.execute(
                "SELECT r.round_number, d.name AS division_name FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
                (int(payload["round_id"]),),
            )
        ).fetchone()
        number = "?" if row is None else row["round_number"]
        division = "" if row is None else f" ({row['division_name']})"
        returning = payload.get("returning")
        resubmission = f"the resubmission of round {number}{division}"
        lines: tuple[str, ...]
        if returning == "cancelled":
            lines = (abandoned_line(
                ctx.named, resubmission, lapsed=False,
                detail="The earlier results stand.\nPress 🔄 Resubmit Initial Results to start again.",
            ),)
        elif returning == "lapsed":
            lines = (abandoned_line(
                ctx.named, resubmission, lapsed=True,
                detail="The earlier results stand.\nPress 🔄 Resubmit Initial Results to start again.",
            ),)
        elif returning == "failed":
            lines = (
                f"❌ {resubmission} failed before the earlier results were replaced\n"
                "  The earlier results stand.\n"
                "  Press 🔄 Resubmit Initial Results to start again.",
            )
        elif ctx.actor_id is not None:
            lines = (f"{ctx.named} | Penalty review opened | Success\n"
                     f"  round {number}{division}",)
        else:
            lines = ()
        return StepResult(result={"closed": True}, lines=lines)

    async def describe_open(_ctx: StepContext) -> str:
        return "opening the penalty review"

    async def describe_prompt(ctx: StepContext) -> str:
        row = await _round_and_channel(ctx.db_path, int(ctx.payload["round_id"]))
        where = "" if row is None or row["channel_id"] is None else f" in <#{row['channel_id']}>"
        return f"posting the penalty review prompt{where}"

    async def describe_take_down(ctx: StepContext) -> str:
        return f"taking the Cancel button off the resubmission in <#{ctx.step_payload['channel_id']}>"

    async def describe_cancel_line(ctx: StepContext) -> str:
        return f"posting that the resubmission was cancelled in <#{ctx.step_payload['channel_id']}>"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording that the penalty review is open"

    def outcome(ctx: OutcomeContext) -> str:
        done = {view.name: view for view in ctx.steps}
        opened = done.get(_OPEN)
        if opened is None or "discarded" in (opened.result or {}):
            return "Nothing was changed. The penalty review is being opened again."
        reply = f"✅ The penalty review is open (round {ctx.payload['round_id']})."
        lines = review_posting.not_done(ctx)
        if (done.get(_POST_REVIEW_PROMPT) is not None
                and "discarded" in (done[_POST_REVIEW_PROMPT].result or {})):
            lines.append("⚠️ The penalty review prompt was not posted. It is being posted again.")
        return "\n".join([reply, *lines])

    steps: dict[str, Step] = {
        **{name: step for name, step in posting_steps().items()},
        _OPEN: Step(_OPEN, StepKind.SAVE, open_review, describe=describe_open),
        _POST_REVIEW_PROMPT: Step(
            _POST_REVIEW_PROMPT, StepKind.ACT, post_review_prompt,
            describe=describe_prompt, record=record_prompt,
        ),
        _TAKE_DOWN_CANCEL: Step(
            _TAKE_DOWN_CANCEL, StepKind.EDIT, take_down_cancel, describe=describe_take_down,
        ),
        _POST_CANCEL_LINE: Step(
            _POST_CANCEL_LINE, StepKind.ACT, post_cancel_line, describe=describe_cancel_line,
        ),
        _CLOSE: Step(_CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(NAMES), PlannedStep(_OPEN)),
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda _payload: "Opening the penalty review",
        outcome=outcome,
    )
