"""A signup's review on the change queue (#439, slice 6b, C84).

A league manager reviews a completed signup in the driver's signup channel. Each thing the review
does to the driver's status is a change of the league lane, asked for by the manager's press
(core specification, "How a change is carried out"; decision 22, "every change of a driver's
status is the league's").

**Approve** asks `signup.approve`. Its check refuses a signup that is no longer awaiting approval
(another manager acted first, the driver withdrew) and a league whose signup configuration is
gone, telling the manager and changing nothing, both when it is asked and again as it comes up to
run. Its jobs are:

- `approve` (a save) writes the sum of the driver's lap times on their signup record, moves the
  driver to Unassigned, marks the signup approved and takes away the timers it leaves, and asks
  back `signup.closing` on the driver's own lane for the notice, the lock and the channel's
  deletion a day on, so that a notice Discord refuses stops that driver's lane alone and the
  league's change is done;
- `grant_role` gives the driver the league's driver role and writes the "Approved" line. A role it
  cannot give (it is gone, the driver has left the server, Discord refuses it) is **named, never
  a stop**: the approval stands, and the manager's reply and the line say so (signup specification,
  the approval's role rule).

**The lap sum is signup's own.** The sum is written on the signup record by this module, in the
approval's save; core's `PlacementService.store_total_lap_ms`, which commits on a connection of
its own, is not asked.

A job that fails stops the queue (core specification, "How a change is carried out").
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.models.change import (
    FollowOn,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.input_validator import parse_time
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import name_of_member
from leaguebot.signup.services.closing_steps import KIND as CLOSING

if TYPE_CHECKING:
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

APPROVE = "signup.approve"
APPROVE_JOB = "approve"
GRANT_ROLE = "grant_role"

#: What a manager is told whose press finds the signup already actioned.
ACTIONED = "This signup has already been actioned."
#: What the driver's channel is told when their signup is approved.
APPROVED_NOTICE = (
    "✅ Your signup has been approved by **{manager}**! You are now an Unassigned driver. "
    "This channel will be automatically deleted in 24 hours."
)

__all__ = ["ACTIONED", "APPROVE", "APPROVED_NOTICE", "approve_change", "total_lap_ms"]


def total_lap_ms(lap_times: dict[str, str]) -> int | None:
    """The sum of a driver's signup lap times in milliseconds, or None where they gave none or
    one cannot be read: the value a placement is seeded from, so that a league that asked for no
    times seeds every driver alike by storing nothing rather than a zero."""
    if not lap_times:
        return None
    total = 0
    for text in lap_times.values():
        milliseconds = parse_time(text)
        if milliseconds is None:
            return None
        total += milliseconds
    return total if total > 0 else None


def approve_change(
    *,
    signup: Callable[[], "SignupModuleService"],
    drivers: Callable[[], "DriverService"],
    config: "ConfigService",
    cancel_timers: Callable[[str], None],
) -> ChangeType:
    """The `signup.approve` change type, for the builder to register.

    The payload is ``user_id`` (the driver's account, as text) and ``actor_name`` (the manager's
    display name, which the driver's notice gives). *cancel_timers* takes away a driver's timers.
    """

    async def check(ctx: CheckContext) -> Verdict:
        profile = await drivers().get_profile(str(ctx.payload["user_id"]))
        if profile is None or profile.current_state != DriverState.PENDING_ADMIN_APPROVAL:
            return Verdict.refuse(f"⛔ {ACTIONED}", ACTIONED)
        if await signup().get_config() is None:
            return Verdict.refuse(
                "⛔ The signup module is not configured, so no signup can be approved. "
                "Nothing was changed.",
                "The signup module is not configured.",
            )
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {"user_id": str(payload["user_id"]), "actor_name": str(payload["actor_name"])}
        return (PlannedStep(APPROVE_JOB, dict(shared)), PlannedStep(GRANT_ROLE, dict(shared)))

    async def approve(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        wizard = await signup().get_wizard_on(db, user_id)
        record = await signup().get_record_on(db, user_id)
        if record is not None and record.lap_times:
            await signup().set_total_lap_ms_on(db, user_id, total_lap_ms(record.lap_times))
        await drivers().transition_on(db, user_id, DriverState.UNASSIGNED)
        await signup().mark_approved_on(db, user_id)
        cancel_timers(user_id)
        follow_ons: tuple[FollowOn, ...] = ()
        if wizard is not None and wizard.signup_channel_id is not None:
            follow_ons = (FollowOn(
                CLOSING,
                {
                    "user_id": user_id, "channel_id": int(wizard.signup_channel_id),
                    "notice": APPROVED_NOTICE.format(manager=ctx.step_payload["actor_name"]),
                },
                "Closing an approved signup",
            ),)
        return StepResult(result={}, follow_ons=follow_ons)

    async def approved(ctx: StepContext) -> bool:
        return not any(
            view.name == APPROVE_JOB and "discarded" in (view.result or {}) for view in ctx.steps
        )

    async def grant_role(ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        note = await _grant(ctx, user_id)
        line = f"{ctx.named} | Signup | Approved\n  driver: {await name_of_member(ctx.bot, int(user_id))}"
        if note is not None:
            line += f"\n  {note}"
        return StepResult(result={"note": note} if note else {}, lines=(line,))

    async def _grant(ctx: StepContext, user_id: str) -> str | None:
        """Give the driver the league's driver role, or say why it could not be given."""
        server_cfg = await config.get_server_config()
        driver_role_id = None if server_cfg is None else server_cfg.driver_role_id
        if not driver_role_id:
            return None
        guild = await league_guild(ctx.bot)
        member = None if guild is None else guild.get_member(int(user_id))
        if member is None:
            return "The driver role could not be granted: the driver is no longer on the server."
        role = guild.get_role(driver_role_id) if guild is not None else None
        if role is None:
            return "The driver role could not be granted: the role no longer exists on the server."
        try:
            await member.add_roles(role, reason="Signup approved")
        except discord.HTTPException:
            return "The driver role could not be granted: Discord refused it."
        return None

    async def describe_approve(ctx: StepContext) -> str:
        return f"approving the signup of <@{ctx.step_payload['user_id']}>"

    async def describe_grant(ctx: StepContext) -> str:
        return f"giving <@{ctx.step_payload['user_id']}> the driver role"

    def outcome(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        approved_job = views.get(APPROVE_JOB)
        if approved_job is not None and "discarded" in (approved_job.result or {}):
            return "⚠️ The signup was not approved: the job was discarded."
        text = "✅ Signup approved."
        granted = views.get(GRANT_ROLE)
        if granted is not None and "discarded" in (granted.result or {}):
            return text + "\n⚠️ The driver role could not be granted: the job was discarded."
        note = (granted.result or {}).get("note") if granted is not None else None
        return text + (f"\n⚠️ {note}" if note else "")

    return ChangeType(
        kind=APPROVE,
        opening=(),
        plan=plan,
        steps={
            APPROVE_JOB: Step(APPROVE_JOB, StepKind.SAVE, approve, describe=describe_approve),
            GRANT_ROLE: Step(
                GRANT_ROLE, StepKind.ACT, grant_role, still_due=approved, describe=describe_grant
            ),
        },
        check=check,
        key=lambda payload: f"{APPROVE}:{payload['user_id']}",
        doing=lambda payload: "Approving a signup",
        outcome=outcome,
    )
