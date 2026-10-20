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
  text stands in, the fault is kept on the job, and the question goes up without its button. The
  reviewer is told which pictures did not draw in their reply, and the fault is written to the log
  channel besides, since the queue does not say whether a reply could be updated;
- `post_report`, one per section;
- `post_question`, whose record writes the review's `season_review_prompts` row (every report id,
  the evidence) and whose line records who ran the review; the Approve button is the view the
  builder's factory makes, not made where a picture would not draw;
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

- one `delete_message` per message of the review, the question first, then the report, through the
  shared delete step: a message already gone, or its channel, completes the job, and one Discord
  refuses stops the queue, a Discard leaving it for deletion by hand;
- `post_expiry`, but where the review was confirmed: the notice pinging the reviewer alone and
  naming the command to run again. A channel that is gone is nothing to tell. It also reads the
  reviewer's name, as a save waits on no second connection;
- `forget`, a save: the `season_review_prompts` row goes, and, but where the review was confirmed
  or its end is the refusal's, the log line records the review's lapse, naming the reviewer.
  Messages a Discard left standing are named in a line of their own, for deletion by hand.

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

#: What became of a review's button, as its row records it (`season_review_prompts.press`): a
#: press is being worked, the five minutes ran out meanwhile, a press hit a fault and the review
#: stands, or a take-down is asked and the button is spent.
UNDER_WAY, TIMER_FIRED, FAILED, TAKING_DOWN = "UNDER_WAY", "TIMER_FIRED", "FAILED", "TAKING_DOWN"
#: What a review says it has not done when it lapses, by the family of its button.
VERBS = {"approve": "approved"}

