"""Turning the signup module off, carried out on the change queue (#439, slice 6b).

`/module disable signup` asks `module.off:signup`, which is signup's own as `module.off:results`
is results': where the window is open, the jobs of a close come first, in the order a close runs
them (`window_changes`, `closing_steps`) -- each driver still filling in the wizard returned and
told, the Sign Up button taken down, "Signups are now closed" posted -- and the last job,
`switch_off`, is one save: it cancels the close timer and every wizard's timers, forgets the
signup channel and the window beside it, brings the module's flag down and audits the switch-off.
A job Discord refuses stops the queue instead of being named and stepped over, and a Discard names
what was not done in the reply and the line.

With the window closed the change has the one job, `switch_off`. Which of the two the change has is
decided when it is asked, from the window the command read, and judged again as it comes up to
run: a window opened or closed since, or reopened on another button, refuses the switch-off and
nothing is changed, since the jobs the change planned would no longer be the ones that fit.

The permission overwrites the bot applied to the signup channel are cleared by the command, not
by this change: a permission, not a message (the module's design in `ModuleCog._disable_signup`).
The time slots and the three question settings are not in the configuration row and stand again
when the module is next enabled; the league's base role and driver role are core's and stand.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

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
from leaguebot.signup.services.closing_steps import closing_steps, window_close_steps
from leaguebot.signup.services.window_changes import (
    RETURN_DRIVERS,
    discarded,
    drivers_returned,
    failed_jobs,
    return_drivers_step,
)

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

KIND = module_off("signup")
SWITCH_OFF = "switch_off"
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

__all__ = ["ALREADY_DISABLED", "KIND", "SWITCH_OFF", "signup_off_change"]


def signup_off_change(
    *,
    modules: "ModuleService",
    signup_module_service: Callable[[], "SignupModuleService"],
    returned_by_close: Collection[DriverState],
    cancel_timers: Callable[[str], None],
    cancel_close_timer: Callable[[], None],
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
) -> ChangeType:
    """The `module.off:signup` change type, for the builder to register.

    The payload is ``window``: the Sign Up button message of the open window the command read, or
    None where the window was closed. *signup_module_service* is a getter, so that the service is
    the one the bot holds when the job runs. *returned_by_close* are the driver states the close
    turns away, *cancel_timers* removes one driver's inactivity and deletion timers,
    *cancel_close_timer* the window's close timer, and *now* and *arm_once* are the queue's clock
    and the scheduler service's one-off timer.
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
        return (PlannedStep(RETURN_DRIVERS, {"audit_action": AUDIT_FORCE_CLOSE}), PlannedStep(SWITCH_OFF))

    async def switch_off(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        cursor = await db.execute(
            "SELECT discord_user_id FROM signup_wizard_records WHERE wizard_state != 'UNENGAGED'"
        )
        wizards = [str(row["discord_user_id"]) for row in await cursor.fetchall()]
        cancel_close_timer()
        for account in wizards:
            cancel_timers(account)
        await signup_module_service().delete_config_on(db)
        await modules.set_signup_enabled_on(db, False)
        line = f"{ctx.named} | /module disable signup | Success"
        if ctx.payload["window"] is not None:
            line += f"\n  drivers_returned_to_not_signed_up: {drivers_returned(ctx.steps)}"
            line += "".join(f"\n  failed_step: {step}" for step in failed_jobs(ctx.steps))
        return StepResult(
            result={},
            audits=(AuditRecord("MODULE_DISABLE", {"module": "signup"}, {}),),
            lines=(line,),
            follow_ons=(
                FollowOn(
                    HUB_REFRESH,
                    {"command": "`/module disable`"},
                    f"Refreshing the hub panel after {ctx.what}",
                ),
            ),
        )

    async def describe_switch_off(_ctx: StepContext) -> str:
        return "turning the signup module off"

    def outcome(ctx: OutcomeContext) -> str:
        if any(discarded(view) for view in ctx.steps if view.name == SWITCH_OFF):
            return NOTHING_CHANGED
        text = SWITCHED_OFF
        failed = failed_jobs(ctx.steps)
        if failed:
            text += (
                "\n⚠️ The window is closed, but not every step succeeded:\n"
                + "\n".join(f"• {step}" for step in failed)
            )
        return text

    steps: dict[str, Step] = {
        **closing_steps(now=now, arm_once=arm_once),
        **window_close_steps(signup_module_service=signup_module_service, now=now),
        RETURN_DRIVERS: return_drivers_step(
            signup_module_service=signup_module_service, returned_by_close=returned_by_close,
            cancel_timers=cancel_timers,
        ),
        SWITCH_OFF: Step(SWITCH_OFF, StepKind.SAVE, switch_off, describe=describe_switch_off),
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
