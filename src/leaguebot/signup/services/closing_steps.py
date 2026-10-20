"""Signup's own closing jobs, and the `signup.closing` change that runs them on a driver's lane.

A driver's signup channel is closed in three jobs, each built by a factory the builder registers
with what it needs handed it: `signup_notice` tells the channel why it closes, mentioning the
driver and no one else; `close_signup` takes the driver's typing from it; `arm_deletion` arms the
channel's deletion a day on (the `signup_channel_delete` timer, keyed by the driver's account).
The notice goes first, so a driver is never locked out of a channel that says nothing: where
Discord refuses it, the job stops its lane with nothing locked and nothing armed, and a Retry finds
the channel as it was.

The jobs are usable on either lane (docs/wip-specs/core_specification.md, "How a change is
carried out"). A league change that closes signups (a command or a timer) runs them itself; the
`signup.closing` change is what a league change that ended a driver's signup (Withdraw, Approve, a
Reject reason) asks back of the driver's own lane, so that a refused notice stops that driver's
lane alone. It needs only its channel to stand, not the signup it closes: that has ended.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services import standing_posts
from leaguebot.core.services.audit_service import record_system_change_on
from leaguebot.core.services.season_lifecycle_service import advance_on_window_close_on
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.signup_posting import post

if TYPE_CHECKING:
    from leaguebot.core.services.change_queue import ChangeQueue
    from leaguebot.signup.services.signup_module_service import SignupModuleService

SIGNUP_NOTICE = "signup_notice"
CLOSE_SIGNUP = "close_signup"
ARM_DELETION = "arm_deletion"
DELETE_BUTTON = "delete_button"
POST_CLOSED = "post_closed"
#: What the signup channel is left saying once the window has closed.
CLOSED_NOTICE = "🔒 Signups are now closed."
#: How a close nobody ran is audited and its line headed, by the cause the change gives it.
CLOSE_CAUSES: dict[str, tuple[str, str]] = {
    "season end": ("SIGNUP_SEASON_END_CLOSE", "🔒 Signups closed as the season ended"),
    "divisions done": (
        "SIGNUP_DIVISIONS_DONE_CLOSE", "🔒 Signups closed as every division is done"
    ),
}
#: The kind of the change that closes one driver's signup channel on the driver's own lane.
KIND = "signup.closing"
#: The timer that deletes a held signup channel a day on.
DELETE_TIMER = "signup_channel_delete"
#: The timer that ends a signup left unanswered for a day: a closing or a switch-off takes it away.
INACTIVITY_TIMER = "signup_inactivity"
#: How long a closed signup channel is kept before it is deleted.
HOLD = timedelta(hours=24)
#: The kind of the change that ends a driver's signup, and the reason a day without an answer gives.
END = "signup.end"
EXPIRED = "expired"
#: What a signup channel is told when its driver's 24 hours run out.
EXPIRED_NOTICE = (
    "⏰ Your signup session has expired due to inactivity. "
    "This channel will be automatically deleted in 24 hours."
)

__all__ = [
    "ARM_DELETION", "CLOSED_NOTICE", "CLOSE_CAUSES", "CLOSE_SIGNUP", "DELETE_BUTTON", "DELETE_TIMER",
    "END", "EXPIRED", "EXPIRED_NOTICE", "HOLD", "INACTIVITY_TIMER", "KIND", "POST_CLOSED",
    "SIGNUP_NOTICE", "ask_expiry", "closing_change", "closing_steps", "window_close_jobs",
    "window_close_steps",
]


async def ask_expiry(queue: "ChangeQueue", db_path: str, user_id: str) -> int | None:
    """Ask the queue, as the bot, for the expiry of *user_id*'s signup (`signup.end` for the
    reason `EXPIRED`): the 24-hour timer's ask, and the restart's for a deadline that passed while
    the bot was down. Gives the change's id, or None where the driver has no wizard or the
    expiry is not due (the change's check judges that, and drops it)."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_wizard_records WHERE discord_user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return await queue.ask(
        END,
        {"user_id": user_id, "reason": EXPIRED, "channel_id": row["signup_channel_id"]},
        what=f"ending the signup of <@{user_id}>, left unanswered for a day",
        origin=ChangeOrigin.BOT,
    )


