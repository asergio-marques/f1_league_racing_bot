"""Turning the signup module off, carried out on the change queue (#439, slice 6b).

`/module disable signup` asks `module.off:signup`, which is signup's own as `module.off:results`
is results': where the window is open, the jobs of a close come first, in the order a close runs
them (`window_changes`, `closing_steps`) -- each driver still filling in the wizard returned and
told, the Sign Up button taken down, "Signups are now closed" posted -- and the last job,
`switch_off`, is one save: it cancels the close timer and every wizard's timers, forgets the
signup channel and the window beside it, brings the module's flag down and audits the switch-off;
where the window was not recorded closed (the closed notice or the drivers' return was discarded)
it also moves the season on, as the closed notice's record would have. A job Discord refuses stops
the queue instead of being named and stepped over, and a Discard names what was not done in the
reply and the line. Where the window was open the line is written by a job of its own after the
switch-off, so that a switch-off discarded after the close's jobs ran still leaves a line saying
what they did.

With the window closed the change has the one job, `switch_off`. Which of the two the change has is
decided when it is asked, from the window the command read, and judged again as it comes up to
run: a window opened or closed since, or reopened on another button, refuses the switch-off and
nothing is changed, since the jobs the change planned would no longer be the ones that fit.

The permission overwrites the bot applied to the signup channel are cleared by a job of this
change, `clear_overwrites`, planned by the switch-off once it has turned the module off. They go
only with the module: a switch-off that is refused when it runs, waits behind a stopped queue or
is discarded leaves the channel as the module needs it (visible to the league's roles alone, the
bot able to post in it), which the signup specification requires while the module is on. A
permission Discord refuses to clear is logged and stepped over: a channel with a stale overwrite is
put right by hand, a module left half-off is not.
The time slots and the three question settings are not in the configuration row and stand again
when the module is next enabled; the league's base role and driver role are core's and stand.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.models.change import (
    AuditRecord,
    FollowOn,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
    module_off,
)
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.hub_service import HUB_REFRESH
from leaguebot.core.services.season_lifecycle_service import advance_on_window_close_on
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.closing_steps import POST_CLOSED, closing_steps, window_close_steps
from leaguebot.signup.services.window_changes import (
    RETURN_DRIVERS,
    discarded,
    drivers_returned,
    failed_jobs,
    return_drivers_step,
    window_was_closed,
)

if TYPE_CHECKING:
    from leaguebot.core.models.server_config import ServerConfig
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

log = logging.getLogger(__name__)

KIND = module_off("signup")
SWITCH_OFF = "switch_off"
CLEAR_OVERWRITES = "clear_overwrites"
OFF_LINE = "off_line"
#: How the close this change runs is audited: a close a member's switch-off forced.
AUDIT_FORCE_CLOSE = "SIGNUP_FORCE_CLOSE"
ALREADY_DISABLED = "⚠️ Signup module is already disabled."
WINDOW_CHANGED = (
    "⛔ The signup window changed since this was asked. Nothing was changed. "
    "Run `/module disable signup` again."
)
NOTHING_CHANGED = "Nothing was changed: the signup module is still on."
SWITCHED_OFF = (
    "✅ Signup module disabled. Its channel has been cleared; its time slots and "
    "question settings are kept, and so are the league's base role and driver role."
)

#: What a close that went wrong in the signup channel leaves, one sentence each, for the reply and
#: the line.
CLOSED_NOT_POSTED = "The closed notice could not be posted in the signup channel."
BUTTON_LEFT = (
    "The Sign Up button was left standing in the signup channel and no closed notice was posted: "
    "delete the button by hand."
)
NOT_TURNED_OFF = (
    "The signup module could not be turned off. Run `/module disable signup` again."
)

__all__ = [
    "ALREADY_DISABLED", "CLEAR_OVERWRITES", "KIND", "OFF_LINE", "SWITCH_OFF", "signup_off_change",
]


def _not_done(steps: Any) -> list[str]:
    """What a close's discarded jobs left undone, in the order they ran, and the two things
    `failed_jobs` does not say for a switch-off: the closed notice not posted, and the Sign Up
    button left standing where the drivers' return was discarded and nothing after it was planned."""
    failed = failed_jobs(steps)
    if any(discarded(view) for view in steps if view.name == RETURN_DRIVERS):
        failed.append(BUTTON_LEFT)
    elif any(discarded(view) for view in steps if view.name == POST_CLOSED):
        failed.append(CLOSED_NOT_POSTED)
    return failed


