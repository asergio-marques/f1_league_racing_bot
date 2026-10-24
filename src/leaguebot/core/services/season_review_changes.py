"""A season review on the change queue (#439, slice 6b, C92).

A season review is a report posted in the review's channel and, below it, the question with its
Approve button. This file holds the change that posts one and the change that takes one down.

**`season.review.post`** is the reviewer's: the whole review is posted by its jobs, in order, so
that a stopped queue holds it whole and a post Discord refuses stops the queue (decision 5, core
specification "Reviewing placements"). Its payload is ``channel_id``, ``season_id``,
``reviewer_id``, ``review`` (the command), ``pictures`` (each ``name``, ``division_id``, ``kind``,
``text``, and where the review draws it so, ``include_uncommitted`` or ``season_number``),
``report`` (its sections, in order), ``question`` (its text, or none where the review offers no
question) and ``evidence`` (what the report was computed from, kept with the question). Its jobs:

- `post_drawing_notice`, where pictures are due, then one `draw_and_post` per picture, then
  `delete_drawing_notice`: a picture is drawn by the callable the builder hands the factory,
  off the event loop, and posted with its text in place of it where Discord refuses the file
  (the same try). A picture that will not draw (a render fault, not Discord's) stops nothing: its
  text stands in, the fault is kept on the job, and the question is withheld. The
  reviewer is told which pictures did not draw in their reply, and the fault is written to the log
  channel besides, since the queue does not say whether a reply could be updated;
- `post_report`, one per section;
- `post_question`, whose record writes the review's `season_review_prompts` row (every report id,
  the evidence) and whose line records who ran the review; the Approve button is the view the
  builder's factory makes. Where a picture would not draw it posts nothing and records nothing,
  and `arm_expiry` finds no question to arm the five minutes from;
- `arm_expiry`, which arms the `review_expiry` timer through the scheduler the builder handed,
  five minutes after the question's job was marked done: the five minutes start when the question
  appears.

**`season.review.take_down`** is the bot's, asked for when a review ends: its five minutes ran out
(`expired`), the bot started again with the review standing (`restart`), a press was refused upon
a changed state (`refused`), or Approve was accepted (`confirmed`). Its payload is ``channel_id``,
``question_id``, ``report_ids``, ``reviewer_id``, ``review`` (the command that posted it, such as
`/season placements-review`) and ``ending``, and optionally ``verb`` (what the review would have
done, ``confirmed`` unless told) and ``press_failed`` (an earlier press failed and may have been
partly done). Its jobs are:

- one `delete_message` per message of the review, the question first, then the report, through
  `_delete_review_message`, which is the shared delete step's rule (a message already gone, or its
  channel, completes the job, and one Discord refuses stops the queue, a Discard leaving it for
  deletion by hand) with one addition: a channel the cache lacks, as it does at a restart, is
  fetched from Discord before it is taken to be gone;
- `post_expiry`, but where the review was confirmed: the notice pinging the reviewer alone and
  naming the command to run again. A channel that is gone is nothing to tell. It also reads the
  reviewer's name, as a save waits on no second connection;
- `forget`, a save: the `season_review_prompts` row goes, and, but where the review was confirmed
  or its end is the refusal's, the log line records the review's lapse, naming the reviewer.
  Messages a Discard left standing are named in a line of their own, for deletion by hand. It
  cannot be discarded: the review's row would stay on the bot's record, and the next restart
  would tell the reviewer it expired and record it as lapsed.

A job that fails stops the queue (core specification, "How a change is carried out").

Nothing is posted with a mention: a review names people, it does not summon them (the expiry
notice alone pings, and the reviewer alone).
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services import standing_posts
from leaguebot.core.services.change_queue import (
    Ahead,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.batch_notice import delete_notice, send_notice
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import abandoned_line, name_of_member

if TYPE_CHECKING:
    from leaguebot.core.services.scheduler_service import SchedulerService

log = logging.getLogger(__name__)

POST = "season.review.post"
TIMER = "review_expiry"
#: How long a review's question can be answered, from when it appears.
REVIEW_WINDOW = timedelta(minutes=5)
POST_DRAWING_NOTICE = "post_drawing_notice"
DRAW_AND_POST = "draw_and_post"
DELETE_DRAWING_NOTICE = "delete_drawing_notice"
POST_REPORT = "post_report"
POST_QUESTION = "post_question"
ARM_EXPIRY = "arm_expiry"
DRAWING_NOTICE = "\U0001f3a8 Drawing the graphics for this review \u2014 one moment."
#: How a picture's message is headed, by the kind of picture.
PICTURE_HEADINGS = {"calendar": "\U0001f4c5 **Calendar**", "lineup": "\U0001f3ce\ufe0f **Lineup**"}

#: Draws a review's picture to a file, raising where it will not draw. It is synchronous and the
#: change runs it in a worker thread; the builder makes it.
ReviewDrawing = Callable[[Mapping[str, Any]], Path]
#: The view of the question's Approve button, made from the post's payload by the builder's
#: factory; not made where a picture did not draw.
ApproveViewFactory = Callable[[dict[str, Any]], discord.ui.View]

_HOST_LOOP: contextvars.ContextVar[asyncio.AbstractEventLoop | None] = contextvars.ContextVar(
    "season_review_host_loop", default=None
)
_T = TypeVar("_T")


def await_from_worker(coroutine: Coroutine[Any, Any, _T]) -> _T:
    """Run *coroutine* to its end from the worker thread a picture is drawn in.

    The drawing the builder hands the change is synchronous, and a picture's render is not: it
    reads the bot's services and, where a driver has no portrait, fetches it through Discord's
    own session, which belongs to the bot's event loop. So the change records that loop where the
    worker thread inherits it, and the coroutine runs there; the loop is free meanwhile, the job
    awaiting the thread. Where no loop was recorded (a drawing called by hand) the coroutine runs
    on a loop of its own.
    """
    loop = _HOST_LOOP.get()
    if loop is None:
        return asyncio.run(coroutine)
    return asyncio.run_coroutine_threadsafe(coroutine, loop).result()


TAKE_DOWN = "season.review.take_down"
DELETE_MESSAGE = "delete_message"
POST_EXPIRY = "post_expiry"
FORGET = "forget"
#: How a review ends: approved, left unanswered five minutes, found standing at a restart, or
#: its press refused upon a changed state.
CONFIRMED, EXPIRED, RESTART, REFUSED = "confirmed", "expired", "restart", "refused"

#: What became of a review's button (`_PRESSES`): a press is being worked, the five minutes ran out
#: meanwhile, a press hit a fault and the review stands, or a take-down is asked and the button is
#: spent.
UNDER_WAY, TIMER_FIRED, FAILED, TAKING_DOWN = "UNDER_WAY", "TIMER_FIRED", "FAILED", "TAKING_DOWN"
#: What a review says it has not done when it lapses, by the family of its button.
VERBS = {"approve": "approved"}

#: What became of each standing review's button, by the id of its question. It is held in memory
#: and not on the row: only the button and the timer of the running bot read it, no change type
#: does, and a restart takes every standing review down (`RESTART`), whose lapse reads no mark, so
#: nothing is lost by it. Written outside any queued change, as a row's column would have been,
#: which would have been a save the architecture's rule forbids. Cleared with the league's other
#: in-memory state (`forget_presses`), and by the take-down's `forget` job with the row.
_PRESSES: dict[int, str] = {}

__all__ = [
    "ARM_EXPIRY", "ApproveViewFactory", "CONFIRMED", "DELETE_DRAWING_NOTICE", "DELETE_MESSAGE",
    "DRAW_AND_POST", "DRAWING_NOTICE", "EXPIRED", "FORGET", "POST", "POST_DRAWING_NOTICE",
    "POST_EXPIRY", "POST_QUESTION", "POST_REPORT", "REFUSED", "RESTART", "REVIEW_WINDOW",
    "FAILED", "ReviewDrawing", "claim_take_down", "forget_presses", "mark_press",
    "release_take_down", "take_review_down_before", "StandingReview", "TAKE_DOWN", "TAKING_DOWN", "TIMER_FIRED", "TIMER", "UNDER_WAY", "VERBS", "await_from_worker", "season_review_post_change",
    "season_review_take_down_change",
]


async def _review_channel(bot: Any, channel_id: int) -> discord.TextChannel | None:
    """The channel a review stands in, or None where it is gone: found in the cache, then on the
    league's server, then fetched.

    A restart takes a review down while the cache may not yet hold every channel, and a cache
    miss looks like a deleted channel, so trusting the cache alone would count a message still
    standing as gone with its channel. A channel Discord says is gone, or that the bot may not
    see, is gone; a fetch that fails otherwise stops the queue, the review's row standing until
    the try after, Retry or Discard.
    """
    channel = as_text_channel(bot.get_channel(channel_id))
    if channel is not None:
        return channel
    guild = await league_guild(bot)
    if guild is not None:
        channel = as_text_channel(guild.get_channel(channel_id))
        if channel is not None:
            return channel
    try:
        return as_text_channel(await bot.fetch_channel(channel_id))
    except (discord.NotFound, discord.Forbidden):
        return None
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord("the review's channel could not be looked up") from exc


async def _delete_review_message(ctx: StepContext) -> StepResult:
    """Delete one message of the review. A channel that is gone completes the job (the message
    went with it); a `NotFound` propagates, which the queue completes; any other Discord refusal
    stops the queue, keeping the message's link for a Discard to name.

    This is `standing_posts`' delete step with a different channel lookup, not a reuse of it: the
    take-down also runs at a restart, when the channel is in no cache, so the channel is fetched
    from Discord (`_review_channel`) before it is taken to be gone, where the shared step asks the
    league's server and counts a missing channel as gone."""
    payload = ctx.step_payload
    channel = await _review_channel(ctx.bot, int(payload["channel_id"]))
    if channel is None:
        return StepResult(result={"gone": True})
    message = channel.get_partial_message(int(payload["message_id"]))
    try:
        await delete_notice(message)
    except discord.NotFound:
        raise
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(
            "a message of the review could not be deleted", result={"link": message.jump_url},
        ) from exc
    return StepResult(result={"deleted": True})


