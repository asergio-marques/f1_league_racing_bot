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
   announcement, and for a cancel, **`post_cancel_line`**, no longer due where the prompt's post
   was discarded: the cancel's line follows a prompt that stands.
6. **`close`**, one save, **an opening job** so that it runs last whatever was discarded before
   it, writing the log line: for a paste, that the review is open, `| Incomplete` where a
   league admin discarded a posting job or the prompt's post, naming each table not posted with
   the commands that post it, or, where the prompt's post was discarded too, as posted again with
   the review, and the prompt as being posted again (`_not_done`, as the approvals do); for a
   cancel, the cancel's line, now after the prompt is back, and none where the prompt was discarded, the review asked for
   again writing it once its own prompt stands; for a lapse or a failure, the line of the
   resubmission that ended. **A Discard reopens the review** ("Discard reopens the review"):
   where `open` or the prompt's post was discarded, `close` asks, in its own save, as the bot,
   for `results.review.open` again, with the dead prompt's id as ``old_prompt_id``, so that a fresh
   prompt replaces it. Where the interim results already stand it asks with ``publish`` unset,
   as recovery does, and records them as posted in the same save. A discarded `open` wrote
   nothing, so the request it asks for carries the return (``returning`` and
   ``cancel_message_id``) on; so does a cancel whose prompt was discarded, its line still owed.

**The check refuses a member and drops the bot.** A member's request is refused where the round
cannot enter a review, its submission channel's row is closed or the channel is gone, or the
round's reports are already being approved. The bot's request (a lapse, a failure, a restart) is
no longer due in those cases and is dropped; only a channel missing or unusable stops the queue,
with the check's reason, since the manager can set it right and press Retry.

