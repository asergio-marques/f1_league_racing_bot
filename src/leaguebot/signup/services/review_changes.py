"""A signup's review on the change queue (#439, slice 6b, C84, C85).

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

**Request Changes** asks `signup.request_changes` once the manager has typed the reason, through
the press they made. Its check refuses a signup that has moved on since the press, as a rejection
does. Its jobs are `name_driver`, which reads the driver's name for the line (a save waits on no
second connection), and `request` (a save), which moves the driver to Awaiting Correction
Parameter, writes down who asked and why on the driver's wizard (the lapse mentions that manager)
and the "Correction requested" line, and asks back `signup.choices` on the driver's own lane.

`signup.choices` is the conversation's own turn: `delete_reason` takes down the reason the manager
typed, `post_choices` posts the correction choices and **its record saves the time they were
posted** (`correction_offered_at`), and `arm_lapse`, a job of its own after them, arms the five
minutes from that time, so the manager's five minutes start when the buttons appear and not when
the reason was typed, however long the queue held it. A choices change whose conversation has
ended is dropped.

**The lap sum is signup's own.** The sum is written on the signup record by this module, in the
approval's save; core's `PlacementService.store_total_lap_ms`, which commits on a connection of
its own, is not asked.

A job that fails stops the queue (core specification, "How a change is carried out").
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime, timedelta
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
from leaguebot.core.services import standing_posts
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.input_validator import parse_time
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import name_of_member
from leaguebot.signup.services.closing_steps import DELETE_REASON, KIND as CLOSING
from leaguebot.signup.services.signup_posting import post
from leaguebot.signup.services.signup_views import _BUTTON_OF, _NOTHING_DONE

if TYPE_CHECKING:
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

APPROVE = "signup.approve"
APPROVE_JOB = "approve"
GRANT_ROLE = "grant_role"
REQUEST = "signup.request_changes"
CHOICES = "signup.choices"
NAME_DRIVER = "name_driver"
REQUEST_JOB = "request"
POST_CHOICES = "post_choices"
ARM_LAPSE = "arm_lapse"
#: The timer that releases a driver whose correction nobody chose within five minutes.
CORRECTION_TIMER = "correction_lapse"
#: How long a manager has to choose what a driver is to correct, from the choices appearing.
CORRECTION_WINDOW = timedelta(minutes=5)

#: What a manager is told whose press finds the signup already actioned.
ACTIONED = "This signup has already been actioned."
#: What the driver's channel is told when their signup is approved.
APPROVED_NOTICE = (
    "✅ Your signup has been approved by **{manager}**! You are now an Unassigned driver. "
    "This channel will be automatically deleted in 24 hours."
)

__all__ = [
    "ACTIONED", "APPROVE", "APPROVED_NOTICE", "CHOICES", "CORRECTION_TIMER", "CORRECTION_WINDOW",
    "REQUEST", "approve_change", "choices_change", "request_changes_change", "total_lap_ms",
]


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


def request_changes_change(
    *,
    signup: Callable[[], "SignupModuleService"],
    drivers: Callable[[], "DriverService"],
) -> ChangeType:
    """The `signup.request_changes` change type, for the builder to register.

    The payload is ``user_id`` (the driver's account, as text), ``actor_id`` and ``actor_name``
    (the manager), ``text`` (the reason they typed) and ``reason_message_id`` (the message it was
    typed in, which the driver's lane takes down).
    """

    async def check(ctx: CheckContext) -> Verdict:
        profile = await drivers().get_profile(str(ctx.payload["user_id"]))
        wizard = await signup().get_wizard(str(ctx.payload["user_id"]))
        if (
            profile is None or profile.current_state != DriverState.PENDING_ADMIN_APPROVAL
            or wizard is None
        ):
            button = _BUTTON_OF["request_changes"]
            return Verdict.refuse(
                f"⛔ This signup has moved on since you pressed {button}. "
                f"{_NOTHING_DONE['request_changes']}."
            )
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {key: payload[key] for key in (
            "user_id", "actor_id", "actor_name", "text", "reason_message_id"
        )}
        return (PlannedStep(NAME_DRIVER, {"user_id": str(payload["user_id"])}),
                PlannedStep(REQUEST_JOB, shared))

    async def name_driver(ctx: StepContext) -> StepResult:
        return StepResult(result={
            "named": await name_of_member(ctx.bot, int(ctx.step_payload["user_id"]))
        })

    async def request(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        user_id = str(payload["user_id"])
        wizard = await signup().get_wizard_on(db, user_id)
        if wizard is None:
            return StepResult(result={"no_wizard": True})
        await drivers().transition_on(db, user_id, DriverState.AWAITING_CORRECTION_PARAMETER)
        # Who asked is written down, as the window can outlive the process; the reason beside it
        # appears only when the driver is prompted to re-submit.
        wizard.draft_answers["_correction_requested_by"] = str(payload["actor_id"])
        if payload["text"]:
            wizard.draft_answers["_correction_reason"] = payload["text"]
        await signup().save_wizard_on(db, wizard)
        named = next(
            (str((view.result or {}).get("named")) for view in ctx.steps
             if view.name == NAME_DRIVER and view.done),
            f"<@{user_id}>",
        )
        line = f"{ctx.named} | Signup | Correction requested\n  driver: {named}"
        if payload["text"]:
            line += f"\n  reason: {payload['text']}"
        follow_ons: tuple[FollowOn, ...] = ()
        if wizard.signup_channel_id is not None:
            follow_ons = (FollowOn(
                CHOICES,
                {
                    "user_id": user_id, "channel_id": int(wizard.signup_channel_id),
                    "actor_name": str(payload["actor_name"]),
                    "reason_message_id": int(payload["reason_message_id"]),
                },
                "Offering a signup's correction choices",
            ),)
        return StepResult(result={}, lines=(line,), follow_ons=follow_ons)

    async def describe_name(ctx: StepContext) -> str:
        return f"finding the name of <@{ctx.step_payload['user_id']}> for the record"

    async def describe_request(ctx: StepContext) -> str:
        return f"asking for changes to the signup of <@{ctx.step_payload['user_id']}>"

    def outcome(ctx: OutcomeContext) -> str:
        requested = {view.name: view for view in ctx.steps}.get(REQUEST_JOB)
        if requested is not None and "discarded" in (requested.result or {}):
            return "⚠️ No changes were requested: the job was discarded."
        return "✅ Correction requested."

    return ChangeType(
        kind=REQUEST,
        opening=(),
        plan=plan,
        steps={
            NAME_DRIVER: Step(NAME_DRIVER, StepKind.ACT, name_driver, describe=describe_name),
            REQUEST_JOB: Step(REQUEST_JOB, StepKind.SAVE, request, describe=describe_request),
        },
        check=check,
        key=lambda payload: f"{REQUEST}:{payload['user_id']}",
        doing=lambda payload: "Asking for changes to a signup",
        outcome=outcome,
    )


def choices_change(
    *,
    signup: Callable[[], "SignupModuleService"],
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
) -> ChangeType:
    """The `signup.choices` change type, for the builder to register, on the driver's lane
    (``channel:<signup channel>``): the league asked it back once a manager's request for changes
    was saved. The payload is ``user_id``, ``channel_id``, ``actor_name`` (the manager) and
    ``reason_message_id``. *now* and *arm_once* are the queue's clock and the scheduler service's
    one-off timer."""

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {"user_id": str(payload["user_id"]), "channel_id": int(payload["channel_id"])}
        return (
            PlannedStep(DELETE_REASON, {
                "channel_id": shared["channel_id"],
                "message_id": int(payload["reason_message_id"]), "what": "reason message",
            }),
            PlannedStep(POST_CHOICES, {**shared, "actor_name": str(payload["actor_name"])}),
            PlannedStep(ARM_LAPSE, dict(shared)),
        )

    async def standing(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    async def open_(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
        """Whether the driver still awaits a correction choice in the channel."""
        row = await (
            await db.execute(
                "SELECT 1 FROM signup_wizard_records w JOIN driver_profiles d "
                "ON d.discord_user_id = w.discord_user_id "
                "WHERE w.signup_channel_id = ? AND d.current_state = ?",
                (int(payload["channel_id"]), DriverState.AWAITING_CORRECTION_PARAMETER.value),
            )
        ).fetchone()
        return row is not None

    @posts_in_a_job
    async def post_choices(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        user_id = str(ctx.step_payload["user_id"])
        guild = await league_guild(ctx.bot)
        found = None if guild is None else guild.get_channel(int(ctx.step_payload["channel_id"]))
        if not isinstance(found, discord.TextChannel):
            return StepResult(result={"no_channel": True})
        from leaguebot.signup.services.signup_views import CorrectionParameterView

        member = found.guild.get_member(int(user_id))
        mention = member.mention if member is not None else f"<@{user_id}>"
        message_id = await post(
            found,
            f"{mention} **{ctx.step_payload['actor_name']}** has requested a correction.\n"
            "Please select the parameter to correct (5-minute window):",
            view=CorrectionParameterView(user_id, ctx.bot),
            mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(id=int(user_id))]
            ),
            refused=f"the correction choices could not be posted in channel {found.id}",
        )
        return StepResult(result={
            "new": message_id, "channel_id": found.id, "at": now().isoformat(),
        })

    async def offered(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        at = (result.result or {}).get("at")
        if at is not None:
            await signup().set_correction_offered_at_on(db, str(ctx.step_payload["user_id"]), at)

    async def arm_lapse(ctx: StepContext) -> StepResult:
        posted = next(
            ((view.result or {}).get("at") for view in ctx.steps
             if view.name == POST_CHOICES and view.done),
            None,
        )
        began = datetime.fromisoformat(posted) if posted else now()
        arm_once(CORRECTION_TIMER, str(ctx.step_payload["user_id"]), began + CORRECTION_WINDOW)
        return StepResult(result={})

    async def describe_post(ctx: StepContext) -> str:
        return f"offering the correction choices in <#{ctx.step_payload['channel_id']}>"

    async def describe_arm(ctx: StepContext) -> str:
        return f"arming the five minutes to choose for <@{ctx.step_payload['user_id']}>"

    async def describe_reason(ctx: StepContext) -> str:
        return f"deleting the reason typed in <#{ctx.step_payload['channel_id']}>"

    return ChangeType(
        kind=CHOICES,
        opening=(),
        plan=plan,
        steps={
            DELETE_REASON: dataclasses.replace(
                standing_posts.delete_replaced_step(DELETE_REASON), describe=describe_reason
            ),
            POST_CHOICES: Step(
                POST_CHOICES, StepKind.ACT, post_choices, describe=describe_post, record=offered
            ),
            ARM_LAPSE: Step(ARM_LAPSE, StepKind.ACT, arm_lapse, describe=describe_arm),
        },
        check=standing,
        key=lambda payload: f"{CHOICES}:{payload['user_id']}",
        doing=lambda payload: f"Offering the correction choices of <@{payload['user_id']}>",
        outcome=lambda _ctx: "",
        lane=lambda payload: f"channel:{int(payload['channel_id'])}",
        conversation_open=open_,
        writes=("signup_wizard_records",),
    )