def closing_steps(*, now: Callable[[], datetime], arm_once: Callable[[str, str, datetime], None]) -> dict[str, Step]:
    """The three jobs of one driver's signup channel's closing, named as above.

    Each reads ``user_id`` and ``channel_id`` from its payload, and the notice its ``notice``. A
    channel gone from the server is nothing to tell, lock or delete: `signup_notice` says so, and
    the two after it find nothing to do. *now* and *arm_once* are the queue's clock and the
    scheduler service's one-off timer.
    """

    async def _channel(ctx: StepContext) -> discord.TextChannel | None:
        guild = await league_guild(ctx.bot)
        found = None if guild is None else guild.get_channel(int(ctx.step_payload["channel_id"]))
        return found if isinstance(found, discord.TextChannel) else None

    def _passed_over(ctx: StepContext) -> bool:
        return any(
            view.name == SIGNUP_NOTICE and view.done
            and str(view.payload.get("user_id")) == str(ctx.step_payload["user_id"])
            and (view.result or {}).get("no_channel")
            for view in ctx.steps
        )

    async def signup_notice(ctx: StepContext) -> StepResult:
        channel = await _channel(ctx)
        if channel is None:
            return StepResult(result={"no_channel": True})
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(id=int(ctx.step_payload["user_id"]))]
        )
        await post(
            channel, str(ctx.step_payload["notice"]), mentions=mentions,
            refused=f"the closing notice could not be posted in channel {channel.id}",
        )
        return StepResult(result={})

    async def close_signup(ctx: StepContext) -> StepResult:
        if _passed_over(ctx):
            return StepResult(result={"no_channel": True})
        channel = await _channel(ctx)
        member = None if channel is None else channel.guild.get_member(int(ctx.step_payload["user_id"]))
        if channel is not None and member is not None:
            try:
                await channel.set_permissions(
                    member, view_channel=True, send_messages=False,
                    reason="Signup wizard channel hold",
                )
            except discord.HTTPException as error:
                raise StepFailedOnDiscord(
                    f"the signup channel {channel.id} could not be locked"
                ) from error
        return StepResult(result={})

    async def arm_deletion(ctx: StepContext) -> StepResult:
        if _passed_over(ctx):
            return StepResult(result={"no_channel": True})
        arm_once(DELETE_TIMER, str(ctx.step_payload["user_id"]), now() + HOLD)
        return StepResult(result={})

    async def describe_notice(ctx: StepContext) -> str:
        return f"telling <@{ctx.step_payload['user_id']}> that their signup has closed"

    async def describe_close(ctx: StepContext) -> str:
        return f"closing the signup of <@{ctx.step_payload['user_id']}>"

    async def describe_arm(ctx: StepContext) -> str:
        return f"arming the deletion of <@{ctx.step_payload['user_id']}>'s signup channel"

    return {
        SIGNUP_NOTICE: Step(SIGNUP_NOTICE, StepKind.ACT, signup_notice, describe=describe_notice),
        CLOSE_SIGNUP: Step(CLOSE_SIGNUP, StepKind.ACT, close_signup, describe=describe_close),
        ARM_DELETION: Step(ARM_DELETION, StepKind.ACT, arm_deletion, describe=describe_arm),
    }


def window_close_jobs(
    cause: str, returned: int, *, channel_id: int | None, button_message_id: int | None
) -> tuple[PlannedStep, ...]:
    """The jobs that close the signup window in its channel once a close nobody ran has returned
    its drivers: the Sign Up button's deletion where one stands, then the "Signups are now
    closed" post, whose record closes the window. *cause* is a key of `CLOSE_CAUSES`, *returned*
    how many drivers the close returned, for the line the post's record writes."""
    audit_action, head = CLOSE_CAUSES[cause]
    planned: list[PlannedStep] = []
    if button_message_id is not None and channel_id is not None:
        planned.append(PlannedStep(DELETE_BUTTON, {
            "channel_id": channel_id, "message_id": button_message_id,
            "what": "Sign Up button", "division_name": "signups",
        }))
    planned.append(PlannedStep(POST_CLOSED, {
        "channel_id": channel_id, "audit_action": audit_action, "head": head, "returned": returned,
    }))
    return tuple(planned)