def season_review_take_down_change() -> ChangeType:
    """The `season.review.take_down` change type, for the builder to register."""

    async def check(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        channel_id = int(payload["channel_id"])
        planned = [
            PlannedStep(DELETE_MESSAGE, {
                "channel_id": channel_id, "message_id": int(message_id), "what": "review message",
            })
            for message_id in (payload["question_id"], *payload["report_ids"])
        ]
        shared = {
            key: payload[key] for key in ("channel_id", "reviewer_id", "review", "ending")
        }
        shared.update({key: payload[key] for key in ("verb", "press_failed") if key in payload})
        if payload["ending"] != CONFIRMED:
            planned.append(PlannedStep(POST_EXPIRY, dict(shared)))
        planned.append(PlannedStep(FORGET, {**shared, "question_id": int(payload["question_id"])}))
        return tuple(planned)

    @posts_in_a_job
    async def post_expiry(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        reviewer = int(payload["reviewer_id"])
        named = await name_of_member(ctx.bot, reviewer)
        channel = await _review_channel(ctx.bot, int(payload["channel_id"]))
        if channel is None:
            return StepResult(result={"named": named, "no_channel": True})
        review = payload["review"]
        if payload["ending"] == RESTART:
            text = (
                f"⏱️ <@{reviewer}> your review expired while the bot was restarting and can no "
                f"longer be answered. Run `{review}` again."
            )
        else:
            text = (
                f"⏱️ <@{reviewer}> your review has expired and can no longer be answered. "
                f"Run `{review}` again."
            )
        await send_notice(
            channel, text,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(id=reviewer)]
            ),
        )
        return StepResult(result={"named": named})

    def _named(ctx: OutcomeContext, payload: dict[str, Any]) -> str:
        for view in ctx.steps:
            named = (view.result or {}).get("named")
            if view.name == POST_EXPIRY and view.done and named:
                return str(named)
        return f"<@{payload['reviewer_id']}>"

    async def forget(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        await db.execute(
            "DELETE FROM season_review_prompts WHERE message_id = ?", (int(payload["question_id"]),)
        )
        # Its button is spent with the row. A save that then failed to commit would leave the row
        # with no mark, but the queue stops there and a Retry carries the job on.
        mark_press(int(payload["question_id"]), None)
        lines: list[str] = []
        review = payload["review"]
        if payload["ending"] in (EXPIRED, RESTART):
            if payload["ending"] == RESTART:
                detail = (
                    "The bot restarted while it stood, so it can no longer be answered, and "
                    f"nothing has been {payload.get('verb', 'confirmed')}. Run `{review}` again."
                )
            elif payload.get("press_failed"):
                detail = (
                    "An earlier press of the button failed and may have been partly done. "
                    f"Check the failure line before running `{review}` again."
                )
            else:
                detail = (
                    f"Nothing has been {payload.get('verb', 'confirmed')}. "
                    f"Run `{review}` again."
                )
            lines.append(abandoned_line(
                _named(ctx, payload), f"`{review}`", lapsed=True, detail=detail
            ))
        left = [
            f"{view.payload['message_id']} in <#{view.payload['channel_id']}>"
            for view in ctx.steps
            if view.name == DELETE_MESSAGE and "discarded" in (view.result or {})
        ]
        if left:
            lines.append(
                f"SEASON REVIEW | `{review}` | NOT DELETED\n  messages: {', '.join(left)}\n"
                "  note: Discord refused to delete them and a league admin discarded the job; "
                "delete them by hand."
            )
        return StepResult(result={}, lines=tuple(lines))

    async def describe_delete(ctx: StepContext) -> str:
        return f"deleting a message of the review from <#{ctx.step_payload['channel_id']}>"

    async def describe_expiry(ctx: StepContext) -> str:
        return f"telling <@{ctx.step_payload['reviewer_id']}> their review expired"

    async def describe_forget(ctx: StepContext) -> str:
        return f"forgetting the review of <@{ctx.step_payload['reviewer_id']}>"

    async def stays_on_record(ctx: StepContext) -> str:
        return (
            f"<@{ctx.step_payload['reviewer_id']}>'s review would stay on the bot's record, and "
            "the next restart would tell them it expired and record it as lapsed"
        )

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=TAKE_DOWN,
        opening=(),
        plan=plan,
        steps={
            DELETE_MESSAGE: Step(
                DELETE_MESSAGE, StepKind.DELETE, _delete_review_message, describe=describe_delete
            ),
            POST_EXPIRY: Step(
                POST_EXPIRY, StepKind.ACT, post_expiry, describe=describe_expiry
            ),
            FORGET: Step(
                FORGET, StepKind.SAVE, forget, describe=describe_forget,
                undiscardable=stays_on_record,
            ),
        },
        check=check,
        key=lambda payload: f"{TAKE_DOWN}:{payload['question_id']}",
        doing=lambda payload: f"Taking down the review of <@{payload['reviewer_id']}>",
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# season.review.post
# ---------------------------------------------------------------------------

_NO_MENTIONS = discord.AllowedMentions.none()


def _view(ctx: OutcomeContext, name: str) -> list[Any]:
    return [view for view in ctx.steps if view.name == name]


def _faults(ctx: OutcomeContext) -> list[tuple[str, str]]:
    """Each picture that would not draw, as its name and the fault, in the order drawn."""
    return [
        (str(view.payload["picture"]["name"]), str(view.result["fault"]))
        for view in _view(ctx, DRAW_AND_POST)
        if view.result and view.result.get("fault")
    ]


def _posted_ids(ctx: OutcomeContext) -> list[int]:
    """The ids of the pictures and report sections posted so far, in the order posted."""
    return [
        int(view.result["new"])
        for view in ctx.steps
        if view.name in (DRAW_AND_POST, POST_REPORT) and view.result and "new" in view.result
    ]


async def _channel_of(ctx: StepContext) -> discord.TextChannel:
    """The review's channel, or the stop of the queue naming it where it is gone."""
    guild = await league_guild(ctx.bot)
    if guild is None:
        raise GuildUnavailable("the league's server is not in the cache")
    channel_id = int(ctx.payload["channel_id"])
    channel = as_text_channel(guild.get_channel(channel_id))
    if channel is None:
        raise StepFailedOnDiscord(
            f"the channel <#{channel_id}> no longer exists or cannot be posted in"
        )
    return channel


def season_review_post_change(
    *, drawing: ReviewDrawing, approve_view: ApproveViewFactory, scheduler: "SchedulerService",
    discard: Callable[[Path], None] | None = None,
) -> ChangeType:
    """The `season.review.post` change type, for the builder to register.

    *drawing* draws a picture to a file (synchronously, raising where it will not draw),
    *approve_view* makes the question's view from the post's payload, and *scheduler* arms the
    `review_expiry` timer; the builder hands all three, so that core imports nothing of the image
    module and no job looks a service up on the bot. *discard*, where given, removes a drawn
    picture once its job is over, whatever came of the post: the builder hands the image module's
    `discard_render`, which takes the render's directory too; without it the file alone goes.
    """
    discard_drawn = discard or (lambda path: path.unlink(missing_ok=True))

    async def check(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        planned: list[PlannedStep] = []
        pictures = list(payload.get("pictures") or ())
        if pictures:
            planned.append(PlannedStep(POST_DRAWING_NOTICE))
            planned.extend(PlannedStep(DRAW_AND_POST, {"picture": picture}) for picture in pictures)
            planned.append(PlannedStep(DELETE_DRAWING_NOTICE))
        planned.extend(PlannedStep(POST_REPORT, {"text": text}) for text in payload["report"])
        if payload.get("question") is not None:
            planned.append(PlannedStep(POST_QUESTION))
            planned.append(PlannedStep(ARM_EXPIRY))
        return tuple(planned)

    @posts_in_a_job
    async def post_drawing_notice(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        channel = await _channel_of(ctx)
        message = await send_notice(channel, DRAWING_NOTICE, allowed_mentions=_NO_MENTIONS)
        return StepResult(result={"new": message.id, "channel_id": channel.id})

    @posts_in_a_job
    async def draw_and_post(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        channel = await _channel_of(ctx)
        picture = ctx.step_payload["picture"]
        fault: str | None = None
        drawn: Path | None = None
        # The worker thread inherits the loop recorded here (`await_from_worker`).
        _HOST_LOOP.set(asyncio.get_running_loop())
        try:
            drawn = await asyncio.to_thread(drawing, picture)
        except Exception as exc:  # noqa: BLE001 — a render fault is no stop, it withholds Approve
            log.error("season review: %s did not draw: %s", picture["name"], exc, exc_info=True)
            fault = str(exc) or type(exc).__name__
        message: discord.Message | None = None
        if drawn is not None:
            attachment = discord.File(str(drawn), filename=drawn.name)
            try:
                message = await send_notice(
                    channel, PICTURE_HEADINGS.get(str(picture["kind"]), str(picture["name"])),
                    allowed_mentions=_NO_MENTIONS, file=attachment,
                )
            except discord.HTTPException as exc:
                # Discord refused the picture: the text stands in, in the same try.
                log.warning("season review: %s was refused as a picture: %s", picture["name"], exc)
            finally:
                attachment.close()
                discard_drawn(drawn)
        if message is None:
            message = await send_notice(
                channel, str(picture["text"]), allowed_mentions=_NO_MENTIONS
            )
        result: dict[str, Any] = {"new": message.id, "channel_id": channel.id}
        if fault is None:
            return StepResult(result=result)
        result["fault"] = fault
        line = (
            f"{ctx.named} | {ctx.payload['review']} | Picture not drawn\n"
            f"  picture: {picture['name']}\n  fault: {fault}"
        )
        return StepResult(result=result, lines=(line,))

    shared_delete = standing_posts.delete_replaced_step(DELETE_DRAWING_NOTICE)

    async def delete_drawing_notice(ctx: StepContext) -> StepResult:
        posted = next(
            (view.result for view in _view(ctx, POST_DRAWING_NOTICE) if view.result), None
        )
        if not posted or "new" not in posted:
            return StepResult(result={"dropped": True})
        # The notice's id is known only once it is posted, after which the pictures come before
        # its take-down, so it cannot be planned by `then` from its post: the job runs the shared
        # delete step on the notice's recorded id.
        return await shared_delete.run(dataclasses.replace(ctx, step_payload={
            "channel_id": int(posted["channel_id"]), "message_id": int(posted["new"]),
            "what": "drawing notice",
        }))

    @posts_in_a_job
    async def post_report(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        channel = await _channel_of(ctx)
        message = await send_notice(
            channel, str(ctx.step_payload["text"]), allowed_mentions=_NO_MENTIONS
        )
        return StepResult(result={"new": message.id, "channel_id": channel.id})

    @posts_in_a_job
    async def post_question(ctx: StepContext) -> StepResult:
        # A picture that would not draw withholds the question altogether (core's rule for every
        # other fault of a review): no question, no five minutes, no expiry notice; the report
        # stands and the outcome tells the reviewer which picture failed.
        if _faults(ctx):
            return StepResult(result={"dropped": True})
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        channel = await _channel_of(ctx)
        message = await send_notice(
            channel, str(ctx.payload["question"]), allowed_mentions=_NO_MENTIONS,
            view=approve_view(dict(ctx.payload)),
        )
        line = f"{ctx.named} | {ctx.payload['review']} | Review posted"
        return StepResult(
            result={
                "new": message.id, "channel_id": channel.id,
                "posted_at": datetime.now(timezone.utc).isoformat(),
            },
            lines=(line,),
        )

    async def record_question(
        db: aiosqlite.Connection, ctx: StepContext, result: StepResult
    ) -> None:
        """The review's row, saved with the question's mark: every report id, and the evidence
        the report was computed from. A question withheld has no row."""
        if "new" not in (result.result or {}):
            return
        await db.execute(
            "INSERT INTO season_review_prompts "
            "(message_id, season_id, channel_id, reviewer_id, review, report_message_ids, "
            "posted_at, evidence, verb) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(result.result["new"]), int(ctx.payload["season_id"]),
                int(ctx.payload["channel_id"]), int(ctx.payload["reviewer_id"]),
                str(ctx.payload["review"]), json.dumps(_posted_ids(ctx)),
                result.result["posted_at"], json.dumps(ctx.payload.get("evidence")),
                VERBS.get(str(ctx.payload.get("view", "approve")), "confirmed"),
            ),
        )

    async def arm_expiry(ctx: StepContext) -> StepResult:
        """Arm the five minutes from when the question's job was marked done, which is when the
        question appeared (the queue's clock, as the mark has it)."""
        question = next(
            (view.result for view in _view(ctx, POST_QUESTION) if view.result), None
        )
        if not question or "new" not in question:
            return StepResult(result={"dropped": True})
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT done_at FROM queued_change_steps WHERE change_id = ? AND name = ?",
                (ctx.change_id, POST_QUESTION),
            )
            row = await cursor.fetchone()
        posted = (
            datetime.fromisoformat(row["done_at"]) if row and row["done_at"]
            else datetime.now(timezone.utc)
        )
        run_at = posted + REVIEW_WINDOW
        scheduler.arm_once(TIMER, int(question["new"]), run_at)
        return StepResult(result={"armed": run_at.isoformat()})

    async def describe_notice(ctx: StepContext) -> str:
        return f"posting the notice that the review is being drawn in <#{ctx.payload['channel_id']}>"

    async def describe_draw(ctx: StepContext) -> str:
        return f"drawing and posting {ctx.step_payload['picture']['name']} in <#{ctx.payload['channel_id']}>"

    async def describe_delete_notice(ctx: StepContext) -> str:
        return f"deleting the drawing notice from <#{ctx.payload['channel_id']}>"

    async def describe_report(ctx: StepContext) -> str:
        return f"posting a section of the review in <#{ctx.payload['channel_id']}>"

    async def describe_question(ctx: StepContext) -> str:
        return f"posting the review's question in <#{ctx.payload['channel_id']}>"

    async def describe_arm(ctx: StepContext) -> str:
        return "arming the five minutes of the review's question"

    def outcome(ctx: OutcomeContext) -> str:
        faults = _faults(ctx)
        if not faults:
            return ""
        body = "\n".join(f"\u2022 **{name}**: {fault}" for name, fault in faults)
        # The review's family words what is withheld: a season's approval, or the placements'
        # (or the configuration's) confirmation, as the review's other faults say.
        withheld = {
            "approve": "the season is **not** offered for approval",
            "mid_season": "the placements are **not** offered for confirmation",
            "configuration": "the configuration is **not** offered for confirmation",
        }.get(str(ctx.payload.get("view", "approve")), "the review is **not** offered for approval")
        return (
            "\u26d4 **A graphic could not be drawn for this review.**\n"
            f"{body}\n"
            f"Its section was shown as text, and {withheld} "
            "while that stands, so no question was posted. Correct the template or the assets "
            f"it names, then run `{ctx.payload['review']}` again."
        )

    return ChangeType(
        kind=POST,
        opening=(),
        plan=plan,
        steps={
            POST_DRAWING_NOTICE: Step(
                POST_DRAWING_NOTICE, StepKind.ACT, post_drawing_notice, describe=describe_notice
            ),
            DRAW_AND_POST: Step(DRAW_AND_POST, StepKind.ACT, draw_and_post, describe=describe_draw),
            DELETE_DRAWING_NOTICE: Step(
                DELETE_DRAWING_NOTICE, StepKind.DELETE, delete_drawing_notice,
                describe=describe_delete_notice,
            ),
            POST_REPORT: Step(POST_REPORT, StepKind.ACT, post_report, describe=describe_report),
            POST_QUESTION: Step(
                POST_QUESTION, StepKind.ACT, post_question, describe=describe_question,
                record=record_question,
            ),
            ARM_EXPIRY: Step(ARM_EXPIRY, StepKind.ACT, arm_expiry, describe=describe_arm),
        },
        check=check,
        key=lambda payload: (
            f"{POST}:{payload['review']}:{payload['season_id']}:{payload['reviewer_id']}"
        ),
        doing=lambda payload: f"Posting the review of {payload['review']}",
        outcome=outcome,
    )


@dataclasses.dataclass(frozen=True)
class StandingReview:
    """A review's question as its row records it, and what became of its button (`_PRESSES`): what
    a press reads, in place of anything the button holds, so that a restart or a stopped queue
    changes nothing the review itself knows."""

    question_id: int
    season_id: int
    channel_id: int
    reviewer_id: int
    review: str
    report_ids: tuple[int, ...]
    posted_at: datetime
    evidence: dict[str, Any] | None
    verb: str
    press: str | None

    @property
    def deadline(self) -> datetime:
        """When the question stops being answerable: five minutes from when it was posted."""
        return self.posted_at + REVIEW_WINDOW


async def standing_review(db_path: str, question_id: int) -> StandingReview | None:
    """The review whose question is *question_id*, or None where it stands no longer."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM season_review_prompts WHERE message_id = ?", (int(question_id),)
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    try:
        report = tuple(int(item) for item in json.loads(row["report_message_ids"] or "[]"))
    except (TypeError, ValueError):
        report = ()
    posted = datetime.fromisoformat(row["posted_at"])
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    evidence = json.loads(row["evidence"]) if row["evidence"] else None
    return StandingReview(
        question_id=int(row["message_id"]), season_id=int(row["season_id"]),
        channel_id=int(row["channel_id"]), reviewer_id=int(row["reviewer_id"]),
        review=str(row["review"]), report_ids=report, posted_at=posted,
        evidence=evidence, verb=str(row["verb"]), press=_PRESSES.get(int(row["message_id"])),
    )


def mark_press(
    question_id: int, to: str | None, *, only_from: tuple[str | None, ...] | None = None
) -> bool:
    """Record what became of the button's press on *question_id*, and say whether it did.

    Where *only_from* is given the mark changes only from one of those states (None among them for
    a review untouched), which is how a press claims the button: a second press, finding it
    claimed, is refused. It awaits nothing, so a claim cannot be raced.
    """
    if only_from is not None and _PRESSES.get(int(question_id)) not in only_from:
        return False
    if to is None:
        _PRESSES.pop(int(question_id), None)
    else:
        _PRESSES[int(question_id)] = to
    return True


def forget_presses() -> None:
    """Drop every review's mark, with the rest of the league's state held in memory."""
    _PRESSES.clear()


async def claim_take_down(
    db_path: str, question_id: int, ending: str
) -> tuple[dict[str, Any], str | None] | None:
    """Claim the take-down of the review whose question is *question_id*, ending *ending*: its
    payload, and the mark the review had, for `release_take_down` to put back; None where the
    review stands no longer or a take-down of it is in hand.

    The review is marked `TAKING_DOWN` before anything is asked, so that two askers at once, a
    press and the timer, ask once. An earlier press that failed (`FAILED`) is carried to the lapse,
    which says so.
    """
    review = await standing_review(db_path, question_id)
    if review is None or review.press == TAKING_DOWN:
        return None
    if not mark_press(question_id, TAKING_DOWN, only_from=(review.press,)):
        return None
    payload: dict[str, Any] = {
        "channel_id": review.channel_id, "question_id": review.question_id,
        "report_ids": list(review.report_ids), "reviewer_id": review.reviewer_id,
        "review": review.review, "ending": ending, "verb": review.verb,
    }
    if review.press == FAILED:
        payload["press_failed"] = True
    return payload, review.press


def release_take_down(question_id: int, back_to: str | None) -> None:
    """Put a claimed take-down's mark back to *back_to*, the take-down not having been asked."""
    mark_press(question_id, back_to)


async def take_review_down(
    queue: Any, db_path: str, question_id: int, ending: str
) -> int | None:
    """Ask the queue to take the review whose question is *question_id* down, ending *ending*, as
    the bot; return the change's id, or None where nothing was asked.

    Nothing is asked where the review stands no longer, or where a take-down of it is in hand
    (`claim_take_down`). A fault in the ask puts the mark back as it was.
    """
    claimed = await claim_take_down(db_path, question_id, ending)
    if claimed is None:
        return None
    payload, was = claimed
    try:
        return await queue.ask(
            TAKE_DOWN, payload, what="taking a season review down", origin=ChangeOrigin.BOT
        )
    except BaseException:
        release_take_down(question_id, was)
        raise


async def take_review_down_before(
    queue: Any, db_path: str, question_id: int, kind: str, payload: dict[str, Any], **ask: Any
) -> int | None:
    """Ask the queue for the change *kind* and, in the same save, the take-down of the review
    whose question is *question_id*, ending `CONFIRMED`, ahead of it; return *kind*'s change id, or
    None where the queue refused it.

    The review comes down ahead of the change's jobs, but only for a change the queue accepts: one
    it refuses at once leaves the review standing, with its mark as it was, and no take-down is
    left on the queue (the core specification's "Confirming placements"). Where the review stands
    no longer, or a take-down of it is in hand, *kind* is asked alone. *ask* is what `ChangeQueue.ask`
    takes besides the kind and payload.
    """
    claimed = await claim_take_down(db_path, question_id, CONFIRMED)
    if claimed is None:
        return await queue.ask(kind, payload, **ask)
    take_down, was = claimed
    try:
        change_id = await queue.ask_after(
            Ahead(TAKE_DOWN, take_down, "taking a season review down"), kind, payload, **ask
        )
    except BaseException:
        release_take_down(question_id, was)
        raise
    if change_id is None:
        release_take_down(question_id, was)
    return change_id


def review_expiry_timer(
    db_path: str, queue_of: Callable[[], Any]
) -> Callable[[int | str], Awaitable[None]]:
    """The callback the builder registers for the `review_expiry` timer, handed the question's id.

    It reads the review's row again and acts on what the button's last press left. A review
    answered or being taken down is left to that. One whose press is under way is marked, so that
    the press, ending, takes it down if it did not go through: the expiry waits for the press. Any
    other is taken down, ending expired. Its row gone, it asks nothing: the key may be fired more
    than once. *queue_of* gives the queue when the timer fires, which a restart has rebuilt since.
    """

    async def fired(key: int | str) -> None:
        review = await standing_review(db_path, int(key))
        if review is None or review.press == TAKING_DOWN:
            return
        if review.press == UNDER_WAY:
            if mark_press(review.question_id, TIMER_FIRED, only_from=(UNDER_WAY,)):
                return
        elif review.press == TIMER_FIRED:
            return
        await take_review_down(queue_of(), db_path, review.question_id, EXPIRED)

    return fired