The payload is ``round_id``, ``label``, ``publish`` (whether the interim results go out),
``returning`` (``"cancelled"``, ``"lapsed"`` or ``"failed"`` for a resubmission's return),
``cancel_message_id`` and ``old_prompt_id``.

**Two more kinds put a review back after a restart, as the bot.** `results.appeals.open` posts a
round's appeals prompt again, replacing the old one once the new one stands; its `close` asks for
it again where a Discard dropped the post. `results.review.close_stale` closes a FINAL round whose
submission row a run from before this change left open: its channel is deleted and its row marked
closed with the deletion, and a line says so, in place of the dead review that used to be posted
again at every restart. Both are dropped, not stopped, where they are no longer due.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    FollowOn,
    GuildUnavailable,
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
from leaguebot.results.services import review_posting, review_verdicts
from leaguebot.results.services.result_submission_service import (
    _build_penalty_review_state,
    held_by_amendment,
    send_cancelled_line,
    send_review_prompt,
    take_down_cancel_message,
)
from leaguebot.results.services.results_post_service import _delete_posting
from leaguebot.results.services.review_posting import (
    DELETE_CHANNEL,
    DELETE_MESSAGE,
    NAMES,
    POST_SESSION_RESULTS,
    POST_STANDINGS,
    _channel,
    _league_guild,
    _record_channel_deleted,
    display_names,
    plan_posts,
    posting_steps,
)
from leaguebot.results.services.standings_service import compute_and_persist_round_on

__all__ = [
    "APPEALS_OPEN_KIND", "CLOSE_STALE_KIND", "KIND", "appeals_open_change", "close_stale_change",
    "review_open_change",
]

KIND = "results.review.open"
APPEALS_OPEN_KIND = "results.appeals.open"
CLOSE_STALE_KIND = "results.review.close_stale"
REPORTS_APPROVE = "results.reports.approve"
APPEALS_APPROVE = "results.appeals.approve"

_OPEN = "open"
_POST_REVIEW_PROMPT = "post_review_prompt"
_TAKE_DOWN_CANCEL = "take_down_cancel"
_POST_CANCEL_LINE = "post_cancel_line"
_CLOSE = "close"

#: Where a round may stand for its review to open: before it, or in the report stage already.
_OPENABLE = ROUND_CANCELLABLE | {RoundStatus.AWAITING_REPORT_VERDICTS.value}


def _naming(what: str, payload: dict[str, Any]) -> str:
    """*what*, naming the round and division where the payload carries them, as the approvals' do.

    `doing` reads the payload alone, so whoever asks for the change puts the round's number and
    the division's name in it where it has them, and a request without them reads generically.
    """
    if payload.get("round_number") is None:
        return what
    division = payload.get("division_name") or "its division"
    return f"{what} of round {payload['round_number']} ({division})"


async def _guild_or_none(ctx: CheckContext) -> discord.Guild | None:
    """The league's server, or None where it is not in the cache. A member's request is then
    refused and the bot's has nothing it can do: it is dropped, to be asked for again at the next
    restart, where a check that raised would stop the queue at the change, and a Discard that
    asks for the review again would ask for ever."""
    try:
        return await _league_guild(ctx.bot)
    except GuildUnavailable:
        return None


_NO_SERVER = "The league's server is not in the cache."


def _discarded(result: dict[str, Any] | None) -> bool:
    return "discarded" in (result or {})


def _dropped(result: dict[str, Any] | None) -> bool:
    return bool((result or {}).get("dropped"))


def _not_done(ctx: OutcomeContext) -> list[str]:
    """One line for each job of an opening a league admin discarded: the posting jobs'
    (`review_posting.not_done`), then the prompt's, worded as the approvals word a lost appeals
    prompt (`review_verdicts.not_done`). The reply and the log line both read it.

    Where the prompt's post was discarded, `close` asks for the review again, and that review
    posts every table afresh where one was discarded here, so the tables not posted are named as
    posted again with it rather than with the sync commands, which would post them twice."""
    prompt_lost = any(
        view.name == _POST_REVIEW_PROMPT and _discarded(view.result) for view in ctx.steps
    )
    lines = review_posting.not_done(ctx, reposted=prompt_lost)
    if prompt_lost:
        lines.append("⚠️ The penalty review prompt was not posted. It is being posted again.")
    return lines


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
        # check, made again where the standings are posted. A review put back without publishing
        # (a resubmission's cancel or failure, a restart's) computes and posts nothing, so the
        # amendment holds nothing it could expose: refusing it would strand the round.
        if bool(ctx.payload.get("publish", True)):
            held = await held_by_amendment(
                ctx.db_path, round_id, int(row["division_id"]),
                then="The review opens once that ends.",
            )
            if held is not None:
                return no(held)
        guild = await _guild_or_none(ctx)
        if guild is None:
            return no(_NO_SERVER)
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
        return StepResult(result={"opened": True}, then=tuple(then))

    async def names_not_discarded(ctx: StepContext) -> bool:
        """The save reads the display names only where the results are published, and is due
        only where they were resolved: a discarded `names` leaves nothing changed."""
        if not bool(ctx.payload.get("publish", True)):
            return True
        return not any(view.name == NAMES and _discarded(view.result) for view in ctx.steps)

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

    async def prompt_not_discarded(ctx: StepContext) -> bool:
        """The cancel's line follows the prompt: where a league admin discarded the prompt's post,
        the review asked for again carries the cancel on and posts it once its own prompt stands."""
        return not any(
            view.name == _POST_REVIEW_PROMPT and _discarded(view.result) for view in ctx.steps
        )

    async def post_cancel_line(ctx: StepContext) -> StepResult:
        guild = await _league_guild(ctx.bot)
        channel = _channel(guild, int(ctx.step_payload["channel_id"]), "submission")
        await send_cancelled_line(channel)
        return StepResult()

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id = int(payload["round_id"])
        row = await (
            await db.execute(
                "SELECT r.round_number, d.name AS division_name FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
        number = "?" if row is None else row["round_number"]
        division = "" if row is None else f" ({row['division_name']})"
        views = {view.name: view for view in ctx.steps}
        opened = views.get(_OPEN)
        open_lost = opened is None or _discarded(opened.result) or _dropped(opened.result)
        prompt = views.get(_POST_REVIEW_PROMPT)
        prompt_lost = prompt is not None and _discarded(prompt.result)

        returning = payload.get("returning")
        resubmission = f"the resubmission of round {number}{division}"
        lines: tuple[str, ...] = ()
        if open_lost:
            # Nothing was changed: the Discard's own line records it.
            pass
        elif returning == "cancelled" and prompt_lost:
            # The cancel is recorded once the prompt is back, by the review asked for again below.
            pass
        elif returning == "cancelled":
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
            # A table or the prompt a league admin discarded is named, with the commands that post
            # a table, as the approvals' lines name theirs: the review is open, but the league's
            # channels are not as a Success would say.
            left = _not_done(ctx)
            lines = (f"{ctx.named} | Penalty review opened | "
                     f"{'Incomplete' if left else 'Success'}\n"
                     f"  round {number}{division}"
                     + "".join(f"\n  {text}" for text in left),)
        if not (open_lost or prompt_lost):
            return StepResult(result={"closed": True}, lines=lines)

        # "Discard reopens the review": a fresh prompt replaces the dead one, and the interim
        # results are not posted twice where they already stand.
        if prompt_lost and bool(payload.get("publish", True)) and not any(
            _discarded(view.result) for view in ctx.steps
            if view.name in (POST_SESSION_RESULTS, POST_STANDINGS)
        ):
            await db.execute(
                "UPDATE round_submission_channels SET results_posted = 1 WHERE round_id = ?",
                (round_id,),
            )
        stored = await (
            await db.execute(
                "SELECT results_posted FROM round_submission_channels WHERE round_id = ?",
                (round_id,),
            )
        ).fetchone()
        again: dict[str, Any] = {
            "round_id": round_id,
            "label": payload.get("label") or "Provisional Results",
            "publish": not (stored is not None and stored["results_posted"]),
            "old_prompt_id": payload.get("old_prompt_id"),
            "round_number": payload.get("round_number"),
            "division_name": payload.get("division_name"),
        }
        # A discarded `open` wrote nothing, so the return is carried on; so is a cancel whose
        # prompt was discarded, its line still owed until a prompt stands.
        if open_lost or returning == "cancelled":
            for key in ("returning", "cancel_message_id"):
                if payload.get(key) is not None:
                    again[key] = payload[key]
        return StepResult(
            result={"closed": True, "reopened": True},
            lines=lines,
            follow_ons=(FollowOn(
                KIND, again,
                f"the penalty review of round {number}, opened again after a job of it was "
                "discarded",
            ),),
        )

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
        if opened is None or _discarded(opened.result) or _dropped(opened.result):
            return "Nothing was changed. The penalty review is being opened again."
        reply = f"✅ The penalty review is open (round {ctx.payload['round_id']})."
        return "\n".join([reply, *_not_done(ctx)])

    steps: dict[str, Step] = {
        **{name: step for name, step in posting_steps().items()},
        _OPEN: Step(
            _OPEN, StepKind.SAVE, open_review, still_due=names_not_discarded,
            describe=describe_open,
        ),
        _POST_REVIEW_PROMPT: Step(
            _POST_REVIEW_PROMPT, StepKind.ACT, post_review_prompt,
            describe=describe_prompt, record=record_prompt,
        ),
        _TAKE_DOWN_CANCEL: Step(
            _TAKE_DOWN_CANCEL, StepKind.EDIT, take_down_cancel, describe=describe_take_down,
        ),
        _POST_CANCEL_LINE: Step(
            _POST_CANCEL_LINE, StepKind.ACT, post_cancel_line, still_due=prompt_not_discarded,
            describe=describe_cancel_line,
        ),
        _CLOSE: Step(_CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(NAMES), PlannedStep(_OPEN), PlannedStep(_CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda payload: _naming("Opening the penalty review", payload),
        outcome=outcome,
    )


def _refusal(ctx: CheckContext, reason: str) -> Verdict:
    """A member's request is refused; the bot's request has nothing left to do."""
    if ctx.origin is ChangeOrigin.MEMBER:
        return Verdict.refuse(f"⚠️ {reason}", reason)
    return Verdict.not_due(reason)


def appeals_open_change() -> ChangeType:
    """The change that posts a round's appeals prompt again, as the bot, after a restart or a
    Discard; see the module. Its payload is ``round_id``, ``division_id`` and ``old_prompt_id``,
    the prompt it replaces once the new one stands."""

    async def check(ctx: CheckContext) -> Verdict:
        round_id = int(ctx.payload["round_id"])
        row = await _round_and_channel(ctx.db_path, round_id)
        if row is None:
            return _refusal(ctx, "The round is no longer there.")
        if row["status"] != RoundStatus.AWAITING_APPEAL_VERDICTS.value:
            return _refusal(ctx, "The round is no longer awaiting its appeal verdicts.")
        if row["channel_id"] is None or row["closed"]:
            return _refusal(ctx, "The round's submission channel is closed.")
        approving = await unfinished(ctx.db_path, [APPEALS_APPROVE], excluding=ctx.change_id)
        if any(p.get("round_id") == round_id for p in approving):
            return _refusal(ctx, "The round's appeals are being approved.")
        guild = await _guild_or_none(ctx)
        if guild is None:
            return _refusal(ctx, _NO_SERVER)
        if as_text_channel(guild.get_channel(int(row["channel_id"]))) is None:
            reason = (
                f"The submission channel <#{row['channel_id']}> no longer exists or cannot be "
                f"posted in. Set it right, then retry."
            )
            return Verdict.refuse(f"⚠️ {reason}", reason)
        return Verdict.go()

    async def plan(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Plan the prompt, then the old prompt's deletion: the new one stands first."""
        round_id = int(ctx.payload["round_id"])
        row = await (
            await db.execute(
                "SELECT r.division_id, rsc.channel_id FROM rounds r "
                "JOIN round_submission_channels rsc ON rsc.round_id = r.id WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
        if row is None:
            raise LookupError(f"round {round_id} has no submission channel to post a prompt in")
        then = [PlannedStep(
            review_verdicts.POST_APPEALS_PROMPT,
            {"round_id": round_id, "division_id": int(row["division_id"])},
        )]
        if ctx.payload.get("old_prompt_id") is not None:
            then.append(PlannedStep(DELETE_MESSAGE, {
                "channel_id": int(row["channel_id"]),
                "message_id": int(ctx.payload["old_prompt_id"]),
                "what": "appeals review prompt",
            }))
        return StepResult(result={"planned": True}, then=tuple(then))

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Where the prompt's post was discarded, ask for it again: the stage stays open with no
        prompt otherwise ("Discard reopens the review")."""
        if not any(
            view.name == review_verdicts.POST_APPEALS_PROMPT and _discarded(view.result)
            for view in ctx.steps
        ):
            return StepResult(result={"closed": True})
        again = {
            "round_id": int(ctx.payload["round_id"]),
            "division_id": int(ctx.payload["division_id"]),
            "old_prompt_id": None,
            "round_number": ctx.payload.get("round_number"),
            "division_name": ctx.payload.get("division_name"),
        }
        return StepResult(
            result={"closed": True, "reopened": True},
            follow_ons=(FollowOn(
                APPEALS_OPEN_KIND, again,
                "the appeals review prompt, posted again after its post was discarded",
            ),),
        )

    async def describe_plan(_ctx: StepContext) -> str:
        return "working out where the appeals review prompt goes"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording that the appeals review prompt is back"

    def outcome(ctx: OutcomeContext) -> str:
        left = review_posting.not_done(ctx)
        prompt = next(
            (v for v in ctx.steps if v.name == review_verdicts.POST_APPEALS_PROMPT), None
        )
        if prompt is not None and _discarded(prompt.result):
            left.append("⚠️ The appeals review prompt was not posted. It is being posted again.")
        return "\n".join(["✅ The appeals review prompt is posted again.", *left])

    steps: dict[str, Step] = {
        **posting_steps(),
        review_verdicts.POST_APPEALS_PROMPT: review_verdicts.appeals_prompt_step(),
        _OPEN: Step(_OPEN, StepKind.SAVE, plan, describe=describe_plan),
        _CLOSE: Step(_CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=APPEALS_OPEN_KIND,
        opening=(PlannedStep(_OPEN), PlannedStep(_CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{APPEALS_OPEN_KIND}:{payload['round_id']}",
        doing=lambda payload: _naming("Posting the appeals review prompt again", payload),
        outcome=outcome,
    )


def close_stale_change() -> ChangeType:
    """The change that closes a FINAL round's submission channel a run from before this change
    left open; see the module. Its payload is ``round_id``."""

    async def check(ctx: CheckContext) -> Verdict:
        row = await _round_and_channel(ctx.db_path, int(ctx.payload["round_id"]))
        if row is None:
            return _refusal(ctx, "The round is no longer there.")
        if row["status"] != RoundStatus.FINAL.value:
            return _refusal(ctx, "The round is not final, so its review is not stale.")
        if row["channel_id"] is None or row["closed"]:
            return _refusal(ctx, "The round's submission channel is already closed.")
        return Verdict.go()

    async def stale(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await (
            await db.execute(
                "SELECT channel_id FROM round_submission_channels WHERE round_id = ?",
                (round_id,),
            )
        ).fetchone()
        if row is None:
            raise LookupError(f"round {round_id} has no submission channel to close")
        return StepResult(
            result={"planned": True},
            then=(PlannedStep(DELETE_CHANNEL, {
                "channel_id": int(row["channel_id"]), "round_id": round_id,
                "what": "submission channel", "reason": "Stale penalty review closed",
            }),),
        )

    async def record_closed(
        db: aiosqlite.Connection, ctx: StepContext, result: StepResult
    ) -> None:
        """Mark the row closed with the deletion, so a channel that could not be deleted keeps
        its row for the retry."""
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?",
            (int(ctx.step_payload["round_id"]),),
        )
        await _record_channel_deleted(db, ctx, result)

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        row = await (
            await db.execute(
                "SELECT r.round_number, d.name AS division_name FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
                (int(ctx.payload["round_id"]),),
            )
        ).fetchone()
        where = "" if row is None else f"round {row['round_number']} ({row['division_name']})"
        return StepResult(
            result={"closed": True},
            lines=(
                f"{ctx.named} | Stale penalty review closed | Success\n"
                f"  {where}\n"
                "  The round is final, and its submission channel was left open by an older run: "
                "it was closed.",
            ),
        )

    async def describe_stale(_ctx: StepContext) -> str:
        return "closing a final round's stale submission channel"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording that the stale penalty review was closed"

    def outcome(ctx: OutcomeContext) -> str:
        return "\n".join(["✅ The stale penalty review is closed.", *review_posting.not_done(ctx)])

    steps: dict[str, Step] = {
        DELETE_CHANNEL: replace(posting_steps()[DELETE_CHANNEL], record=record_closed),
        _OPEN: Step(_OPEN, StepKind.SAVE, stale, describe=describe_stale),
        _CLOSE: Step(_CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=CLOSE_STALE_KIND,
        opening=(PlannedStep(_OPEN), PlannedStep(_CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{CLOSE_STALE_KIND}:{payload['round_id']}",
        doing=lambda payload: _naming("Closing a stale penalty review", payload),
        outcome=outcome,
    )
