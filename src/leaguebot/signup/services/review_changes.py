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

**The choice of what to correct** asks `signup.correction.choose` (C86) when a manager presses one of
the correction choices, acknowledged privately and updated. Its check refuses a request that has
ended (the five minutes lapsed, another choice was made, the signup was decided, the wizard is gone)
and a press made more than five minutes after the choices appeared, **judged at the press's time**
(the payload's ``pressed_at`` against the wizard's ``correction_offered_at``), so a choice pressed in
time counts though the queue reaches it later, and a choice pressed late is refused though the
lapse has not yet run. Its jobs are `name_driver`, `choose` (a save: Pending Driver Correction, the
wizard moved to the step that re-collects the answer, the line written) and `arm_inactivity`, a
job of its own after it, which takes the five minutes away and arms the driver's 24 hours from
when it ran. The choice asks back `signup.recollect` on the driver's own lane for the permission
and the prompt to re-submit, so that a channel Discord refuses stops that driver's lane alone.

**The lap sum is signup's own.** The sum is written on the signup record by this module, in the
approval's save; core's `PlacementService.store_total_lap_ms`, which commits on a connection of
its own, is not asked.

A job that fails stops the queue (core specification, "How a change is carried out").
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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
from leaguebot.core.services import standing_posts, track_service
from leaguebot.core.db.database import get_connection
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
from leaguebot.signup.models.signup_module import WizardState
from leaguebot.signup.services.closing_steps import (
    DELETE_REASON,
    INACTIVITY_TIMER,
    KIND as CLOSING,
)
from leaguebot.signup.services.signup_posting import post
from leaguebot.signup.services.signup_views import _BUTTON_OF, _NOTHING_DONE
from leaguebot.signup.services.wizard_answers import (
    INACTIVITY,
    _BUTTON_ONLY,
    _set_typing,
    _text_channel,
)
from leaguebot.signup.services.wizard_service import (
    _CORRECTION_PARAMETER_STATES,
    WizardService,
    build_step_view,
)

if TYPE_CHECKING:
    from leaguebot.core.services.change_queue import ChangeQueue
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
CHOOSE = "signup.correction.choose"
RECOLLECT = "signup.recollect"
CHOOSE_JOB = "choose"
ARM_INACTIVITY = "arm_inactivity"
POST_RECOLLECT = "post_recollect"
#: The timer that releases a driver whose correction nobody chose within five minutes.
CORRECTION_TIMER = "correction_lapse"
#: How long a manager has to choose what a driver is to correct, from the choices appearing.
CORRECTION_WINDOW = timedelta(minutes=5)

#: What a manager is told whose choice finds the correction request over.
CORRECTION_ENDED = "This correction request has ended. Nothing was changed."
#: What a manager is told whose press finds the signup already actioned.
ACTIONED = "This signup has already been actioned."
#: What the driver's channel is told when their signup is approved.
APPROVED_NOTICE = (
    "✅ Your signup has been approved by **{manager}**! You are now an Unassigned driver. "
    "This channel will be automatically deleted in 24 hours."
)

__all__ = [
    "ACTIONED", "APPROVE", "APPROVED_NOTICE", "CHOICES", "CHOOSE", "CORRECTION_ENDED",
    "CORRECTION_TIMER", "CORRECTION_WINDOW", "RECOLLECT", "REQUEST", "approve_change",
    "choices_change", "choose_change", "recollect_change", "request_changes_change", "total_lap_ms",
]


def _instant(text: str) -> datetime:
    """An ISO time as text, read as UTC where it carries no zone."""
    at = datetime.fromisoformat(text)
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


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
    queue_of: Callable[[], "ChangeQueue"],
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
) -> ChangeType:
    """The `signup.choices` change type, for the builder to register, on the driver's lane
    (``channel:<signup channel>``): the league asked it back once a manager's request for changes
    was saved. The payload is ``user_id``, ``channel_id``, ``actor_name`` (the manager) and
    ``reason_message_id``. *queue_of* gives the change queue the choices are handed. *now* and
    *arm_once* are the queue's clock and the scheduler service's one-off timer."""

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
            view=CorrectionParameterView(user_id, change_queue=queue_of()),
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


def _owner_of(guild: discord.Guild | None, user_id: str) -> str:
    """Whose signup review a button sits on, as the log channel names it: "Alex's", by the display
    name the league's server gives them, or their mention where the name is not to be had."""
    member = None if guild is None else guild.get_member(int(user_id))
    name = getattr(member, "display_name", None)
    return f"{name if isinstance(name, str) else f'<@{user_id}>'}'s"