def signup_off_change(
    *,
    modules: "ModuleService",
    signup_module_service: Callable[[], "SignupModuleService"],
    returned_by_close: Collection[DriverState],
    cancel_timers: Callable[[str], None],
    cancel_close_timer: Callable[[], None],
    server_config: Callable[[], Awaitable["ServerConfig | None"]],
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
) -> ChangeType:
    """The `module.off:signup` change type, for the builder to register.

    The payload is ``window``: the Sign Up button message of the open window the command read, or
    None where the window was closed. *signup_module_service* is a getter, so that the service is
    the one the bot holds when the job runs. *returned_by_close* are the driver states the close
    turns away, *cancel_timers* removes one driver's inactivity and deletion timers,
    *cancel_close_timer* the window's close timer, *server_config* reads the league's roles for the
    overwrites to clear, and *now* and *arm_once* are the queue's clock and the scheduler
    service's one-off timer.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_signup_enabled():
            return Verdict.refuse(ALREADY_DISABLED)
        cfg = await signup_module_service().get_config()
        window = cfg.signup_button_message_id if cfg is not None and cfg.signups_open else None
        if window != ctx.payload["window"]:
            return Verdict.refuse(WINDOW_CHANGED)
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        if payload["window"] is None:
            return (PlannedStep(SWITCH_OFF),)
        return (
            PlannedStep(RETURN_DRIVERS, {"audit_action": AUDIT_FORCE_CLOSE}),
            PlannedStep(SWITCH_OFF),
            PlannedStep(OFF_LINE),
        )

    async def switch_off(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        cursor = await db.execute(
            "SELECT discord_user_id FROM signup_wizard_records WHERE wizard_state != 'UNENGAGED'"
        )
        wizards = [str(row["discord_user_id"]) for row in await cursor.fetchall()]
        cursor = await db.execute("SELECT signup_channel_id FROM signup_module_config WHERE id = 1")
        config = await cursor.fetchone()
        channel_id = None if config is None else config["signup_channel_id"]
        cancel_close_timer()
        for account in wizards:
            cancel_timers(account)
        window_open = ctx.payload["window"] is not None
        if window_open and not window_was_closed(ctx.steps):
            # The closed notice's record moves the season on; without it the module's going off
            # must, or the season is left in a stage waiting on a window nothing can close.
            await advance_on_window_close_on(db)
        await signup_module_service().delete_config_on(db)
        await db.execute("UPDATE server_configs SET signup_module_enabled = 0")
        return StepResult(
            result={},
            audits=(AuditRecord("MODULE_DISABLE", {"module": "signup"}, {}),),
            lines=() if window_open else (f"{ctx.named} | /module disable signup | Success",),
            then=(
                () if channel_id is None
                else (PlannedStep(CLEAR_OVERWRITES, {"channel_id": int(channel_id)}),)
            ),
            follow_ons=(
                FollowOn(
                    HUB_REFRESH,
                    {"command": "`/module disable`"},
                    f"Refreshing the hub panel after {ctx.what}",
                ),
            ),
        )

    async def clear_overwrites(ctx: StepContext) -> StepResult:
        """Clear what `/signup channel` applied to the signup channel: everyone's, the bot's, the
        base role's and the interaction role's (the admin role's allow is left, #372). Each is
        tried on its own and a refusal logged, never raised."""
        guild = await league_guild(ctx.bot)
        channel = None if guild is None else guild.get_channel(int(ctx.step_payload["channel_id"]))
        if guild is None or not isinstance(channel, discord.TextChannel):
            return StepResult()
        targets: list[discord.Role | discord.Member] = [guild.default_role, guild.me]
        league = await server_config()
        if league is not None:
            # The base role is the league's and outlives the module (issue #276): only its
            # overwrite on this channel goes.
            if league.base_role_id is not None:
                base_role = guild.get_role(league.base_role_id)
                if base_role:
                    targets.append(base_role)
            interaction_role = guild.get_role(league.interaction_role_id)
            if interaction_role:
                targets.append(interaction_role)
        for target in targets:
            try:
                await channel.set_permissions(target, overwrite=None)
            except Exception:
                log.exception("disable_signup: could not clear overwrite for %s", target)
        return StepResult()

    async def off_line(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one line of a switch-off that closed the window: what the close's jobs did, and
        beneath it each job a league admin discarded, the switch-off itself among them."""
        refused = any(discarded(view) for view in ctx.steps if view.name == SWITCH_OFF)
        failed = _not_done(ctx.steps)
        if refused:
            failed.append(NOT_TURNED_OFF)
        line = (
            f"{ctx.named} | /module disable signup | {'Incomplete' if refused else 'Success'}"
            f"\n  drivers_returned_to_not_signed_up: {drivers_returned(ctx.steps)}"
            + "".join(f"\n  failed_step: {step}" for step in failed)
        )
        return StepResult(result={}, lines=(line,))

    async def describe_switch_off(_ctx: StepContext) -> str:
        return "turning the signup module off"

    async def describe_clear(_ctx: StepContext) -> str:
        return "clearing the signup channel's permissions"

    async def describe_line(_ctx: StepContext) -> str:
        return "recording the switch-off of the signup module"

    def outcome(ctx: OutcomeContext) -> str:
        failed = _not_done(ctx.steps)
        bullets = "".join(f"\n• {step}" for step in failed)
        if any(discarded(view) for view in ctx.steps if view.name == SWITCH_OFF):
            if ctx.payload["window"] is None:
                return NOTHING_CHANGED
            closed = window_was_closed(ctx.steps)
            text = (
                "⚠️ The signup module is still on: turning it off was discarded. "
                + (
                    f"Signups were closed first, returning {drivers_returned(ctx.steps)} "
                    "driver(s) to Not Signed Up."
                    if closed else "Signups were not closed."
                )
            )
            return text + bullets
        text = SWITCHED_OFF
        if failed:
            lead = "The window is closed" if window_was_closed(ctx.steps) else "The module is off"
            text += f"\n⚠️ {lead}, but not every step succeeded:" + bullets
        return text

    steps: dict[str, Step] = {
        **closing_steps(now=now, arm_once=arm_once),
        **window_close_steps(signup_module_service=signup_module_service, now=now),
        RETURN_DRIVERS: return_drivers_step(
            signup_module_service=signup_module_service, returned_by_close=returned_by_close,
            cancel_timers=cancel_timers,
        ),
        SWITCH_OFF: Step(SWITCH_OFF, StepKind.SAVE, switch_off, describe=describe_switch_off),
        CLEAR_OVERWRITES: Step(
            CLEAR_OVERWRITES, StepKind.ACT, clear_overwrites, describe=describe_clear
        ),
        OFF_LINE: Step(OFF_LINE, StepKind.SAVE, off_line, describe=describe_line),
    }
    return ChangeType(
        kind=KIND,
        opening=(),
        plan=plan,
        steps=steps,
        check=check,
        key=lambda payload: KIND,
        doing=lambda payload: "Turning the signup module off",
        outcome=outcome,
    )
