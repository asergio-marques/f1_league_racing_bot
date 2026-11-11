"""The jobs signup plans for a held signup channel as its driver's account changes.

Core's *driver_account_changed* hook hands signup the two accounts inside the save that changes
them, where `signup_module_service.driver_account_changed_on` re-keys the wizard record and plans
the jobs, which core runs after the save; the steps here, handed back through the companion hook
*driver_account_changed_steps*, are what they run:

- `MOVE_HELD_CHANNEL` moves the channel's deletion timer to the new account, kept at the moment it
  was already due (a day after the change where none stands), and the channel's permissions: the
  new account may read it and not write, and the replaced account's own permission is removed. A
  timer is moved before the permissions, so that the channel is never left with nothing to delete
  it; run again after the timer has moved, it keeps the new account's. Discord refusing the
  permissions ends the job with the problem named, for the change's reply and line, and the queue
  goes on. Any other fault stops the lane.
- `DELETE_REPLACED_CHANNEL` deletes the replaced account's channel where the new account holds
  one of its own, its wizard record with it, and takes the replaced account's timer away.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from leaguebot.core.models.change import GuildUnavailable, StepKind, StepResult
from leaguebot.core.services.queue.change_types import Step, StepContext
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.interaction_errors import describe_fault
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.closing_steps import DELETE_TIMER, HOLD
from leaguebot.signup.services.signup_module_service import (
    DELETE_REPLACED_CHANNEL,
    MOVE_HELD_CHANNEL,
)
from leaguebot.signup.services.wizard_changes import delete_signup_channel

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.signup.services.signup_module_service import SignupModuleService


def held_channel_steps(
    *,
    scheduler_service: Callable[[], "SchedulerService"],
    signup: Callable[[], "SignupModuleService"],
    now: Callable[[], datetime],
) -> dict[str, Step]:
    """The two jobs, by name; see the module. Subscribed to core's *driver_account_changed_steps*;
    the services are getters, so that each is the one the bot holds when the job runs, and *now*
    is the queue's clock."""

    async def move(ctx: StepContext) -> StepResult:
        each = ctx.step_payload
        replaced, new = str(each["from_account"]), str(each["to_account"])
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        scheduler = scheduler_service()
        due = (
            scheduler.timer_due(DELETE_TIMER, replaced)
            or scheduler.timer_due(DELETE_TIMER, new)
            or now() + HOLD
        )
        scheduler.arm_once(DELETE_TIMER, new, due)
        scheduler.disarm(DELETE_TIMER, replaced)
        channel = as_text_channel(guild.get_channel(int(each["channel_id"])))
        if channel is None:
            return StepResult(result={})
        try:
            new_member = guild.get_member(int(new))
            if new_member is not None:
                await channel.set_permissions(
                    new_member, view_channel=True, send_messages=False,
                    reason="Signup wizard channel hold",
                )
            old_member = guild.get_member(int(replaced))
            if old_member is not None:
                await channel.set_permissions(
                    old_member, overwrite=None, reason="Driver's current account changed"
                )
        except discord.HTTPException as error:
            return StepResult(result={"problem": (
                f"the signup channel's permissions could not be moved: {describe_fault(error)}"
            )})
        return StepResult(result={})

    async def describe_move(ctx: StepContext) -> str:
        return f"moving the signup channel of <@{ctx.step_payload['from_account']}>"

    async def delete(ctx: StepContext) -> StepResult:
        done = await delete_signup_channel(ctx, "Driver's current account changed")
        scheduler_service().disarm(DELETE_TIMER, str(ctx.step_payload["user_id"]))
        return done

    async def held(ctx: StepContext) -> bool:
        row = await signup().get_wizard(str(ctx.step_payload["user_id"]))
        return row is not None and row.signup_channel_id == int(ctx.step_payload["channel_id"])

    async def forget(db: "aiosqlite.Connection", ctx: StepContext, _result: StepResult) -> None:
        await signup().delete_wizard_on(db, str(ctx.step_payload["user_id"]))

    async def describe_delete(ctx: StepContext) -> str:
        return f"deleting the signup channel <#{ctx.step_payload['channel_id']}>"

    return {
        MOVE_HELD_CHANNEL: Step(
            MOVE_HELD_CHANNEL, StepKind.ACT, move, describe=describe_move
        ),
        DELETE_REPLACED_CHANNEL: Step(
            DELETE_REPLACED_CHANNEL, StepKind.DELETE, delete, still_due=held,
            describe=describe_delete, record=forget,
        ),
    }


__all__ = ["held_channel_steps"]