def window_close_steps(
    *, signup_module_service: Callable[[], "SignupModuleService"], now: Callable[[], datetime]
) -> dict[str, Step]:
    """The two jobs that close the window in its channel, for a change that ends signups.

    `delete_button` takes the Sign Up button down (a button already gone completes it). `post_closed`
    posts "Signups are now closed" in the signup channel, and in the save that marks it done its
    record closes the window with the post's id (so the next opening deletes the notice), audits
    the close and has the queue write the "Signups closed" line, where the payload gives a
    ``head`` for it (a close a member ran writes its own line in its last job), and moves the
    season on where it says ``advance_season`` (a season's end moves its own): no window is ever
    recorded closed without its notice up, and a refused post stops the queue with signups still
    open.
    Where its try posted and the record then failed, the message is kept for the next try to remove
    before it posts again. A window already closed by another hand is not closed twice.
    *signup_module_service* is a getter, so that the service is the one the bot holds when the job runs.
    """

    async def post_closed(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        guild = await league_guild(ctx.bot)
        found = None if guild is None or ctx.step_payload.get("channel_id") is None else (
            guild.get_channel(int(ctx.step_payload["channel_id"]))
        )
        if not isinstance(found, discord.TextChannel):
            raise StepFailedOnDiscord("the signup channel could not be found to post in")
        message_id = await post(
            found, CLOSED_NOTICE, mentions=discord.AllowedMentions.none(),
            refused=f"the closed notice could not be posted in channel {found.id}",
        )
        payload = ctx.step_payload
        lines: tuple[str, ...] = ()
        if "head" in payload:
            lines = (
                f"{payload['head']}\n  drivers_returned_to_not_signed_up: {payload['returned']}",
            )
        return StepResult(result={"new": message_id, "channel_id": found.id}, lines=lines)

    async def record(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        await signup_module_service().set_window_closed_on(
            db, closed_msg_id=int((result.result or {})["new"])
        )
        await record_system_change_on(
            db, change_type=str(ctx.step_payload["audit_action"]), old_value="open",
            new_value="closed", now=now(),
        )
        if ctx.step_payload.get("advance_season"):
            await advance_on_window_close_on(db)

    async def window_stands(_ctx: StepContext) -> bool:
        return await signup_module_service().get_window_state()

    async def describe_closed(_ctx: StepContext) -> str:
        return "closing the signup window and telling its channel"

    async def describe_button(_ctx: StepContext) -> str:
        return "taking the Sign Up button down"

    button = dataclasses.replace(
        standing_posts.delete_replaced_step(DELETE_BUTTON), describe=describe_button
    )
    return {
        DELETE_BUTTON: button,
        POST_CLOSED: Step(
            POST_CLOSED, StepKind.ACT, post_closed, still_due=window_stands,
            describe=describe_closed, record=record,
        ),
    }


def closing_change(
    *, now: Callable[[], datetime], arm_once: Callable[[str, str, datetime], None]
) -> ChangeType:
    """The `signup.closing` change type, for the builder to register, on the driver's lane
    (``channel:<signup channel>``): the notice, the lock and the deletion's arming of a signup
    that has ended. The payload is ``user_id``, ``channel_id`` and ``notice``."""

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {"user_id": str(payload["user_id"]), "channel_id": int(payload["channel_id"])}
        return (
            PlannedStep(SIGNUP_NOTICE, {**shared, "notice": str(payload["notice"])}),
            PlannedStep(CLOSE_SIGNUP, shared),
            PlannedStep(ARM_DELETION, shared),
        )

    async def standing(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=KIND,
        opening=(),
        plan=plan,
        steps=closing_steps(now=now, arm_once=arm_once),
        check=standing,
        key=lambda payload: f"{KIND}:{payload['user_id']}",
        doing=lambda payload: f"Closing the signup of <@{payload['user_id']}>",
        outcome=outcome,
        lane=lambda payload: f"channel:{int(payload['channel_id'])}",
        writes=(),
    )
