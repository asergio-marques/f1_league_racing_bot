"""A season review on the change queue (#439, slice 6b, C92).

A season review is a report posted in the review's channel and, below it, the question with its
Approve button. This file holds the change that takes one down; the change that posts one joins
it with C93.

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
"""

from __future__ import annotations

import dataclasses
from typing import Any

import aiosqlite
import discord

from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services import standing_posts
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.batch_notice import send_notice
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import abandoned_line, name_of_member

TAKE_DOWN = "season.review.take_down"
DELETE_MESSAGE = "delete_message"
POST_EXPIRY = "post_expiry"
FORGET = "forget"
#: How a review ends: approved, left unanswered five minutes, found standing at a restart, or
#: its press refused upon a changed state.
CONFIRMED, EXPIRED, RESTART, REFUSED = "confirmed", "expired", "restart", "refused"

__all__ = [
    "CONFIRMED", "DELETE_MESSAGE", "EXPIRED", "FORGET", "POST_EXPIRY", "REFUSED", "RESTART",
    "TAKE_DOWN", "season_review_take_down_change",
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
