"""Signup's own closing jobs, and the `signup.closing` change that runs them on a driver's lane.

A driver's signup channel is closed in three jobs, each built by a factory the builder registers
with what it needs handed it (and, where a manager's typed reason began the closing, a fourth
before them, `delete_reason`, which takes that message down): `signup_notice` tells the channel why it closes, mentioning the
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
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    FollowOn,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.posting import standing_posts
from leaguebot.core.services.queue.audit_service import record_system_change_on
from leaguebot.core.services.season_end_changes import season_ending_on
from leaguebot.core.services.season_lifecycle_service import (
    NO_CHANNEL_LEFT,
    advance_on_window_close_on,
    require_guild,
)
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    DiscardedContext,
    OutcomeContext,
    RefusedContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.signup_posting import post

if TYPE_CHECKING:
    from leaguebot.core.services.queue.change_queue import ChangeQueue
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.signup.services.signup_module_service import SignupModuleService
    from leaguebot.signup.services.wizard_service import WizardService

#: The job that deletes the reason a manager typed after Reject, ahead of the closing notice that
#: gives it.
DELETE_REASON = "delete_reason"
SIGNUP_NOTICE = "signup_notice"
CLOSE_SIGNUP = "close_signup"
ARM_DELETION = "arm_deletion"
DELETE_BUTTON = "delete_button"
POST_CLOSED = "post_closed"
#: The job that closes the window at a season's end, which core plans through its hook for the
#: close of what a module holds open.
CLOSE_WINDOW = "close_window"
#: What the signup channel of a driver is told where the signup window closes under it.
WINDOW_CLOSED_NOTICE = "🔒 Signups have closed. This channel will be automatically deleted in 24 hours."
#: What a close, a season's end or a wind-down says of a window it could not close. A close time
#: that was armed stays armed, and `/signup close` refuses while one is, so it is named first; it
#: refuses too while a season's end or wind-down is in hand on the queue, so that is named too.
WINDOW_NOT_CLOSED = (
    "The signup window could not be closed. Close it with `/signup close`. "
    "Where a close time is set, run `/signup close-time cancel` first, and where a season's end "
    "or wind-down is still on the queue, let it finish first, as `/signup close` is refused "
    "until then."
)
#: What a discarded deletion of the Sign Up button leaves for a league admin to do.
BUTTON_KEPT = "The Sign Up button could not be removed from the signup channel."
#: What the signup channel is left saying once the window has closed.
CLOSED_NOTICE = "🔒 Signups are now closed."
#: How a close nobody ran is audited and its line headed, by the cause the change gives it.
CLOSE_CAUSES: dict[str, tuple[str, str]] = {
    "season end": ("SIGNUP_SEASON_END_CLOSE", "🔒 Signups closed as the season ended"),
    "divisions done": (
        "SIGNUP_DIVISIONS_DONE_CLOSE", "🔒 Signups closed as every division is done"
    ),
    # The abort of a season being set up is a cause of its own for the words of the job, and is
    # recorded as a season's end is.
    "abort": ("SIGNUP_SEASON_END_CLOSE", "🔒 Signups closed as the season ended"),
}
#: The kind of the change that closes one driver's signup channel on the driver's own lane.
KIND = "signup.closing"
#: The kind of the change that takes down a manager's typed reason the signup it was typed for
#: has moved on from, on the driver's own lane.
REASON_KIND = "signup.reason.delete"
#: The timer that deletes a held signup channel a day on.
DELETE_TIMER = "signup_channel_delete"
#: The timer that ends a signup left unanswered for a day: a closing or a switch-off takes it away.
INACTIVITY_TIMER = "signup_inactivity"
#: How long a closed signup channel is kept before it is deleted.
HOLD = timedelta(hours=24)
#: The kind of the change that deletes a driver's held signup channel.
DELETE_KIND = "signup.channel.delete"
#: The kind of the change that ends a driver's signup, and the reason a day without an answer gives.
END = "signup.end"
EXPIRED = "expired"
#: The reason a signup ends when its signup channel is gone where its review panel was due.
CHANNEL_GONE = "channel_gone"
#: What a signup channel is told when its driver's 24 hours run out.
EXPIRED_NOTICE = (
    "⏰ Your signup session has expired due to inactivity. "
    "This channel will be automatically deleted in 24 hours."
)

#: The states of a driver whose signup is still in progress or in review: a season's end that
#: returns such a driver to Not Signed Up tells their signup channel and closes it.
_SIGNUP_IN_PROGRESS: frozenset[str] = frozenset({
    "PENDING_SIGNUP_COMPLETION",
    "PENDING_ADMIN_APPROVAL",
    "AWAITING_CORRECTION_PARAMETER",
    "PENDING_DRIVER_CORRECTION",
})

__all__ = [
    "ARM_DELETION", "BUTTON_KEPT", "CLOSED_NOTICE", "CLOSE_CAUSES", "CLOSE_SIGNUP", "CLOSE_WINDOW",
    "DELETE_BUTTON", "DELETE_REASON",
    "DELETE_TIMER",
    "CHANNEL_GONE", "DELETE_KIND", "END", "EXPIRED", "EXPIRED_NOTICE", "HOLD", "INACTIVITY_TIMER", "KIND",
    "POST_CLOSED", "REASON_KIND", "SIGNUP_NOTICE", "WINDOW_CLOSED_NOTICE", "WINDOW_NOT_CLOSED", "ask_channel_delete", "ask_expiry", "close_driver_signup",
    "closing_change", "closing_steps", "driver_returned_jobs", "drivers_returned_on", "post_driver_notice",
    "reason_delete_change", "season_end_closes", "season_end_steps", "take_reason_down", "window_close_jobs",
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


async def ask_channel_delete(queue: "ChangeQueue", db_path: str, user_id: str) -> int | None:
    """Ask the queue, as the bot, to delete the signup channel *user_id*'s wizard holds
    (`signup.channel.delete`): the hold's 24-hour timer's ask, on the driver's own lane, since a
    timer's deletion is the conversation's own. Gives the change's id, or None where the driver
    holds no channel."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_wizard_records WHERE discord_user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    if row is None or row["signup_channel_id"] is None:
        return None
    return await queue.ask(
        DELETE_KIND,
        {"user_id": user_id, "channel_id": int(row["signup_channel_id"])},
        what=f"deleting the signup channel of <@{user_id}>",
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

    async def describe_reason(ctx: StepContext) -> str:
        return f"deleting the reason typed in <#{ctx.step_payload['channel_id']}>"

    return {
        DELETE_REASON: dataclasses.replace(
            standing_posts.delete_replaced_step(DELETE_REASON), describe=describe_reason
        ),
        SIGNUP_NOTICE: Step(SIGNUP_NOTICE, StepKind.ACT, signup_notice, describe=describe_notice),
        CLOSE_SIGNUP: Step(CLOSE_SIGNUP, StepKind.ACT, close_signup, describe=describe_close),
        ARM_DELETION: Step(ARM_DELETION, StepKind.ACT, arm_deletion, describe=describe_arm),
    }


def driver_returned_jobs(driver: Mapping[str, Any], *, notice: str) -> tuple[PlannedStep, ...]:
    """The jobs signup plans for one driver a season's end returns to Not Signed Up: a signup still
    in progress or in review is told *notice* (`signup_notice`) and then has its channel closed
    (`close_signup`); any other driver needs nothing of signup. *driver* holds their ``user_id``
    and ``state`` (the one they were in). Subscribed to core's `driver_returned_jobs`."""
    if driver["state"] not in _SIGNUP_IN_PROGRESS:
        return ()
    user_id = str(driver["user_id"])
    return (
        PlannedStep(SIGNUP_NOTICE, {"user_id": user_id, "notice": notice}),
        PlannedStep(CLOSE_SIGNUP, {"user_id": user_id}),
    )


async def drivers_returned_on(
    signup_module_service: Callable[[], "SignupModuleService"],
    db: aiosqlite.Connection,
    drivers: Sequence[Mapping[str, Any]],
) -> None:
    """Mark over the signup wizard of each of *drivers* a season's end returns to Not Signed Up
    whose signup was in progress or in review, on the save's connection, committing nothing, so
    that a restart finds no wizard still engaged for a signup that has ended and tells nobody it
    expired. *drivers* are mappings of ``user_id`` and ``state``. Subscribed to core's
    `drivers_returned`; *signup_module_service* is a getter, so that it is the service the bot
    holds when the save runs."""
    for driver in drivers:
        if driver["state"] in _SIGNUP_IN_PROGRESS:
            await signup_module_service().end_wizard_on(db, str(driver["user_id"]))


def season_end_closes(*, cause: str, season_number: int | None = None) -> tuple[PlannedStep, ...]:
    """The job signup plans to close its window as a season ends (`close_window`, which does
    nothing where signup is off or its window is not open): *cause* is the season's end, the abort
    of the season being set up or the wind-down's close as every division is done, and
    *season_number* the season's, where it has one, for the words of the job. Subscribed to core's
    `season_end_closes`."""
    return (PlannedStep(CLOSE_WINDOW, {"cause": cause, "season_number": season_number}),)


async def post_driver_notice(
    guild: discord.Guild, driver: Mapping[str, Any], *, wizard_service: "WizardService", notice: str
) -> dict:
    """Tell one driver's signup channel *notice*, before anything is locked (the `signup_notice`
    job of a season's end).

    *driver* holds their ``user_id``. A refusal from Discord raises, for the queue to stop on with
    the channel still readable and writable; a wizard with no channel, or a channel gone, is
    nothing to tell and the job is done.
    """
    held = await wizard_service.trigger_channel_hold(
        str(driver["user_id"]), guild, notice, lock=False, raise_on_failure=True
    )
    return {"no_channel": True} if held.channel_id is None else {}


def cancel_signup_timeout(scheduler_service: "SchedulerService", user_id: str) -> None:
    """Remove one driver's inactivity timeout, and the record of its timer; one the scheduler no
    longer holds is nothing to remove."""
    # Imported here: the wizard service imports this module.
    from leaguebot.signup.services.wizard_service import inactivity_job_id

    scheduler_service.cancel_job(inactivity_job_id(user_id))
    scheduler_service.disarm(INACTIVITY_TIMER, user_id)


async def close_driver_signup(
    guild: discord.Guild,
    driver: Mapping[str, Any],
    *,
    wizard_service: "WizardService",
    scheduler_service: "SchedulerService",
) -> dict:
    """Close one driver's signup channel and cancel their inactivity timeout (the `close_signup`
    job of a season's end).

    The channel loses the driver's write access and its deletion is armed 24 hours on. It runs
    whether the `signup_notice` job before it went through or was discarded. A timeout the
    scheduler no longer holds is nothing to cancel.
    """
    user_id = str(driver["user_id"])
    await wizard_service.lock_signup_channel(user_id, guild)
    cancel_signup_timeout(scheduler_service, user_id)
    return {}


def season_end_steps(
    *,
    wizard_service: Callable[[], "WizardService"],
    scheduler_service: Callable[[], "SchedulerService"],
    line_prefix: str | None = None,
) -> dict[str, Step]:
    """The two jobs of one driver's signup channel at a season's end, by name: `signup_notice`
    tells the channel why it closes and `close_signup` locks it. A driver with no signup channel
    left is passed over by `signup_notice`, and `close_signup` then has nothing to lock or delete.
    The change's closing outcome names them; where the change has no closing line of its own,
    *line_prefix* ("System | Every division is done") has the notice job write one. Not subscribed
    itself: signup's `window_changes.season_end_steps`, which is, includes it.

    *wizard_service* and *scheduler_service* are getters, so that each is the one the bot holds
    when the job runs.
    """

    async def signup_notice(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        result = await post_driver_notice(
            guild, ctx.step_payload, wizard_service=wizard_service(),
            notice=str(ctx.step_payload["notice"]),
        )
        lines: tuple[str, ...] = ()
        if result.get("no_channel") and line_prefix:
            who = f"<@{ctx.step_payload['user_id']}>"
            lines = (f"{line_prefix} | {NO_CHANNEL_LEFT.format(who=who)}",)
        return StepResult(result=result, lines=lines)

    async def close_signup(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        user_id = str(ctx.step_payload["user_id"])
        if any(
            view.name == SIGNUP_NOTICE and view.done
            and str(view.payload.get("user_id")) == user_id
            and (view.result or {}).get("no_channel")
            for view in ctx.steps
        ):
            # Nothing to lock or delete: the notice found no channel left.
            cancel_signup_timeout(scheduler_service(), user_id)
            return StepResult(result={"no_channel": True})
        return StepResult(result=await close_driver_signup(
            guild, ctx.step_payload, wizard_service=wizard_service(),
            scheduler_service=scheduler_service(),
        ))

    async def describe_notice(ctx: StepContext) -> str:
        return f"telling <@{ctx.step_payload['user_id']}> that their signup has closed"

    async def describe_close(ctx: StepContext) -> str:
        return f"closing the signup of <@{ctx.step_payload['user_id']}>"

    return {
        SIGNUP_NOTICE: Step(SIGNUP_NOTICE, StepKind.ACT, signup_notice, describe=describe_notice),
        CLOSE_SIGNUP: Step(CLOSE_SIGNUP, StepKind.ACT, close_signup, describe=describe_close),
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
    season on where it says ``advance_season`` (a season's end moves its own, and a season whose
    end, or a wind-down that would run, is in hand on the queue is left to it, `season_ending_on`):
    no window is ever recorded closed without its notice up, and a refused post stops the queue
    with signups still open.
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
        # A season whose end or wind-down is in hand on the queue is left to it, which moves it
        # its own way.
        if ctx.step_payload.get("advance_season") and not await season_ending_on(db):
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
    that has ended. The payload is ``user_id``, ``channel_id`` and ``notice``, and
    ``reason_message_id`` where a manager's typed reason (after Reject) is to come down first: the
    notice gives the reason, so the message is deleted ahead of it."""

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {"user_id": str(payload["user_id"]), "channel_id": int(payload["channel_id"])}
        planned: list[PlannedStep] = []
        if payload.get("reason_message_id") is not None:
            planned.append(PlannedStep(DELETE_REASON, {
                "channel_id": shared["channel_id"],
                "message_id": int(payload["reason_message_id"]), "what": "reason message",
            }))
        planned += [
            PlannedStep(SIGNUP_NOTICE, {**shared, "notice": str(payload["notice"])}),
            PlannedStep(CLOSE_SIGNUP, shared),
            PlannedStep(ARM_DELETION, shared),
        ]
        return tuple(planned)

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


def take_reason_down(context: RefusedContext | DiscardedContext) -> tuple[FollowOn, ...]:
    """The change's `on_refused` and `on_discarded` for a rejection or a request for changes: the
    manager's reason, typed in the driver's channel (``reason_channel_id`` and
    ``reason_message_id`` of the payload), is taken down by `signup.reason.delete` when the press
    is refused as it comes up to run, or discarded at its check before it started, so that a
    reason the change will never take down does not stay where the driver can read it. A payload
    that carries no reason (an expiry, a withdrawal) asks nothing."""
    channel_id = context.payload.get("reason_channel_id")
    message_id = context.payload.get("reason_message_id")
    if channel_id is None or message_id is None:
        return ()
    return (FollowOn(
        REASON_KIND,
        {"channel_id": int(channel_id), "message_id": int(message_id)},
        "taking down a manager's reason",
    ),)


def reason_delete_change() -> ChangeType:
    """The `signup.reason.delete` change type, for the builder to register, on the driver's own lane
    (``channel:<signup channel>``): the manager's reason, typed after Reject or Request Changes,
    for a signup that has moved on since the press. The press was refused and nothing else is
    done, so the one job `delete_reason` takes the message down, as the closing and the correction
    choices do for a reason that was taken. The payload is ``channel_id`` and ``message_id``; a
    message already gone is nothing to do, and one Discord refuses to delete stops the driver's
    lane."""

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (PlannedStep(DELETE_REASON, {
            "channel_id": int(payload["channel_id"]),
            "message_id": int(payload["message_id"]), "what": "reason message",
        }),)

    async def standing(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    async def describe_reason(ctx: StepContext) -> str:
        return f"deleting the reason typed in <#{ctx.step_payload['channel_id']}>"

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=REASON_KIND,
        opening=(),
        plan=plan,
        steps={
            DELETE_REASON: dataclasses.replace(
                standing_posts.delete_replaced_step(DELETE_REASON), describe=describe_reason
            ),
        },
        check=standing,
        key=lambda payload: f"{REASON_KIND}:{payload['message_id']}",
        doing=lambda payload: "Taking down a manager's reason",
        outcome=outcome,
        lane=lambda payload: f"channel:{int(payload['channel_id'])}",
        writes=(),
    )