def choose_change(
    *,
    signup: Callable[[], "SignupModuleService"],
    drivers: Callable[[], "DriverService"],
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
    disarm: Callable[[str, str], None],
) -> ChangeType:
    """The `signup.correction.choose` change type, for the builder to register.

    The payload is ``user_id`` (the driver's account, as text), ``parameter`` (the answer to
    correct, by its key), ``label`` (the button's, which the line names it by) and ``pressed_at``
    (when the manager pressed it, which the five minutes are judged against). *now*, *arm_once* and
    *disarm* are the queue's clock and the scheduler service's one-off timer and its removal.
    """

    async def check(ctx: CheckContext) -> Verdict:
        user_id = str(ctx.payload["user_id"])
        profile = await drivers().get_profile(user_id)
        wizard = await signup().get_wizard(user_id)
        ended = Verdict.refuse(f"⛔ {CORRECTION_ENDED}", CORRECTION_ENDED)
        if (
            ctx.payload["parameter"] not in _CORRECTION_PARAMETER_STATES
            or profile is None or wizard is None
            or profile.current_state != DriverState.AWAITING_CORRECTION_PARAMETER
        ):
            return ended
        offered = wizard.correction_offered_at
        if offered is not None and (
            _instant(str(ctx.payload["pressed_at"])) > _instant(offered) + CORRECTION_WINDOW
        ):
            return ended
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {key: payload[key] for key in ("user_id", "parameter", "label")}
        return (
            PlannedStep(NAME_DRIVER, {"user_id": str(payload["user_id"])}),
            PlannedStep(CHOOSE_JOB, shared),
            PlannedStep(ARM_INACTIVITY, {"user_id": str(payload["user_id"])}),
        )

    async def name_driver(ctx: StepContext) -> StepResult:
        guild = await league_guild(ctx.bot)
        return StepResult(result={"owner": _owner_of(guild, str(ctx.step_payload["user_id"]))})

    async def choose(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        pl = ctx.step_payload
        user_id = str(pl["user_id"])
        wizard = await signup().get_wizard_on(db, user_id)
        if wizard is None:
            return StepResult(result={"ended": True})
        target = _CORRECTION_PARAMETER_STATES[str(pl["parameter"])]
        # The check judged the state as the change started; the transition is the check that
        # cannot be raced, and a refused one means the request has ended all the same.
        try:
            await drivers().transition_on(db, user_id, DriverState.PENDING_DRIVER_CORRECTION)
        except ValueError:
            return StepResult(result={"ended": True})
        reason = wizard.draft_answers.pop("_correction_reason", "")
        wizard.wizard_state = target
        wizard.draft_answers["_is_correction"] = True
        if target == WizardState.COLLECTING_LAP_TIME:
            wizard.current_lap_track_index = 0
        if target == WizardState.COLLECTING_PREFERRED_TEAMS:
            wizard.draft_answers.pop("_pref_teams_step", None)
            wizard.draft_answers["preferred_teams"] = []
        wizard.last_activity_at = now().isoformat()
        await signup().save_wizard_on(db, wizard)
        await signup().set_correction_offered_at_on(db, user_id, None)
        owner = next(
            (str((view.result or {}).get("owner")) for view in ctx.steps
             if view.name == NAME_DRIVER and view.done),
            "a",
        )
        label = pl["label"]
        line = (
            f"{ctx.named} | the “{label}” button of {owner} signup review | "
            f"Correction requested: {label}"
        )
        follow_ons: tuple[FollowOn, ...] = ()
        if wizard.signup_channel_id is not None:
            follow_ons = (FollowOn(
                RECOLLECT,
                {
                    "user_id": user_id, "channel_id": int(wizard.signup_channel_id),
                    "parameter": pl["parameter"], "reason": reason,
                },
                "Asking a driver to re-submit an answer",
            ),)
        return StepResult(result={}, lines=(line,), follow_ons=follow_ons)

    async def chosen(ctx: StepContext) -> bool:
        return any(
            view.name == CHOOSE_JOB and view.done and not (view.result or {}).get("ended")
            and "discarded" not in (view.result or {})
            for view in ctx.steps
        )

    async def arm_inactivity(ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        disarm(CORRECTION_TIMER, user_id)
        arm_once(INACTIVITY_TIMER, user_id, now() + INACTIVITY)
        return StepResult(result={})

    async def describe_name(ctx: StepContext) -> str:
        return f"finding the name of <@{ctx.step_payload['user_id']}> for the record"

    async def describe_choose(ctx: StepContext) -> str:
        return f"sending <@{ctx.step_payload['user_id']}> back to one answer of their signup"

    async def describe_arm(ctx: StepContext) -> str:
        return f"arming the 24 hours of <@{ctx.step_payload['user_id']}>'s correction"

    def outcome(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        chose = views.get(CHOOSE_JOB)
        if chose is not None and "discarded" in (chose.result or {}):
            return "⚠️ No answer was sent back: the job was discarded."
        if chose is not None and (chose.result or {}).get("ended"):
            return f"⛔ {CORRECTION_ENDED}"
        return f"✅ Re-collecting **{str(ctx.payload['parameter']).replace('_', ' ')}**."

    return ChangeType(
        kind=CHOOSE,
        opening=(),
        plan=plan,
        steps={
            NAME_DRIVER: Step(NAME_DRIVER, StepKind.ACT, name_driver, describe=describe_name),
            CHOOSE_JOB: Step(CHOOSE_JOB, StepKind.SAVE, choose, describe=describe_choose),
            ARM_INACTIVITY: Step(
                ARM_INACTIVITY, StepKind.ACT, arm_inactivity, still_due=chosen,
                describe=describe_arm,
            ),
        },
        check=check,
        key=lambda payload: f"{CHOOSE}:{payload['user_id']}",
        doing=lambda payload: "Choosing what a driver is to correct",
        outcome=outcome,
    )


def recollect_change(
    *,
    signup: Callable[[], "SignupModuleService"],
    queue_of: Callable[[], "ChangeQueue"],
) -> ChangeType:
    """The `signup.recollect` change type, for the builder to register, on the driver's lane
    (``channel:<signup channel>``): the league asked it back once a manager's choice was saved.

    The payload is ``user_id``, ``channel_id``, ``parameter`` (the answer to re-submit) and
    ``reason`` (the manager's, which the prompt gives). Its one job `post_recollect` sets the
    driver's typing for the step they return to (taken away for a button step, given back for a
    typed one: a driver returned to a typed question with typing revoked could not answer) and
    posts the prompt. A channel gone from the server is nothing to post in: the driver's state is
    the league's record and the prompt only how they learn of it. *queue_of* gives the change
    queue the step's buttons are handed. A change whose driver is no longer part-way through a
    correction is dropped.
    """

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (PlannedStep(POST_RECOLLECT, {
            "user_id": str(payload["user_id"]), "channel_id": int(payload["channel_id"]),
            "parameter": payload["parameter"], "reason": payload.get("reason") or "",
        }),)

    async def standing(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    async def open_(db: aiosqlite.Connection, payload: dict[str, Any]) -> bool:
        row = await (
            await db.execute(
                "SELECT 1 FROM signup_wizard_records w JOIN driver_profiles d "
                "ON d.discord_user_id = w.discord_user_id "
                "WHERE w.signup_channel_id = ? AND d.current_state = ?",
                (int(payload["channel_id"]), DriverState.PENDING_DRIVER_CORRECTION.value),
            )
        ).fetchone()
        return row is not None

    @posts_in_a_job
    async def post_recollect(ctx: StepContext) -> StepResult:
        pl = ctx.step_payload
        user_id = str(pl["user_id"])
        channel = await _text_channel(ctx, int(pl["channel_id"]))
        if channel is None:
            return StepResult(result={"no_channel": True})
        wizard = await signup().get_wizard(user_id)
        if wizard is None:
            return StepResult(result={"no_wizard": True})
        snapshot = wizard.config_snapshot
        state = wizard.wizard_state
        member = channel.guild.get_member(int(user_id))
        if member is not None:
            await _set_typing(
                channel, member, allowed=state not in _BUTTON_ONLY,
                reason=(
                    "Signup wizard channel hold" if state in _BUTTON_ONLY
                    else "Signup wizard button step answered"
                ),
            )
        mention = member.mention if member is not None else f"<@{user_id}>"
        reason_line = f"\n**Reason:** {pl['reason']}" if pl["reason"] else ""
        async with get_connection(ctx.db_path) as db:
            track_map = await track_service.get_track_name_map(db)
        await post(
            channel,
            f"{mention} Please re-submit your **{str(pl['parameter']).replace('_', ' ')}**:"
            f"{reason_line}\n"
            + WizardService._prompt_for_state(
                state, snapshot, wizard, track_name_map=track_map
            ),
            view=build_step_view(
                queue_of(), state, user_id, snapshot.team_names if snapshot else []
            ),
            mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(id=int(user_id))]
            ),
            refused=f"the prompt to re-submit could not be posted in channel {channel.id}",
        )
        return StepResult(result={})

    async def describe_post(ctx: StepContext) -> str:
        return f"asking for an answer again in <#{ctx.step_payload['channel_id']}>"

    return ChangeType(
        kind=RECOLLECT,
        opening=(),
        plan=plan,
        steps={
            POST_RECOLLECT: Step(
                POST_RECOLLECT, StepKind.ACT, post_recollect, describe=describe_post
            ),
        },
        check=standing,
        key=lambda payload: f"{RECOLLECT}:{payload['user_id']}",
        doing=lambda payload: f"Asking <@{payload['user_id']}> to re-submit an answer",
        outcome=lambda _ctx: "",
        lane=lambda payload: f"channel:{int(payload['channel_id'])}",
        conversation_open=open_,
        writes=(),
    )
