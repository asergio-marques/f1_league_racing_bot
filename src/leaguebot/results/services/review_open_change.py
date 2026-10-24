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
   resubmission that ended. **It cannot be discarded**, nor can the prompt change's: dropping it
   could leave the round in its penalty review with no prompt, never final, until the bot restarts.
   **A Discard reopens the review** ("Discard reopens the review"):
   where `open` or the prompt's post was discarded, `close` asks, in its own save, as the bot,
   for `results.review.open` again, with the dead prompt's id as ``old_prompt_id``, so that a fresh
   prompt replaces it. Where the interim results already stand it asks with ``publish`` unset,
   as recovery does, and records them as posted in the same save. A discarded `open` wrote
   nothing, so the request it asks for carries the return (``returning`` and
   ``cancel_message_id``) on; so does a cancel whose prompt was discarded, its line still owed.

**Where a conversation handed the review over, the round's lane puts the prompt up.** A request made
for a member (a last paste, a resubmission's swap or Cancel) opens the review on the league lane
without the jobs 4 and 5 above: its `close` asks `results.review.prompt` (`review_prompt_change`),
which runs on the round's lane (`channel:<id>`) and carries out jobs 4 and 5 and the closing line
itself, so a prompt Discord refuses stops that round's lane alone. The opening hands it what only it
knows (whether the tables stand, and the lines naming those a league admin discarded). A request made
by the bot alone (a restart's recovery, which has no member) keeps all the jobs on the league
lane. Discarding the prompt's post asks the review again, as before, for the same member. The
choice is read from the actor, not the origin: the review asked for again is the bot's, though made
for a member, and the member's conversation is still waiting on its prompt.

**The check refuses a member and drops the bot.** A member's request is refused where the round
cannot enter a review, its submission channel's row is closed or the channel is gone, or the
round's reports are already being approved. The bot's request (a lapse, a failure, a restart) is
no longer due in those cases and is dropped; only a channel missing or unusable stops the queue,
with the check's reason, since the manager can set it right and press Retry.

The payload is ``round_id``, ``label``, ``publish`` (whether the interim results go out),
``returning`` (``"cancelled"`` or ``"failed"`` for a resubmission's return, ``"restart"`` for
one a restart cut short), ``while_resubmitting`` (set on those first requests: the check finds
the request no longer due, or refuses it, where the round is no longer resubmitting, as its swap
may have replaced the results while it waited), ``cancel_message_id`` and ``old_prompt_id``.

**A restart that cut a resubmission short is ended in the opening's own jobs** (``returning`` =
``"restart"``, the bot's request). The collection lived in memory, so before anything else
**`abandon_resubmission`** clears the flag (the review channel's message guard is back in force
before the prompt returns), the announcement's id and the drafts the resubmission held; the
prompt's jobs then take the Cancel button down (`take_down_cancel`, the announcement named by
``cancel_message_id``) and **`post_restart_notice`** tells the channel the sessions entered were
lost and the earlier results stand. The closing line records the lapse, naming who started it
(``started_by_named``, worked out when the request was made) and the round (``round_label``).
Where the submission channel is gone (``channel_gone``) there is nowhere to take the button down,
to post the notice or to put the prompt: the notice completes posting nothing, nothing else is
opened, and the flag still comes down and the lapse is still recorded (results spec :417).

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
from leaguebot.core.services import season_service
from leaguebot.core.services.season_service import set_round_status_on
from leaguebot.core.services import typed_turns
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
from leaguebot.results.services.submission_steps import post_step

__all__ = [
    "APPEALS_OPEN_KIND", "CLOSE_STALE_KIND", "KIND", "PROMPT_KIND", "appeals_open_change",
    "close_stale_change", "review_open_change", "review_prompt_change",
]

KIND = "results.review.open"
APPEALS_OPEN_KIND = "results.appeals.open"
CLOSE_STALE_KIND = "results.review.close_stale"
PROMPT_KIND = "results.review.prompt"
APPEALS_STAGE = "appeals"
REPORTS_APPROVE = "results.reports.approve"
APPEALS_APPROVE = "results.appeals.approve"

_OPEN = "open"
_POST_REVIEW_PROMPT = "post_review_prompt"
_TAKE_DOWN_CANCEL = "take_down_cancel"
_ABANDON = "abandon_resubmission"
_POST_RESTART_NOTICE = "post_restart_notice"
_POST_CANCEL_LINE = "post_cancel_line"
_CLOSE = "close"

RESTART_NOTICE = (
    "⚠️ **The bot restarted during a results resubmission**, and the sessions entered so "
    "far were lost. The earlier results still stand. Press **🔄 Resubmit Initial Results** "
    "again to re-enter them."
)

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
                "       rsc.channel_id, rsc.closed, rsc.resubmitting "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                "LEFT JOIN round_submission_channels rsc ON rsc.round_id = r.id "
                "WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()


async def _post_review_prompt(ctx: StepContext) -> StepResult:
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


async def _record_prompt(
    db: aiosqlite.Connection, ctx: StepContext, result: StepResult
) -> None:
    """Save the prompt's id, so the next restart can replace it, and `results_posted` where
    the interim results went out and no job posting them was discarded."""
    if "results_posted" in ctx.payload:
        # Asked of the round's lane: the opening on the league lane said what it posted.
        posted = bool(ctx.payload["results_posted"])
    else:
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


async def _take_down_cancel_job(ctx: StepContext) -> StepResult:
    guild = await _league_guild(ctx.bot)
    channel = as_text_channel(guild.get_channel(int(ctx.step_payload["channel_id"])))
    if channel is not None:
        await take_down_cancel_message(
            channel.get_partial_message(int(ctx.step_payload["message_id"]))
        )
    return StepResult()


async def _prompt_not_discarded(ctx: StepContext) -> bool:
    """The cancel's line follows the prompt: where a league admin discarded the prompt's post,
    the review asked for again carries the cancel on and posts it once its own prompt stands."""
    return not any(
        view.name == _POST_REVIEW_PROMPT and _discarded(view.result) for view in ctx.steps
    )


async def _post_cancel_line_job(ctx: StepContext) -> StepResult:
    guild = await _league_guild(ctx.bot)
    channel = _channel(guild, int(ctx.step_payload["channel_id"]), "submission")
    await send_cancelled_line(channel)
    return StepResult()


async def _describe_prompt(ctx: StepContext) -> str:
    row = await _round_and_channel(ctx.db_path, int(ctx.payload["round_id"]))
    where = "" if row is None or row["channel_id"] is None else f" in <#{row['channel_id']}>"
    return f"posting the penalty review prompt{where}"


async def _describe_take_down(ctx: StepContext) -> str:
    return f"taking the Cancel button off the resubmission in <#{ctx.step_payload['channel_id']}>"


async def _describe_cancel_line(ctx: StepContext) -> str:
    return f"posting that the resubmission was cancelled in <#{ctx.step_payload['channel_id']}>"

def _tables_discarded(ctx: OutcomeContext) -> bool:
    """Whether a league admin discarded a job that posts the interim results."""
    return any(
        _discarded(view.result) for view in ctx.steps
        if view.name in (POST_SESSION_RESULTS, POST_STANDINGS)
    )


def prompt_steps(payload: dict[str, Any], channel_id: int) -> list[PlannedStep]:
    """The jobs that put the review's prompt up, in order: the prompt, the old prompt's deletion
    once the new one stands, the resubmission's Cancel button taken down, and for a cancel its
    line."""
    steps = [PlannedStep(_POST_REVIEW_PROMPT)]
    if payload.get("old_prompt_id") is not None:
        steps.append(PlannedStep(DELETE_MESSAGE, {
            "channel_id": channel_id, "message_id": int(payload["old_prompt_id"]),
            "what": "penalty review prompt",
        }))
    if payload.get("cancel_message_id") is not None:
        steps.append(PlannedStep(_TAKE_DOWN_CANCEL, {
            "channel_id": channel_id, "message_id": int(payload["cancel_message_id"]),
        }))
    if payload.get("returning") == "restart":
        steps.append(PlannedStep(_POST_RESTART_NOTICE, {"channel_id": channel_id}))
    if payload.get("returning") == "cancelled":
        steps.append(PlannedStep(_POST_CANCEL_LINE, {"channel_id": channel_id}))
    return steps


def appeals_stage_steps(payload: dict[str, Any]) -> list[PlannedStep]:
    """The jobs that put the appeals stage up, in order: each message the stage-one review leaves
    standing taken down (``takedowns``), then the appeals prompt."""
    channel_id = int(payload["channel_id"])
    steps = [
        PlannedStep(DELETE_MESSAGE, {
            "channel_id": channel_id, "message_id": int(down["message_id"]),
            "what": down["what"],
        })
        for down in payload.get("takedowns", [])
    ]
    steps.append(PlannedStep(
        review_verdicts.POST_APPEALS_PROMPT,
        {"round_id": int(payload["round_id"]), "division_id": int(payload["division_id"])},
    ))
    return steps


def _closing_lines(
    ctx: OutcomeContext, number: Any, division: str, *, open_lost: bool, prompt_lost: bool,
    left: list[str] | None,
) -> tuple[str, ...]:
    """The line the log carries once the review is open (or back): none where the opening was
    discarded, none for a cancel whose prompt was discarded (the review asked for again records
    it once its own prompt stands), the cancel's or the failure's line for a return, else the
    manager's line, `Incomplete` where a table or the prompt was discarded, naming each (*left*,
    as `_not_done` gives it)."""
    returning = ctx.payload.get("returning")
    resubmission = f"the resubmission of round {number}{division}"
    if returning == "restart" and not open_lost:
        return (abandoned_line(
            str(ctx.payload["started_by_named"]),
            f"the “Resubmit Initial Results” button of {ctx.payload['round_label']}",
            lapsed=True,
            detail="The sessions entered were lost. The earlier results stand. "
                   "Press Resubmit again to re-enter them.",
        ),)
    if open_lost or (returning == "cancelled" and prompt_lost):
        return ()
    if returning == "cancelled":
        return (abandoned_line(
            ctx.named, resubmission, lapsed=False,
            detail="The earlier results stand.\nPress 🔄 Resubmit Initial Results to start again.",
        ),)
    if returning == "failed":
        return (
            f"❌ {resubmission} failed before the earlier results were replaced\n"
            "  The earlier results stand.\n"
            "  Press 🔄 Resubmit Initial Results to start again.",
        )
    if ctx.actor_id is not None:
        # A table or the prompt a league admin discarded is named, with the commands that post a
        # table, as the approvals' lines name theirs: the review is open, but the league's
        # channels are not as a Success would say.
        left = left or []
        return (f"{ctx.named} | Penalty review opened | "
                f"{'Incomplete' if left else 'Success'}\n"
                f"  round {number}{division}"
                + "".join(f"\n  {text}" for text in left),)
    return ()


async def _reopening(
    db: aiosqlite.Connection, payload: dict[str, Any], number: Any, *, prompt_lost: bool,
    tables_discarded: bool, carry_return: bool,
) -> FollowOn:
    """The request for the review again that follows a Discard ("Discard reopens the review"):
    a fresh prompt replaces the dead one, and the interim results are not posted twice where they
    already stand."""
    round_id = int(payload["round_id"])
    if prompt_lost and bool(payload.get("publish", True)) and not tables_discarded:
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
    if carry_return:
        for key in ("returning", "cancel_message_id", "started_by_named", "round_label"):
            if payload.get(key) is not None:
                again[key] = payload[key]
    if payload.get("mark") is not None:
        again["mark"] = payload["mark"]
    return FollowOn(
        KIND, again,
        f"the penalty review of round {number}, opened again after a job of it was discarded",
    )


def _hand_the_prompt_over(ctx: StepContext, channel_id: int) -> StepResult:
    """Ask the round's lane, in the opening's last save, to put the prompt up (C44a).

    The prompt is the submission channel's own post, so the round's lane posts it (R2(c)); the
    league's opening has put the interim results out by now, as the prompt waits for them. What the
    opening knows and the lane's change cannot read from its own steps goes in the payload: whether
    the results stand, and the lines naming what a league admin discarded, worded for a prompt that
    stood and for one that is posted again.
    """
    payload = ctx.payload
    tables_discarded = _tables_discarded(ctx)
    asked = {
        "round_id": int(payload["round_id"]),
        "channel_id": channel_id,
        "label": payload.get("label") or "Provisional Results",
        "publish": bool(payload.get("publish", True)),
        "round_number": payload.get("round_number"),
        "division_name": payload.get("division_name"),
        "old_prompt_id": payload.get("old_prompt_id"),
        "cancel_message_id": payload.get("cancel_message_id"),
        "returning": payload.get("returning"),
        "tables_discarded": tables_discarded,
        "results_posted": bool(payload.get("publish", True)) and not tables_discarded,
        "left": review_posting.not_done(ctx, reposted=False),
        "left_reposted": review_posting.not_done(ctx, reposted=True),
        "mark": payload.get("mark"),
    }
    return StepResult(
        result={"closed": True},
        follow_ons=(FollowOn(
            PROMPT_KIND, asked,
            f"the penalty review prompt of round {payload.get('round_number') or payload['round_id']}",
        ),),
    )


def _marked_channel(payload: dict[str, Any]) -> int | None:
    """The channel of the typed message the opening was asked with (its ``mark``), which waits
    while the opening is in hand: the last paste handed the review to the league, and a message
    typed after it is turned away like any typed while a turn waits. None where the opening was
    asked by no message (a round's start, a restart)."""
    mark = payload.get("mark")
    return int(mark["channel_id"]) if mark else None


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
        if ctx.payload.get("while_resubmitting") and not row["resubmitting"]:
            # The resubmission this would end has ended already: its swap replaced the results
            # (or another return ended it) while this waited. Ending it again would say the earlier
            # results stand over the replaced ones. A request a Discard asks again does not carry
            # the mark, as the flag it waits on is cleared by then.
            return no("The resubmission has already ended.")
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
            if ctx.payload.get("channel_gone"):
                # A resubmission a restart cut short is still ended, its notice completing.
                return Verdict.go()
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
        if returning:
            # A resubmission that ends without its swap leaves nothing of its attempt behind, so
            # the next Resubmit asks for the first session again.
            await db.execute("DELETE FROM resubmission_drafts WHERE round_id = ?", (round_id,))
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
        if ctx.actor_id is None:
            then.extend(prompt_steps(payload, channel_id))
        return StepResult(result={"opened": True}, then=tuple(then))

    async def names_not_discarded(ctx: StepContext) -> bool:
        """The save reads the display names only where the results are published, and is due
        only where they were resolved: a discarded `names` leaves nothing changed."""
        if not bool(ctx.payload.get("publish", True)):
            return True
        return not any(view.name == NAMES and _discarded(view.result) for view in ctx.steps)

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        round_id = int(payload["round_id"])
        row = await (
            await db.execute(
                "SELECT r.round_number, d.name AS division_name, rsc.channel_id "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                "LEFT JOIN round_submission_channels rsc ON rsc.round_id = r.id "
                "WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
        number = "?" if row is None else row["round_number"]
        division = "" if row is None else f" ({row['division_name']})"
        views = {view.name: view for view in ctx.steps}
        opened = views.get(_OPEN)
        gone = bool(payload.get("channel_gone"))
        open_lost = (opened is None and not gone) or (
            opened is not None and (_discarded(opened.result) or _dropped(opened.result))
        )
        prompt = views.get(_POST_REVIEW_PROMPT)
        prompt_lost = prompt is not None and _discarded(prompt.result)

        if not open_lost and ctx.actor_id is not None and row is not None and row["channel_id"]:
            return _hand_the_prompt_over(ctx, int(row["channel_id"]))

        returning = payload.get("returning")
        lines = _closing_lines(
            ctx, number, division, open_lost=open_lost, prompt_lost=prompt_lost,
            left=None if open_lost else _not_done(ctx),
        )
        if not (open_lost or prompt_lost):
            return StepResult(result={"closed": True}, lines=lines)

        # A discarded `open` wrote nothing, so the return is carried on; so is a cancel whose
        # prompt was discarded, its line still owed until a prompt stands.
        return StepResult(
            result={"closed": True, "reopened": True},
            lines=lines,
            follow_ons=(await _reopening(
                db, payload, number, prompt_lost=prompt_lost,
                tables_discarded=_tables_discarded(ctx),
                carry_return=open_lost or returning == "cancelled",
            ),),
        )

    async def describe_open(_ctx: StepContext) -> str:
        return "opening the penalty review"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording that the penalty review is open"

    async def would_be_left_unreviewed(ctx: StepContext) -> str:
        called = await season_service.round_called(ctx.db_path, int(ctx.payload["round_id"]))
        return (
            f"{called} could be left in its penalty review with no prompt, never final, until "
            "the bot restarts"
        )

    def outcome(ctx: OutcomeContext) -> str:
        done = {view.name: view for view in ctx.steps}
        opened = done.get(_OPEN)
        if ctx.payload.get("channel_gone"):
            return "The resubmission was ended. Its submission channel is gone."
        if opened is None or _discarded(opened.result) or _dropped(opened.result):
            return "Nothing was changed. The penalty review is being opened again."
        number = ctx.payload.get("round_number") or ctx.payload["round_id"]
        division = ctx.payload.get("division_name")
        named = f"round {number}" + (f" ({division})" if division else "")
        if ctx.payload.get("returning") == "cancelled":
            # What the Cancel press was told before the review was asked for (results :410).
            return "\n".join([
                f"↩️ Resubmission of {named} cancelled. The earlier results stand.",
                *_not_done(ctx),
            ])
        reply = f"✅ The penalty review is open ({named})."
        return "\n".join([reply, *_not_done(ctx)])

    async def abandon(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        await db.execute(
            "UPDATE round_submission_channels SET resubmitting = 0, "
            "resubmit_prompt_message_id = NULL WHERE round_id = ?",
            (round_id,),
        )
        await db.execute("DELETE FROM resubmission_drafts WHERE round_id = ?", (round_id,))
        return StepResult(result={"abandoned": True})

    async def describe_abandon(_ctx: StepContext) -> str:
        return "ending the resubmission a restart cut short"

    notice = post_step(
        _POST_RESTART_NOTICE, describing="telling the channel the resubmission was lost"
    )

    async def post_restart_notice(ctx: StepContext) -> StepResult:
        """Say the sessions entered were lost; completing, posting nothing, where the channel is
        gone."""
        guild = await _league_guild(ctx.bot)
        if as_text_channel(guild.get_channel(int(ctx.step_payload["channel_id"]))) is None:
            return StepResult(result={"posted": False})
        return await notice.run(
            replace(ctx, step_payload={**ctx.step_payload, "text": RESTART_NOTICE})
        )

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        if payload.get("channel_gone"):
            return (
                PlannedStep(_ABANDON),
                PlannedStep(_POST_RESTART_NOTICE, {"channel_id": int(payload["channel_id"])}),
                PlannedStep(_CLOSE),
            )
        if payload.get("returning") == "restart":
            return (
                PlannedStep(NAMES), PlannedStep(_ABANDON), PlannedStep(_OPEN), PlannedStep(_CLOSE)
            )
        return (PlannedStep(NAMES), PlannedStep(_OPEN), PlannedStep(_CLOSE))

    steps: dict[str, Step] = {
        **{name: step for name, step in posting_steps().items()},
        _ABANDON: Step(_ABANDON, StepKind.SAVE, abandon, describe=describe_abandon),
        _POST_RESTART_NOTICE: Step(
            _POST_RESTART_NOTICE, StepKind.ACT, post_restart_notice, describe=notice.describe,
        ),
        _OPEN: Step(
            _OPEN, StepKind.SAVE, open_review, still_due=names_not_discarded,
            describe=describe_open,
        ),
        _POST_REVIEW_PROMPT: Step(
            _POST_REVIEW_PROMPT, StepKind.ACT, _post_review_prompt,
            describe=_describe_prompt, record=_record_prompt,
        ),
        _TAKE_DOWN_CANCEL: Step(
            _TAKE_DOWN_CANCEL, StepKind.EDIT, _take_down_cancel_job, describe=_describe_take_down,
        ),
        _POST_CANCEL_LINE: Step(
            _POST_CANCEL_LINE, StepKind.ACT, _post_cancel_line_job, still_due=_prompt_not_discarded,
            describe=_describe_cancel_line,
        ),
        _CLOSE: Step(
            _CLOSE, StepKind.SAVE, close, describe=describe_close,
            undiscardable=would_be_left_unreviewed,
        ),
    }
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(NAMES), PlannedStep(_OPEN), PlannedStep(_CLOSE)),
        plan=plan,
        steps=steps,
        check=check,
        key=lambda payload: f"{KIND}:{payload['round_id']}",
        doing=lambda payload: _naming("Opening the penalty review", payload),
        outcome=outcome,
        turn_channel=_marked_channel,
    )


def review_prompt_change() -> ChangeType:
    """The change that puts a round's penalty review prompt up, on the round's lane (C44a).

    Asked by `results.review.open` in its last save where a conversation handed the review over (a
    last paste, a resubmission's swap or Cancel): it posts the stage prompt in the submission
    channel, deletes the old one once the new stands, takes a resubmission's Cancel button down and
    posts the cancel line, and records the prompt's id on the round's row. A refusal stops the
    round's lane alone. Where the prompt's post is discarded its `close` asks the review again, as
    the opening did when the prompt was one of its own jobs, and writes the opening's line.

    The payload is the opening's own (``round_id``, ``label``, ``publish``, ``old_prompt_id``,
    ``cancel_message_id``, ``returning``) with ``channel_id`` (the lane), ``results_posted``,
    ``tables_discarded`` and the lines ``left`` and ``left_reposted`` the opening worded. The
    conversation still stands while the round's row names the channel and is not closed.

    **The same change puts the appeals stage up** (C44b), where its payload has ``stage`` set to
    `APPEALS_STAGE`: the reports approval, in its last save, asks it to take the stage-one prompt
    and the approval message down (``takedowns``, each ``message_id`` and ``what``) and to post the
    appeals prompt for ``round_id`` and ``division_id``, saving its id on the round's row. A
    prompt whose post is discarded is asked for again by `results.appeals.open`, as the approval
    did when the prompt was one of its own jobs.
    """

    async def check(ctx: CheckContext) -> Verdict:
        row = await _round_and_channel(ctx.db_path, int(ctx.payload["round_id"]))
        if row is None or row["channel_id"] is None or row["closed"] or (
            int(row["channel_id"]) != int(ctx.payload["channel_id"])
        ):
            return Verdict.not_due("The round's submission channel is closed.")
        if ctx.payload.get("stage") == APPEALS_STAGE and (
            row["status"] != RoundStatus.AWAITING_APPEAL_VERDICTS.value
        ):
            return Verdict.not_due("The round is no longer awaiting its appeal verdicts.")
        return Verdict.go()

    async def conversation_open(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
        row = await (
            await db.execute(
                "SELECT 1 FROM round_submission_channels WHERE round_id = ? AND channel_id = ? "
                "AND closed = 0",
                (int(payload["round_id"]), int(payload["channel_id"])),
            )
        ).fetchone()
        return row is not None

    async def close_appeals_stage(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Where the appeals prompt's post was discarded, ask for it again, as the bot: the stage
        stays open with no prompt otherwise ("Discard reopens the review")."""
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

    async def close(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        if payload.get("stage") == APPEALS_STAGE:
            return await close_appeals_stage(db, ctx)
        row = await (
            await db.execute(
                "SELECT r.round_number, d.name AS division_name FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
                (int(payload["round_id"]),),
            )
        ).fetchone()
        number = "?" if row is None else row["round_number"]
        division = "" if row is None else f" ({row['division_name']})"
        prompt = next((view for view in ctx.steps if view.name == _POST_REVIEW_PROMPT), None)
        prompt_lost = prompt is not None and _discarded(prompt.result)
        left = list(payload["left_reposted" if prompt_lost else "left"])
        if prompt_lost:
            left.append("⚠️ The penalty review prompt was not posted. It is being posted again.")
        lines = _closing_lines(
            ctx, number, division, open_lost=False, prompt_lost=prompt_lost, left=left,
        )
        if not prompt_lost:
            return StepResult(result={"closed": True}, lines=lines)
        return StepResult(
            result={"closed": True, "reopened": True},
            lines=lines,
            follow_ons=(await _reopening(
                db, payload, number, prompt_lost=True,
                tables_discarded=bool(payload["tables_discarded"]),
                carry_return=payload.get("returning") == "cancelled",
            ),),
        )

    async def describe_close(_ctx: StepContext) -> str:
        return "recording that the penalty review is open"

    async def would_be_left_unreviewed(ctx: StepContext) -> str:
        called = await season_service.round_called(ctx.db_path, int(ctx.payload["round_id"]))
        review = "appeals" if ctx.payload.get("stage") == APPEALS_STAGE else "penalty"
        return (
            f"{called} could be left in its {review} review with no prompt, never final, until "
            "the bot restarts"
        )

    def outcome(ctx: OutcomeContext) -> str:
        if ctx.payload.get("stage") == APPEALS_STAGE:
            left = review_posting.not_done(ctx)
            if any(
                view.name == review_verdicts.POST_APPEALS_PROMPT and _discarded(view.result)
                for view in ctx.steps
            ):
                return "\n".join([
                    "⚠️ The appeals review prompt was not posted. It is being posted again.", *left,
                ])
            return "\n".join([
                f"✅ The appeals review prompt is up (round {ctx.payload['round_id']}).", *left,
            ])
        return f"✅ The penalty review prompt is up (round {ctx.payload['round_id']})."

    def finished_lines(ctx: OutcomeContext) -> tuple[str, ...]:
        """A discarded appeals prompt is named in the log, as the approval's own line named it
        when the prompt was one of its jobs: the reply that approved the reports has long been
        sent."""
        if ctx.payload.get("stage") != APPEALS_STAGE:
            return ()
        prompt = next(
            (v for v in ctx.steps if v.name == review_verdicts.POST_APPEALS_PROMPT), None
        )
        if prompt is None or not _discarded(prompt.result):
            return ()
        number = ctx.payload.get("round_number") or ctx.payload["round_id"]
        return (
            f"{ctx.named} | PENALTY_REVIEW_APPROVED | Incomplete\n"
            f"  round: {number} ({ctx.payload.get('division_name', '?')})\n"
            "  ⚠️ The appeals review prompt was not posted. It is being posted again.",
        )

    def tell_origin(ctx: OutcomeContext) -> str | None:
        """The manager's reply to the approval, while it can be updated, hears of a discarded
        appeals prompt too: the approval's own reply was sent before the prompt was tried."""
        if ctx.payload.get("stage") != APPEALS_STAGE:
            return None
        prompt = next(
            (v for v in ctx.steps if v.name == review_verdicts.POST_APPEALS_PROMPT), None
        )
        if prompt is None or not _discarded(prompt.result):
            return None
        return "⚠️ The appeals review prompt was not posted. It is being posted again."

    steps: dict[str, Step] = {
        DELETE_MESSAGE: posting_steps()[DELETE_MESSAGE],
        review_verdicts.POST_APPEALS_PROMPT: review_verdicts.appeals_prompt_step(),
        _POST_REVIEW_PROMPT: Step(
            _POST_REVIEW_PROMPT, StepKind.ACT, _post_review_prompt,
            describe=_describe_prompt, record=_record_prompt,
        ),
        _TAKE_DOWN_CANCEL: Step(
            _TAKE_DOWN_CANCEL, StepKind.EDIT, _take_down_cancel_job, describe=_describe_take_down,
        ),
        _POST_CANCEL_LINE: Step(
            _POST_CANCEL_LINE, StepKind.ACT, _post_cancel_line_job,
            still_due=_prompt_not_discarded, describe=_describe_cancel_line,
        ),
        _CLOSE: Step(
            _CLOSE, StepKind.SAVE, close, describe=describe_close,
            undiscardable=would_be_left_unreviewed,
        ),
        typed_turns.CLEAR_MARK: typed_turns.clear_mark_step(),
    }

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        if payload.get("stage") == APPEALS_STAGE:
            return (*appeals_stage_steps(payload), PlannedStep(_CLOSE))
        # The last paste's ⏳ is cleared once the prompt stands (the member seeing nothing else
        # while the league worked), a reopening after a discard carrying it on.
        clear = (
            (PlannedStep(typed_turns.CLEAR_MARK, dict(payload["mark"])),)
            if payload.get("mark") else ()
        )
        return (*prompt_steps(payload, int(payload["channel_id"])), *clear, PlannedStep(_CLOSE))

    def key(payload: dict[str, Any]) -> str:
        stage = ":appeals" if payload.get("stage") == APPEALS_STAGE else ""
        return f"{PROMPT_KIND}{stage}:{payload['round_id']}"

    def doing(payload: dict[str, Any]) -> str:
        if payload.get("stage") == APPEALS_STAGE:
            return _naming("Putting up the appeals review prompt", payload)
        return _naming("Putting up the penalty review prompt", payload)

    return ChangeType(
        kind=PROMPT_KIND,
        opening=(),
        steps=steps,
        check=check,
        key=key,
        doing=doing,
        outcome=outcome,
        lane=lambda payload: f"channel:{int(payload['channel_id'])}",
        conversation_open=conversation_open,
        writes=("round_submission_channels",),
        plan=plan,
        finished_lines=finished_lines,
        tell_origin=tell_origin,
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
