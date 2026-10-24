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
  the approval's role rule). The "Approved" line is `grant_role`'s; where an admin discards that
  job the change's `finished_lines` write it as the change closes, since the approval it records
  stands.

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
ended is dropped. `arm_lapse` cannot be discarded (#439, slice 8): dropped, nothing would end the
correction request and the driver would stay awaiting a choice, an unsettled signup that refuses
the season's placements; a Discard on it is refused and only a Retry moves it.

**The choice of what to correct** asks `signup.correction.choose` (C86) when a manager presses one of
the correction choices, acknowledged privately and updated. Its check refuses a request that has
ended (the five minutes lapsed, another choice was made, the signup was decided, the wizard is gone)
and a press made more than five minutes after the choices appeared, **judged at the press's time**
(the payload's ``pressed_at`` against the wizard's ``correction_offered_at``), so a choice pressed in
time counts though the queue reaches it later, and a choice pressed late is refused though the
lapse has not yet run. Its jobs are `name_driver`, `choose` (a save: Pending Driver Correction, the
wizard moved to the step that re-collects the answer, the line written) and `arm_inactivity`, a
job of its own after it, which takes the five minutes away and arms the driver's 24 hours from
when it ran. `choose` and `arm_inactivity` cannot be discarded
(#439, slice 8): dropped, the driver would be left awaiting a choice nobody can make, or a
correction that never times out, either an unsettled signup that refuses the season's placements;
a Discard on either is refused and only a Retry moves it. The choice asks back `signup.recollect` on the driver's own lane for the permission
and the prompt to re-submit, so that a channel Discord refuses stops that driver's lane alone.

**The correction's lapse** asks `signup.correction.lapse` (C87), on the league lane, when the five
minutes run out (the timer `correction_lapse`, which the builder registers) and for each driver
still awaiting a choice when the bot starts again, before the queue does (`ask_lapse`). Its check
drops it where the signup module is off, the driver no longer awaits a choice, or a choice pressed
in time waits on the queue (that choice counts, Q23). Its jobs are `names`, which finds the
manager and the driver for the notice and the line (a save waits on no second connection),
`lapse` (a save: back to Pending Admin Approval, the wizard unengaged and the correction's state,
its reason, requester and offer time cleared), `post_lapse` (the notice mentioning the manager,
worded for a restart where it was one) and `post_panel` (a fresh review panel, and the lapse
written to the log, naming the panel where one was posted). A signup channel confirmed gone (the
strict lookup `post_lapse` and `post_panel` share, which stops the job where the league's server
is missing from the cache or Discord cannot tell) posts nothing and ends the signup as a driver
who leaves does, `post_panel` asking `signup.end` in its own save and the lapse's record saying
so (#439, slice 8). `lapse` and `post_panel` cannot be
discarded (#439, slice 8): dropped, the driver would be left awaiting a correction nobody can
choose, or waiting for approval with no panel, and either leaves a signup unsettled, which refuses
the season's placements; a Discard on either is refused and only a Retry moves it.

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
    ChangeOrigin,
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
    unfinished,
)
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.input_validator import parse_time
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import abandoned_line, name_of_member
from leaguebot.signup.models.signup_module import WizardState
from leaguebot.signup.services.closing_steps import (
    DELETE_REASON,
    INACTIVITY_TIMER,
    KIND as CLOSING,
    take_reason_down,
)
from leaguebot.signup.services.signup_posting import post
from leaguebot.signup.services.signup_views import _BUTTON_OF, _NOTHING_DONE, AdminReviewView
from leaguebot.signup.services.wizard_answers import (
    INACTIVITY,
    _BUTTON_ONLY,
    _set_typing,
    _signup_channel_or_gone,
    _text_channel,
    channel_gone_ending,
)
from leaguebot.signup.services.wizard_service import (
    _CORRECTION_PARAMETER_STATES,
    _REVIEW_PANEL_MENTIONS,
    WizardService,
    build_step_view,
)

if TYPE_CHECKING:
    from leaguebot.core.services.change_queue import ChangeQueue
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.core.services.module_service import ModuleService
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
LAPSE = "signup.correction.lapse"
NAMES = "names"
LAPSE_JOB = "lapse"
POST_LAPSE = "post_lapse"
POST_PANEL = "post_panel"
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
    "CORRECTION_TIMER", "CORRECTION_WINDOW", "LAPSE", "RECOLLECT", "REQUEST", "approve_change",
    "ask_lapse", "choices_change", "choose_change", "lapse_change", "recollect_change",
    "request_changes_change", "total_lap_ms",
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

    def finished_lines(ctx: OutcomeContext) -> tuple[str, ...]:
        # The "Approved" line is `grant_role`'s; a role job an admin discarded wrote none, and the
        # approval it follows stands, so the line is written as the change closes.
        views = {view.name: view for view in ctx.steps}
        approved_job = views.get(APPROVE_JOB)
        granted = views.get(GRANT_ROLE)
        if (
            approved_job is None or not approved_job.done
            or "discarded" in (approved_job.result or {})
            or granted is None or "discarded" not in (granted.result or {})
        ):
            return ()
        return (
            f"{ctx.named} | Signup | Approved\n  driver: <@{ctx.payload['user_id']}>\n"
            "  The driver role could not be granted: the job was discarded.",
        )

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
        finished_lines=finished_lines,
    )


def request_changes_change(
    *,
    signup: Callable[[], "SignupModuleService"],
    drivers: Callable[[], "DriverService"],
) -> ChangeType:
    """The `signup.request_changes` change type, for the builder to register.

    The payload is ``user_id`` (the driver's account, as text), ``actor_id`` and ``actor_name``
    (the manager), ``text`` (the reason they typed) and ``reason_message_id`` (the message it was
    typed in, which the driver's lane takes down) with ``reason_channel_id`` (its channel); a press
    the check refuses as it comes up to run, or an admin discards at the check, has the reason taken
    down all the same (`take_reason_down`).
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
        on_refused=take_reason_down,
        on_discarded=take_reason_down,
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

    async def would_never_lapse(ctx: StepContext) -> str:
        return (
            f"nothing would end <@{ctx.step_payload['user_id']}>'s correction request once its "
            "five minutes ran out, and the season's placements could not be confirmed until the "
            "bot restarts"
        )

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
            ARM_LAPSE: Step(
                ARM_LAPSE, StepKind.ACT, arm_lapse, describe=describe_arm,
                undiscardable=would_never_lapse,
            ),
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

    async def would_never_time_out(ctx: StepContext) -> str:
        return (
            f"<@{ctx.step_payload['user_id']}>'s signup would never time out if they stopped "
            "answering, and while they correct it the season's placements could not be "
            "confirmed until the bot restarts"
        )

    def outcome(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        chose = views.get(CHOOSE_JOB)
        if chose is not None and (chose.result or {}).get("ended"):
            return f"⛔ {CORRECTION_ENDED}"
        return f"✅ Re-collecting **{str(ctx.payload['parameter']).replace('_', ' ')}**."

    return ChangeType(
        kind=CHOOSE,
        opening=(),
        plan=plan,
        steps={
            NAME_DRIVER: Step(NAME_DRIVER, StepKind.ACT, name_driver, describe=describe_name),
            CHOOSE_JOB: Step(
                CHOOSE_JOB, StepKind.SAVE, choose, describe=describe_choose,
                undiscardable=would_await_a_choice_nobody_can_make,
            ),
            ARM_INACTIVITY: Step(
                ARM_INACTIVITY, StepKind.ACT, arm_inactivity, still_due=chosen,
                describe=describe_arm, undiscardable=would_never_time_out,
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


async def ask_lapse(queue: "ChangeQueue", user_id: str, *, restart: bool = False) -> int | None:
    """Ask the queue, as the bot, for the lapse of *user_id*'s correction request
    (`signup.correction.lapse`): the correction timer's ask once its five minutes ran out, and the
    restart's for every driver still awaiting a choice (*restart*, which only changes the notice's
    wording). Gives the change's id, or None where the change is not due (its check judges that)."""
    return await queue.ask(
        LAPSE,
        {"user_id": user_id, "restart": restart},
        what=f"returning <@{user_id}> to the approval queue, no correction having been chosen",
        origin=ChangeOrigin.BOT,
    )


async def would_await_a_choice_nobody_can_make(ctx: StepContext) -> str:
    """What a Discard of a job that moves a driver out of Awaiting Correction Parameter would
    leave: the driver stuck there, an unsettled signup that refuses the season's placements."""
    return (
        f"<@{ctx.step_payload['user_id']}> would be left awaiting a correction nobody can "
        "choose, and the season's placements could not be confirmed until the bot restarts"
    )


def lapse_change(
    *,
    modules: "ModuleService",
    signup: Callable[[], "SignupModuleService"],
    drivers: Callable[[], "DriverService"],
    queue_of: Callable[[], "ChangeQueue"],
) -> ChangeType:
    """The `signup.correction.lapse` change type, for the builder to register, on the league lane
    (a timer's change is the league's whole, and so is the driver's status).

    The payload is ``user_id`` (the driver's account, as text) and ``restart`` (whether the bot
    starting again asked it, which words the notice). It is not due, and is dropped, where the
    signup module is off (no panel is posted into a module the league turned off), the driver no
    longer awaits a correction choice, or a choice pressed in time is still queued: that choice
    counts. *queue_of* gives the change queue the fresh review panel is handed.
    """

    async def check(ctx: CheckContext) -> Verdict:
        user_id = str(ctx.payload["user_id"])
        if not await modules.is_signup_enabled():
            return Verdict.not_due("the signup module is off")
        profile = await drivers().get_profile(user_id)
        if profile is None or profile.current_state != DriverState.AWAITING_CORRECTION_PARAMETER:
            return Verdict.not_due("the driver no longer awaits a correction choice")
        if any(
            str(waiting.get("user_id")) == user_id for waiting in await unfinished(ctx.db_path, [CHOOSE])
        ):
            return Verdict.not_due("a choice pressed in time waits its turn")
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {"user_id": str(payload["user_id"]), "restart": bool(payload.get("restart"))}
        return (
            PlannedStep(NAMES, dict(shared)),
            PlannedStep(LAPSE_JOB, dict(shared)),
            PlannedStep(POST_LAPSE, dict(shared)),
            PlannedStep(POST_PANEL, dict(shared)),
        )

    async def names(ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        wizard = await signup().get_wizard(user_id)
        asked = None if wizard is None else wizard.draft_answers.get("_correction_requested_by")
        guild = await league_guild(ctx.bot)
        member = None if guild is None else guild.get_member(int(user_id))
        driver = getattr(member, "display_name", None)
        return StepResult(result={
            "manager": await name_of_member(ctx.bot, int(asked) if asked else None),
            "driver": driver if isinstance(driver, str) else f"<@{user_id}>",
        })

    async def lapse(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        # The check judged the state as the change started; the transition is the check that
        # cannot be raced, and a refused one means the window has nothing left to lapse.
        try:
            await drivers().transition_on(db, user_id, DriverState.PENDING_ADMIN_APPROVAL)
        except ValueError:
            return StepResult(result={"ended": True})
        wizard = await signup().get_wizard_on(db, user_id)
        if wizard is None:
            return StepResult(result={})
        requested_by = wizard.draft_answers.get("_correction_requested_by")
        wizard.wizard_state = WizardState.UNENGAGED
        # The reason goes with the rest of the correction state, so that a stale one cannot
        # resurface on the manager's next correction.
        for key in ("_is_correction", "_correction_reason", "_correction_requested_by"):
            wizard.draft_answers.pop(key, None)
        await signup().save_wizard_on(db, wizard)
        await signup().set_correction_offered_at_on(db, user_id, None)
        return StepResult(result={
            "requested_by": requested_by,
            "channel_id": wizard.signup_channel_id,
        })

    async def lapsed(ctx: StepContext) -> bool:
        """Whether the lapse took place, for the notice and the panel that follow it: not where
        the window had nothing left to lapse."""
        return any(
            view.name == LAPSE_JOB and view.done and not (view.result or {}).get("ended")
            for view in ctx.steps
        )

    def lapse_result(ctx: OutcomeContext) -> dict[str, Any]:
        return next(
            ((view.result or {}) for view in ctx.steps if view.name == LAPSE_JOB and view.done), {}
        )

    def named_result(ctx: OutcomeContext) -> dict[str, Any]:
        return next(
            ((view.result or {}) for view in ctx.steps if view.name == NAMES and view.done), {}
        )

    @posts_in_a_job
    async def post_lapse(ctx: StepContext) -> StepResult:
        channel_id = lapse_result(ctx).get("channel_id")
        if channel_id is None:
            return StepResult(result={"no_channel": True})
        channel = await _signup_channel_or_gone(ctx, int(channel_id))
        if channel is None:
            return StepResult(result={"no_channel": True})
        requested_by = lapse_result(ctx).get("requested_by")
        driver = named_result(ctx).get("driver") or f"<@{ctx.step_payload['user_id']}>"
        lapse_text = (
            "the bot restarted before a correction parameter was chosen"
            if ctx.step_payload["restart"] else "nobody chose a correction parameter in time"
        )
        # No mention where the window was opened before the requesting manager was recorded.
        opening = f"<@{requested_by}> — {lapse_text}" if requested_by else lapse_text.capitalize()
        await post(
            channel,
            f"⏰ {opening}. **{driver}** is back in the approval queue.",
            mentions=discord.AllowedMentions(
                everyone=False, roles=False,
                users=[discord.Object(id=int(requested_by))] if requested_by else [],
            ),
            refused=f"the correction's lapse could not be told in channel {channel.id}",
        )
        return StepResult(result={})

    @posts_in_a_job
    async def post_panel(ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        channel_id = lapse_result(ctx).get("channel_id")
        channel = None if channel_id is None else await _signup_channel_or_gone(
            ctx, int(channel_id)
        )
        if channel is None:
            # The channel is gone, or the wizard names none: the signup ends as it does for a
            # driver who leaves, since nothing else would ever settle a driver awaiting a review
            # that cannot be posted.
            ended = await channel_gone_ending(
                ctx, user_id, None if channel_id is None else int(channel_id),
            )
            return StepResult(
                result={"panel": False, "channel_gone": True},
                lines=(lapse_line(ctx, False, gone=True),),
                follow_ons=ended.follow_ons,
            )
        record = await signup().get_record(user_id)
        posted = False
        if record is not None:
            wizard = await signup().get_wizard(user_id)
            slot_labels = {
                slot.slot_id: slot.display_label
                for slot in (
                    wizard.config_snapshot.slots if wizard and wizard.config_snapshot else []
                )
            }
            async with get_connection(ctx.db_path) as db:
                track_map = await track_service.get_track_name_map(db)
            await post(
                channel,
                WizardService._format_review_panel(record, slot_labels, track_name_map=track_map),
                view=AdminReviewView(user_id, change_queue=queue_of()),
                mentions=_REVIEW_PANEL_MENTIONS,
                refused=f"the review panel could not be posted in channel {channel.id}",
            )
            posted = True
        return StepResult(result={"panel": posted}, lines=(lapse_line(ctx, posted),))

    def lapse_line(ctx: OutcomeContext, posted: bool, gone: bool = False) -> str:
        """The log channel's record of the lapse, naming the manager who asked and the driver, and
        whether a fresh review panel is in the driver's channel, or that the channel was gone and
        the signup ended."""
        user_id = str(ctx.payload["user_id"])
        driver = named_result(ctx).get("driver") or f"<@{user_id}>"
        return abandoned_line(
            str(named_result(ctx).get("manager") or "a member"),
            f"the correction request for {driver}'s signup",
            lapsed=True,
            detail=(
                f"{driver}'s signup channel was gone, so no fresh review panel could be posted "
                "and their signup is ended."
                if gone else
                f"{driver} is back in the approval queue"
                + ("; a fresh review panel is in their channel." if posted else ".")
            ),
        )

    async def describe_names(ctx: StepContext) -> str:
        return f"finding the names of <@{ctx.step_payload['user_id']}> and their manager for the record"

    async def describe_lapse(ctx: StepContext) -> str:
        return f"returning <@{ctx.step_payload['user_id']}> to the approval queue"

    async def describe_post_lapse(ctx: StepContext) -> str:
        return f"telling <@{ctx.step_payload['user_id']}>'s channel the correction lapsed"

    async def describe_panel(ctx: StepContext) -> str:
        return f"posting <@{ctx.step_payload['user_id']}>'s review panel again"

    async def would_wait_unreviewed(ctx: StepContext) -> str:
        return (
            f"<@{ctx.step_payload['user_id']}> would wait for approval with no review panel, "
            "and the season's placements could not be confirmed"
        )

    return ChangeType(
        kind=LAPSE,
        opening=(),
        plan=plan,
        steps={
            NAMES: Step(NAMES, StepKind.ACT, names, describe=describe_names),
            LAPSE_JOB: Step(
                LAPSE_JOB, StepKind.SAVE, lapse, describe=describe_lapse,
                undiscardable=would_await_a_choice_nobody_can_make,
            ),
            POST_LAPSE: Step(
                POST_LAPSE, StepKind.ACT, post_lapse, still_due=lapsed,
                describe=describe_post_lapse,
            ),
            POST_PANEL: Step(
                POST_PANEL, StepKind.ACT, post_panel, still_due=lapsed, describe=describe_panel,
                undiscardable=would_wait_unreviewed,
            ),
        },
        check=check,
        key=lambda payload: f"{LAPSE}:{payload['user_id']}",
        doing=lambda payload: "Returning a driver to the approval queue",
        outcome=lambda _ctx: "",
    )
