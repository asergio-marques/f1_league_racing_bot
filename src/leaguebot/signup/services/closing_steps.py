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

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import discord

from leaguebot.core.models.change import PlannedStep, StepFailedOnDiscord, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.signup_posting import post

SIGNUP_NOTICE = "signup_notice"
CLOSE_SIGNUP = "close_signup"
ARM_DELETION = "arm_deletion"
#: The kind of the change that closes one driver's signup channel on the driver's own lane.
KIND = "signup.closing"
#: The timer that deletes a held signup channel a day on.
DELETE_TIMER = "signup_channel_delete"
#: How long a closed signup channel is kept before it is deleted.
HOLD = timedelta(hours=24)

__all__ = [
    "ARM_DELETION", "CLOSE_SIGNUP", "DELETE_TIMER", "HOLD", "KIND", "SIGNUP_NOTICE",
    "closing_change", "closing_steps",
]


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