__all__ = [
    "ARM_EXPIRY", "ApproveViewFactory", "CONFIRMED", "DELETE_DRAWING_NOTICE", "DELETE_MESSAGE",
    "DRAW_AND_POST", "DRAWING_NOTICE", "EXPIRED", "FORGET", "POST", "POST_DRAWING_NOTICE",
    "POST_EXPIRY", "POST_QUESTION", "POST_REPORT", "REFUSED", "RESTART", "REVIEW_WINDOW",
    "FAILED", "ReviewDrawing", "StandingReview", "TAKE_DOWN", "TAKING_DOWN", "TIMER_FIRED", "TIMER", "UNDER_WAY", "VERBS", "await_from_worker", "season_review_post_change",
    "season_review_take_down_change",
]


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
        guild = await league_guild(ctx.bot)
        channel = None if guild is None else as_text_channel(
            guild.get_channel(int(payload["channel_id"]))
        )
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

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=TAKE_DOWN,
        opening=(),
        plan=plan,
        steps={
            DELETE_MESSAGE: dataclasses.replace(
                standing_posts.delete_replaced_step(DELETE_MESSAGE), describe=describe_delete
            ),
            POST_EXPIRY: Step(
                POST_EXPIRY, StepKind.ACT, post_expiry, describe=describe_expiry
            ),
            FORGET: Step(FORGET, StepKind.SAVE, forget, describe=describe_forget),
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
    *, drawing: ReviewDrawing, approve_view: ApproveViewFactory, scheduler: "SchedulerService"
) -> ChangeType:
    """The `season.review.post` change type, for the builder to register.

    *drawing* draws a picture to a file (synchronously, raising where it will not draw),
    *approve_view* makes the question's view from the post's payload, and *scheduler* arms the
    `review_expiry` timer; the builder hands all three, so that core imports nothing of the image
    module and no job looks a service up on the bot.
    """

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
                drawn.unlink(missing_ok=True)
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

    async def delete_drawing_notice(ctx: StepContext) -> StepResult:
        posted = next(
            (view.result for view in _view(ctx, POST_DRAWING_NOTICE) if view.result), None
        )
        if not posted or "new" not in posted:
            return StepResult(result={"dropped": True})
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        channel = as_text_channel(guild.get_channel(int(posted["channel_id"])))
        if channel is None:
            return StepResult(result={"gone": True})
        message = channel.get_partial_message(int(posted["new"]))
        try:
            await delete_notice(message)
        except discord.NotFound:
            raise
        except discord.HTTPException as exc:
            raise StepFailedOnDiscord(
                "the drawing notice could not be deleted", result={"link": message.jump_url}
            ) from exc
        return StepResult(result={"deleted": True})

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
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        channel = await _channel_of(ctx)
        extra: dict[str, Any] = {}
        if not _faults(ctx):
            extra["view"] = approve_view(dict(ctx.payload))
        message = await send_notice(
            channel, str(ctx.payload["question"]), allowed_mentions=_NO_MENTIONS, **extra
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
        the report was computed from."""
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
        return (
            "\u26d4 **A graphic could not be drawn for this review.**\n"
            f"{body}\n"
            "Its section was shown as text, and the season is **not** offered for approval "
            f"while that stands. Correct the template or the assets it names, then run "
            f"`{ctx.payload['review']}` again."
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
    """A review's question as its row records it: what a press reads, in place of anything the
    button holds in memory, so that a restart or a stopped queue changes nothing it knows."""

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
        evidence=evidence, verb=str(row["verb"]), press=row["press"],
    )


async def mark_press(
    db_path: str, question_id: int, to: str | None, *, only_from: tuple[str | None, ...] | None = None
) -> bool:
    """Record what became of the button's press on *question_id*'s row, and say whether it did.

    Where *only_from* is given the row changes only from one of those states (None among them for
    a review untouched), which is how a press claims the button: a second press, finding it
    claimed, is refused. A row gone is not marked.
    """
    where = "message_id = ?"
    args: list[Any] = [int(question_id)]
    if only_from is not None:
        marks = ", ".join("?" for state in only_from if state is not None)
        clauses = [f"press IN ({marks})"] if marks else []
        if None in only_from:
            clauses.append("press IS NULL")
        where += " AND (" + " OR ".join(clauses) + ")"
        args += [state for state in only_from if state is not None]
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"UPDATE season_review_prompts SET press = ? WHERE {where}",
                                  (to, *args))
        await db.commit()
        return cursor.rowcount > 0


async def take_review_down(
    queue: Any, db_path: str, question_id: int, ending: str
) -> int | None:
    """Ask the queue to take the review whose question is *question_id* down, ending *ending*, as
    the bot; return the change's id, or None where nothing was asked.

    Nothing is asked where the review stands no longer, or where a take-down of it is in hand: the
    row says so (`TAKING_DOWN`), set before the ask so that two askers at once, a press and the
    timer, ask once. A fault in the ask puts the row back as it was. An earlier press that failed
    (`FAILED`) is carried to the lapse, which says so.
    """
    review = await standing_review(db_path, question_id)
    if review is None or review.press == TAKING_DOWN:
        return None
    if not await mark_press(db_path, question_id, TAKING_DOWN, only_from=(review.press,)):
        return None
    payload: dict[str, Any] = {
        "channel_id": review.channel_id, "question_id": review.question_id,
        "report_ids": list(review.report_ids), "reviewer_id": review.reviewer_id,
        "review": review.review, "ending": ending, "verb": review.verb,
    }
    if review.press == FAILED:
        payload["press_failed"] = True
    try:
        change_id = await queue.ask(
            TAKE_DOWN, payload, what="taking a season review down", origin=ChangeOrigin.BOT
        )
    except BaseException:
        await mark_press(db_path, question_id, review.press)
        raise
    return change_id


async def withdraw_take_down(
    queue: Any, db_path: str, question_id: int, change_id: int, *, back_to: str | None
) -> None:
    """Take back the take-down *change_id*, asked ahead of an approval the queue then refused at
    once: a request the queue refuses at once leaves the review standing (the core
    specification's "Confirming placements"). The row goes back to *back_to*, the state the press
    had put it in."""
    if await queue.withdraw(change_id):
        await mark_press(db_path, question_id, back_to)


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
            if await mark_press(db_path, review.question_id, TIMER_FIRED, only_from=(UNDER_WAY,)):
                return
        elif review.press == TIMER_FIRED:
            return
        await take_review_down(queue_of(), db_path, review.question_id, EXPIRED)

    return fired
