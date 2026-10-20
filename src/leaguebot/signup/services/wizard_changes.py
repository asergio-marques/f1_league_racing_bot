"""A driver's signup channel on the change queue (#439, slice 6b).

Sign Up asks `signup.start`, a change of the league lane: a press outside every conversation is a
command's, and the driver has no channel of their own to hold a lane yet. Its check is every
refusal the button has always made (test mode, a past account, a signup in progress or approved,
the module off or the window shut, the module not configured), then two the queue adds: a close
of the window waiting on the queue, since a driver let in behind it would be returned the moment
it ran, and a bot that may not create the channel, or a category with no room left, each refused
naming the cause so that nothing is left to stop the queue. Its jobs are:

- `delete_old_channel` takes down the channel an earlier signup of the driver left standing, a
  channel already gone being nothing to do;
- `create_channel` creates the driver's channel, **its record saving the wizard row** in the save
  that marks the job done, so the row names a channel only once it is certain to stand. A job
  that created a channel and failed to save its row keeps the channel's id; the next try deletes
  it before it creates another, so no channel is ever left unnamed;
- `start` (a save) moves the driver to Pending Signup Completion and takes away the timers an
  earlier signup left, and `arm_inactivity` arms the 24 hours they have to answer, from the
  moment the job runs;
- `post_welcome` takes the typing away where the first question is answered by a button, and posts
  the welcome and that question in the new channel, with the "Signup | Started" line.

Cancel Signup asks `signup.end`, a change of the league lane too: its check refuses a signup that
has already ended, and its one job `end` (a save) returns the driver to Not Signed Up, ends the
wizard, takes away the timers, writes the "Withdrawn" line and asks back `signup.closing` (signup's
closing jobs, `closing_steps`) on the driver's own lane, so that a closing notice Discord refuses
stops that lane alone and the league's change is done.

A held channel's deletion asks `signup.channel.delete` (`channel_delete_change`): the hold's
24-hour timer's own, on the driver's lane, and `/driver reassign`'s for the replaced account's channel, on
the league's. The one job `delete_channel` deletes the channel, a channel already gone being
nothing to do, and its record deletes the wizard's row; Discord's refusal stops the lane that
asked.

The reason a manager types after Reject asks `signup.end` the same way, through the manager's
stored press: the message is not deleted at once, but by the first job of the `signup.closing` it
asks back, ahead of the notice that gives it.

A job that fails stops the queue (core specification, "How a change is carried out").
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.models.change import (
    LEAGUE_LANE,
    ChangeOrigin,
    FollowOn,
    PlannedStep,
    StepFailedOnDiscord,
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
    unfinished,
)
from leaguebot.core.db.database import get_connection
from leaguebot.core.services import track_service
from leaguebot.core.services.driver_service import ALLOWED_TRANSITIONS
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import abandoned_line, name_of_member
from leaguebot.core.utils.member_names import member_named
from leaguebot.signup.models.signup_module import WizardState
from leaguebot.signup.services.closing_steps import (
    ARM_DELETION,
    CLOSE_SIGNUP,
    DELETE_KIND,
    END,
    EXPIRED,
    EXPIRED_NOTICE,
    INACTIVITY_TIMER,
    KIND as CLOSING,
    SIGNUP_NOTICE,
    closing_steps,
)
from leaguebot.signup.services.signup_posting import delete_channel, post
from leaguebot.signup.services.signup_views import _BUTTON_OF, _NOTHING_DONE, IN_PROGRESS_STATES
from leaguebot.signup.services.wizard_service import WizardService, build_step_view

if TYPE_CHECKING:
    from leaguebot.core.services.change_queue import ChangeQueue
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.team_service import TeamService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

START = "signup.start"
#: The kind of the typed answer a signup's driver gives, which an expiry waits behind (Q22).
ANSWER = "signup.answer"
DELETE_OLD_CHANNEL = "delete_old_channel"
CREATE_CHANNEL = "create_channel"
START_JOB = "start"
ARM_INACTIVITY = "arm_inactivity"
POST_WELCOME = "post_welcome"
#: The job that deletes a held signup channel.
DELETE_CHANNEL = "delete_channel"
#: The job that ends a driver's signup (the kind, `END`, is in `closing_steps`).
END_JOB = "end"
#: The job, before `end`, that names the driver for the lines `end` writes: the lookup uses a
#: connection of its own, which a save must not wait on.
NAME_JOB = "name_driver"
#: Why a signup ends: the driver pressed Cancel Signup.
WITHDRAWN = "withdrawn"
#: Why a signup ends: a manager pressed Reject and typed the reason.
REJECTED = "rejected"
#: What a manager is told whose reason arrives after the signup moved on.
REJECT_MOVED_ON = (
    f"⛔ This signup has moved on since you pressed {_BUTTON_OF['reject']}. "
    f"{_NOTHING_DONE['reject']}."
)
#: What a driver who presses Cancel Signup on a signup that has already ended is told.
SIGNUP_ENDED = "This signup has already ended. Nothing was changed."
#: What the driver's channel is told when they withdraw.
WITHDRAWN_NOTICE = (
    "❌ You have withdrawn your signup. This channel will be automatically deleted in 24 hours."
)
#: How long a driver has to answer before their signup lapses.
INACTIVITY = timedelta(hours=24)
#: The most channels Discord lets a category hold.
CATEGORY_LIMIT = 50

__all__ = [
    "ARM_INACTIVITY", "CREATE_CHANNEL", "DELETE_CHANNEL", "DELETE_KIND", "DELETE_OLD_CHANNEL", "END",
    "END_JOB", "EXPIRED", "INACTIVITY", "NAME_JOB",
    "POST_WELCOME", "REJECTED", "REJECT_MOVED_ON", "SIGNUP_ENDED", "START", "START_JOB", "WITHDRAWN",
    "WITHDRAWN_NOTICE",
    "channel_delete_change", "end_change", "start_change",
]


def start_change(
    *,
    modules: "ModuleService",
    config: "ConfigService",
    signup: Callable[[], "SignupModuleService"],
    drivers: Callable[[], "DriverService"],
    teams: Callable[[], "TeamService"],
    wizard: Callable[[], WizardService],
    queue_of: Callable[[], "ChangeQueue"],
    closing_kinds: tuple[str, ...],
    cancel_timers: Callable[[str], None],
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
) -> ChangeType:
    """The `signup.start` change type, for the builder to register.

    The payload is ``user_id`` (the driver's account, as text). *closing_kinds* are the kinds of
    the changes that close the window, one of which waiting on the queue refuses a Sign Up.
    *cancel_timers* takes away a driver's timers, *wizard* is the service whose prompt the welcome is
    made of and *queue_of* the change queue the welcome's buttons are handed.

    The driver's status moves only where the channel was made: `start`, like the 24 hours and the
    welcome, is not due where a Discard dropped `create_channel`, so that the driver is left able
    to press Sign Up again, as the outcome tells them.
    """

    async def check(ctx: CheckContext) -> Verdict:
        user_id = str(ctx.payload["user_id"])
        server_cfg = await config.get_server_config()
        # A real driver never joins a server that is under test: test mode and a real league may
        # not share one, and leaving test mode deletes every fake driver.
        if server_cfg is not None and server_cfg.test_mode_active:
            return Verdict.refuse(
                "⛔ Signups are closed while this server is in test mode. "
                "Ask an admin to turn it off."
            )
        # A past account of a driver signs nobody up (issue #243).
        current = await drivers().current_account(user_id)
        if current != user_id:
            return Verdict.refuse(
                f"⛔ This account is a past account of a driver in this league. Sign up from "
                f"<@{current}>, or ask a league manager to make this account the current one."
            )
        profile = await drivers().get_profile(user_id)
        if profile is not None and profile.current_state != DriverState.NOT_SIGNED_UP:
            if profile.current_state in IN_PROGRESS_STATES:
                return Verdict.refuse(
                    "⛔ You already have a signup in progress — check your private wizard channel."
                )
            # Every remaining state is an approved one (`APPROVED_STATES` and
            # `IN_PROGRESS_STATES` cover the six): a new state gets a wrong message rather than a
            # wrong signup.
            return Verdict.refuse(
                "⛔ Your signup has already been approved. You cannot sign up again."
            )
        # A Sign Up button the close could not delete stays pressable after the window shut.
        cfg = await signup().get_config()
        if not await modules.is_signup_enabled() or (cfg is not None and not cfg.signups_open):
            return Verdict.refuse("⛔ Signups are closed.")
        if cfg is None:
            return Verdict.refuse("❌ Signup module is not configured. Contact an admin.")
        if await unfinished(ctx.db_path, closing_kinds):
            return Verdict.refuse(
                "⛔ Signups are closing. Try again once they have closed, if they reopen."
            )
        guild = await league_guild(ctx.bot)
        beside = None if guild is None or cfg.signup_channel_id is None else guild.get_channel(
            int(cfg.signup_channel_id)
        )
        category = getattr(beside, "category", None)
        if guild is not None:
            allowed = (
                category.permissions_for(guild.me) if category is not None
                else guild.me.guild_permissions
            )
            where = f"in **{category.name}**" if category is not None else "on the server"
            if not allowed.manage_channels:
                return Verdict.refuse(
                    f"⛔ The bot cannot create your signup channel: it lacks the **Manage "
                    f"Channels** permission {where}. Ask an admin."
                )
            if category is not None and len(category.channels) >= CATEGORY_LIMIT:
                return Verdict.refuse(
                    f"⛔ The bot cannot create your signup channel: the category "
                    f"**{category.name}** is full. Ask an admin."
                )
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {"user_id": str(payload["user_id"])}
        return tuple(
            PlannedStep(name, dict(shared))
            for name in (DELETE_OLD_CHANNEL, CREATE_CHANNEL, START_JOB, ARM_INACTIVITY, POST_WELCOME)
        )

    async def _guild(ctx: StepContext) -> discord.Guild:
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise StepFailedOnDiscord("the league's server could not be found")
        return guild

    def _created(ctx: StepContext) -> int | None:
        for view in ctx.steps:
            if view.name == CREATE_CHANNEL and view.done:
                channel_id = (view.result or {}).get("channel_id")
                return None if channel_id is None else int(channel_id)
        return None

    async def delete_old(ctx: StepContext) -> StepResult:
        existing = await signup().get_wizard(str(ctx.step_payload["user_id"]))
        guild = await _guild(ctx)
        if existing is None or existing.signup_channel_id is None:
            return StepResult(result={})
        old = guild.get_channel(existing.signup_channel_id)
        if old is None:
            return StepResult(result={"gone": True})
        await delete_channel(old, reason="Driver re-started signup wizard")
        return StepResult(result={})

    @posts_in_a_job
    async def create_channel(ctx: StepContext) -> StepResult:
        guild = await _guild(ctx)
        kept = (ctx.kept or {}).get("channel_id")
        if kept is not None:
            # A try that created the channel and failed to save its row left it standing.
            earlier = guild.get_channel(int(kept))
            if earlier is not None:
                try:
                    await delete_channel(earlier, reason="Replaced by a later try")
                except discord.NotFound:
                    pass
                except discord.HTTPException as error:
                    raise StepFailedOnDiscord(
                        "the channel an earlier try made could not be deleted",
                        result=dict(ctx.kept or {}),
                    ) from error
        user_id = str(ctx.step_payload["user_id"])
        member = guild.get_member(int(user_id))
        if member is None:
            raise StepFailedOnDiscord("the member signing up could not be found on the server")
        cfg = await signup().get_config()
        server_cfg = await config.get_server_config()
        try:
            made = await WizardService._create_wizard_channel(
                guild, member, cfg.signup_channel_id if cfg else None,
                server_cfg.interaction_role_id if server_cfg else None,
                server_cfg.league_admin_role_id if server_cfg else None,
            )
        except discord.HTTPException as error:
            raise StepFailedOnDiscord(
                "the signup channel could not be created",
                notice="the bot needs the Manage Channels permission and room in its category",
            ) from error
        # The configuration is frozen as it stands as the channel is made. A driver is offered,
        # and answers with, the name every post shows; the shorthand is the league's own, for
        # typing, and is not put to a driver (#381).
        snapshot = await signup().capture_config_snapshot()
        default_teams = await teams().get_default_teams()
        snapshot.team_names = [t.full_name for t in default_teams if not t.is_reserve]
        first_state = (
            WizardState.COLLECTING_NATIONALITY
            if snapshot.nationality_required
            else WizardState.COLLECTING_PLATFORM
        )
        return StepResult(result={
            "channel_id": made.id,
            "state": first_state.value,
            "snapshot": json.dumps(signup()._snapshot_to_dict(snapshot)),
            "draft": {
                "discord_username": member.name,
                "server_display_name": member.display_name,
            },
            "at": now().isoformat(),
        })

    async def save_wizard(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        made = result.result or {}
        # The start of a signup: any closing notice a past close still owes the driver goes.
        await signup().save_wizard_row_on(
            db, discord_user_id=str(ctx.step_payload["user_id"]), wizard_state=made["state"],
            signup_channel_id=int(made["channel_id"]), snapshot_json=made["snapshot"],
            draft_answers=made["draft"], current_lap_track_index=0,
            last_activity_at=made["at"], starting=True,
        )

    async def start(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        cancel_timers(user_id)
        wizard()._forget_correction(user_id)
        await drivers().transition_on(db, user_id, DriverState.PENDING_SIGNUP_COMPLETION)
        return StepResult(result={})

    async def arm_inactivity(ctx: StepContext) -> StepResult:
        arm_once(INACTIVITY_TIMER, str(ctx.step_payload["user_id"]), now() + INACTIVITY)
        return StepResult(result={})

    async def has_channel(ctx: StepContext) -> bool:
        return _created(ctx) is not None

    @posts_in_a_job
    async def post_welcome(ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        guild = await _guild(ctx)
        found = guild.get_channel(_created(ctx) or 0)
        if not isinstance(found, discord.TextChannel):
            raise StepFailedOnDiscord("the signup channel could not be found to post in")
        record = await signup().get_wizard(user_id)
        if record is None or record.config_snapshot is None:
            raise StepFailedOnDiscord("the signup's record could not be found")
        member = guild.get_member(int(user_id))
        if record.wizard_state in WizardService._BUTTON_ONLY_STATES and member is not None:
            try:
                await WizardService._revoke_driver_write(found, member)
            except discord.HTTPException as error:
                raise StepFailedOnDiscord(
                    f"typing in the signup channel {found.id} could not be taken away"
                ) from error
        async with get_connection(ctx.db_path) as db:
            track_map = await track_service.get_track_name_map(db)
        await post(
            found,
            f"Welcome to the signup wizard, <@{user_id}>!\n"
            + WizardService._prompt_for_state(
                record.wizard_state, record.config_snapshot, track_name_map=track_map
            ),
            view=build_step_view(
                queue_of(), record.wizard_state, user_id, record.config_snapshot.team_names
            ),
            mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(id=int(user_id))]
            ),
            refused=f"the welcome could not be posted in channel {found.id}",
        )
        return StepResult(result={}, lines=(f"{ctx.named} | Signup | Started",))

    async def describe_delete(_ctx: StepContext) -> str:
        return "deleting the channel of the driver's earlier signup"

    async def describe_create(_ctx: StepContext) -> str:
        return "creating the driver's signup channel"

    async def describe_start(_ctx: StepContext) -> str:
        return "starting the driver's signup"

    async def describe_arm(_ctx: StepContext) -> str:
        return "arming the signup's 24 hours"

    async def describe_welcome(_ctx: StepContext) -> str:
        return "welcoming the driver in their signup channel"

    def outcome(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        created = views.get(CREATE_CHANNEL)
        if created is None or "discarded" in (created.result or {}):
            return (
                "⚠️ Your signup channel could not be created and was discarded. "
                "Press Sign Up again."
            )
        text = f"✅ Your signup channel has been created: <#{(created.result or {})['channel_id']}>"
        # Each job an admin discarded is named, so that the reply says what was not done.
        notes = {
            DELETE_OLD_CHANNEL: (
                "The channel of your earlier signup could not be deleted and was left standing: "
                "ask a league manager to delete it."
            ),
            START_JOB: (
                "Your signup was not started and was discarded: press Sign Up again to start "
                "over."
            ),
            ARM_INACTIVITY: (
                "Your signup's 24 hours were not armed and were discarded, so it will not lapse "
                "by itself until the bot starts again."
            ),
            POST_WELCOME: "The welcome could not be posted and was discarded.",
        }
        for name, note in notes.items():
            view = views.get(name)
            if view is not None and "discarded" in (view.result or {}):
                text += f"\n{note}"
        return text

    steps: dict[str, Step] = {
        DELETE_OLD_CHANNEL: Step(
            DELETE_OLD_CHANNEL, StepKind.DELETE, delete_old, describe=describe_delete
        ),
        CREATE_CHANNEL: Step(
            CREATE_CHANNEL, StepKind.ACT, create_channel, describe=describe_create,
            record=save_wizard,
        ),
        START_JOB: Step(
            START_JOB, StepKind.SAVE, start, still_due=has_channel, describe=describe_start
        ),
        ARM_INACTIVITY: Step(
            ARM_INACTIVITY, StepKind.ACT, arm_inactivity, still_due=has_channel,
            describe=describe_arm,
        ),
        POST_WELCOME: Step(
            POST_WELCOME, StepKind.ACT, post_welcome, still_due=has_channel,
            describe=describe_welcome,
        ),
    }
    return ChangeType(
        kind=START,
        opening=(),
        plan=plan,
        steps=steps,
        check=check,
        key=lambda payload: f"{START}:{payload['user_id']}",
        doing=lambda payload: "Signing up",
        outcome=outcome,
    )


def end_change(
    *,
    signup: Callable[[], "SignupModuleService"],
    drivers: Callable[[], "DriverService"],
    cancel_timers: Callable[[str], None],
    wizard: Callable[[], WizardService],
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
) -> ChangeType:
    """The `signup.end` change type, for the builder to register.

    The payload is ``user_id`` (the driver's account, as text) and ``reason``: `WITHDRAWN` (the
    driver pressed Cancel Signup), `REJECTED` (a manager pressed Reject and typed the reason: the
    payload then carries ``actor_id`` and ``actor_name`` (the manager), ``text`` (the reason) and
    ``reason_message_id`` (the message it was typed in)), or `EXPIRED` (their 24 hours ran out,
    asked by the timer or at a restart, with the signup channel's id as ``channel_id``). Its job
    `end` is a save, after `name_driver`, which finds the driver's name for the line (the lookup
    uses a connection of its own, which a save must not wait on): the driver is moved to Not
    Signed Up and the wizard ended in the same save, the correction timeout and the timers taken
    away and the line written ("Withdrawn", "Rejected" with the manager and the reason, or the
    lapse).

    A withdrawal is refused by the check where the signup has already ended (the transition to Not
    Signed Up is not one the state machine allows from where they stand), telling the driver so and
    changing nothing; it asks back `signup.closing` for the driver's channel, which holds the
    notice, the lock and the deletion's arming on the channel's own lane. A rejection is refused
    where the driver no longer awaits review (another manager approved them, they withdrew, a
    correction was asked for), telling the manager so and changing nothing, and otherwise asks back
    the same change, whose first job deletes the manager's reason message and whose notice gives
    the reason. An expiry is the league's
    whole (a timer is a command's, decision 31): the change carries those three jobs itself, after
    `end`. It is not due, and is dropped, where the driver's wizard is parked or they are not
    mid-signup any longer, and where an answer typed before the 24 hours ran out is still waiting
    on the driver's lane (the time typed counts, Q22). *cancel_timers* takes away a driver's
    timers, *wizard* is the service whose correction timeout is forgotten, and *now* and
    *arm_once* are the queue's clock and the scheduler service's one-off timer, for the deletion.
    """
    #: The states from which a wizard still collecting answers can expire.
    expirable = (DriverState.PENDING_SIGNUP_COMPLETION, DriverState.PENDING_DRIVER_CORRECTION)

    def last_activity(record: Any) -> datetime | None:
        """When the driver last answered (or the signup began), or None where none is recorded."""
        if not record.last_activity_at:
            return None
        last = datetime.fromisoformat(record.last_activity_at)
        return last if last.tzinfo is not None else last.replace(tzinfo=timezone.utc)

    async def answered_in_time(ctx: CheckContext, record: Any) -> bool:
        """Whether an answer typed within the 24 hours waits on the driver's lane. An answer
        carrying no time (a button pressed) was pressed on a question still standing."""
        last = last_activity(record)
        if last is None:
            return False
        deadline = last + INACTIVITY
        for waiting in await unfinished(ctx.db_path, [ANSWER]):
            if record.signup_channel_id is None or (
                int(waiting.get("channel_id", 0)) != int(record.signup_channel_id)
            ):
                continue
            typed = waiting.get("typed_at")
            if typed is None or datetime.fromisoformat(typed) <= deadline:
                return True
        return False

    async def check(ctx: CheckContext) -> Verdict:
        user_id = str(ctx.payload["user_id"])
        profile = await drivers().get_profile(user_id)
        if ctx.payload.get("reason") == EXPIRED:
            record = await signup().get_wizard(user_id)
            if record is None or record.wizard_state == WizardState.UNENGAGED:
                return Verdict.not_due("the signup is no longer collecting answers")
            if profile is None or profile.current_state not in expirable:
                return Verdict.not_due("the driver is no longer part-way through signing up")
            if await answered_in_time(ctx, record):
                return Verdict.not_due("an answer typed in time waits its turn")
            # A timer that fires early (an earlier answer's, a correction's) never ends a live
            # signup: the 24 hours are counted from the driver's last answer.
            last = last_activity(record)
            if last is not None and last + INACTIVITY > now():
                return Verdict.not_due("the driver has answered within the last 24 hours")
            return Verdict.go()
        if ctx.payload.get("reason") == REJECTED:
            # A signup that has moved on since the press: approved by another manager, withdrawn,
            # or already sent back for a correction (#492). The reason is too late to act on.
            if profile is None or profile.current_state != DriverState.PENDING_ADMIN_APPROVAL:
                return Verdict.refuse(REJECT_MOVED_ON)
            return Verdict.go()
        if profile is None or DriverState.NOT_SIGNED_UP not in ALLOWED_TRANSITIONS.get(
            profile.current_state, set()
        ):
            if ctx.origin is ChangeOrigin.MEMBER:
                return Verdict.refuse(f"⛔ {SIGNUP_ENDED}", SIGNUP_ENDED)
            return Verdict.not_due("the signup has already ended")
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        user_id = str(payload["user_id"])
        shared_end = {"user_id": user_id, "reason": payload["reason"]}
        if payload["reason"] == REJECTED:
            shared_end.update({
                key: payload[key]
                for key in ("actor_id", "actor_name", "text", "reason_message_id")
            })
        planned = [PlannedStep(NAME_JOB, {"user_id": user_id}), PlannedStep(END_JOB, shared_end)]
        channel_id = payload.get("channel_id")
        if payload["reason"] == EXPIRED and channel_id is not None:
            shared = {"user_id": user_id, "channel_id": int(channel_id)}
            planned += [
                PlannedStep(SIGNUP_NOTICE, {**shared, "notice": EXPIRED_NOTICE}),
                PlannedStep(CLOSE_SIGNUP, shared),
                PlannedStep(ARM_DELETION, shared),
            ]
        return tuple(planned)

    async def end(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        user_id = str(ctx.step_payload["user_id"])
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_wizard_records WHERE discord_user_id = ?",
            (user_id,),
        )
        wizard_row = await cursor.fetchone()
        await drivers().transition_on(db, user_id, DriverState.NOT_SIGNED_UP)
        await signup().end_wizard_on(db, user_id)
        cancel_timers(user_id)
        wizard()._forget_correction(user_id)
        named = member_named(None, int(user_id))
        for view in ctx.steps:
            if view.name == NAME_JOB and view.done:
                named = str((view.result or {}).get("named") or named)
        if ctx.step_payload["reason"] == EXPIRED:
            # The lapse is the whole of the expiry's line: its notice, lock and deletion are the
            # change's own jobs after this one.
            return StepResult(result={}, lines=(abandoned_line(
                named, "the signup wizard", lapsed=True,
                detail=(
                    "The signup was cancelled after 24 hours without an answer. "
                    "They may press Sign Up again while signups are open."
                ),
            ),))
        follow_ons: tuple[FollowOn, ...] = ()
        if ctx.step_payload["reason"] == REJECTED:
            payload = ctx.step_payload
            manager = member_named(payload["actor_name"], int(payload["actor_id"]))
            if wizard_row is not None and wizard_row["signup_channel_id"] is not None:
                follow_ons = (FollowOn(
                    CLOSING,
                    {
                        "user_id": user_id, "channel_id": int(wizard_row["signup_channel_id"]),
                        "notice": (
                            f"<@{user_id}> ❌ Your signup has been rejected by "
                            f"**{payload['actor_name']}**.\n**Reason:** {payload['text']}\n"
                            "This channel will be automatically deleted in 24 hours."
                        ),
                        "reason_message_id": int(payload["reason_message_id"]),
                    },
                    "Closing a rejected signup",
                ),)
            return StepResult(
                result={},
                lines=(
                    f"{manager} | Signup | Rejected\n  driver: {named}\n  reason: {payload['text']}",
                ),
                follow_ons=follow_ons,
            )
        if wizard_row is not None and wizard_row["signup_channel_id"] is not None:
            follow_ons = (FollowOn(
                CLOSING,
                {
                    "user_id": user_id, "channel_id": int(wizard_row["signup_channel_id"]),
                    "notice": WITHDRAWN_NOTICE,
                },
                "Closing a withdrawn signup",
            ),)
        return StepResult(result={}, lines=(f"{named} | Signup | Withdrawn",), follow_ons=follow_ons)

    async def name_driver(ctx: StepContext) -> StepResult:
        return StepResult(result={
            "named": await name_of_member(ctx.bot, int(ctx.step_payload["user_id"]))
        })

    async def ended(ctx: StepContext) -> bool:
        """Whether the signup was ended, for the notice, lock and deletion that follow `end`: not
        where an admin discarded `end`, which leaves the driver still part-way through signing up
        (Discard drops that one job and the later ones check they are still due, core spec)."""
        return not any(
            view.name == END_JOB and "discarded" in (view.result or {}) for view in ctx.steps
        )

    async def describe_name(ctx: StepContext) -> str:
        return f"finding the name of <@{ctx.step_payload['user_id']}> for the record"

    async def describe_end(ctx: StepContext) -> str:
        return f"ending the signup of <@{ctx.step_payload['user_id']}>"

    def outcome(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        ended = views.get(END_JOB)
        expired = ctx.payload.get("reason") == EXPIRED
        rejected = ctx.payload.get("reason") == REJECTED
        if ended is not None and "discarded" in (ended.result or {}):
            if rejected:
                return "⚠️ The signup was not rejected: the job was discarded."
            return (
                "⚠️ The signup was not ended: the job was discarded." if expired
                else "⚠️ Your signup was not withdrawn: the job was discarded."
            )
        if rejected:
            return "✅ Signup rejected."
        if expired:
            left = [name for name in (SIGNUP_NOTICE, CLOSE_SIGNUP, ARM_DELETION)
                    if name in views and "discarded" in (views[name].result or {})]
            return "✅ The signup has expired." + (
                f"\nDiscarded, and not done: {', '.join(left)}." if left else ""
            )
        return "✅ Your signup has been withdrawn."

    return ChangeType(
        kind=END,
        opening=(),
        plan=plan,
        steps={
            NAME_JOB: Step(NAME_JOB, StepKind.ACT, name_driver, describe=describe_name),
            END_JOB: Step(END_JOB, StepKind.SAVE, end, describe=describe_end),
            **{
                name: (
                    dataclasses.replace(step, still_due=ended)
                    if name in (SIGNUP_NOTICE, CLOSE_SIGNUP, ARM_DELETION) else step
                )
                for name, step in closing_steps(now=now, arm_once=arm_once).items()
            },
        },
        check=check,
        key=lambda payload: f"{END}:{payload['user_id']}",
        doing=lambda payload: (
            "Ending a signup" if payload.get("reason") == EXPIRED
            else "Rejecting a signup" if payload.get("reason") == REJECTED
            else "Withdrawing a signup"
        ),
        outcome=outcome,
    )


def channel_delete_change(*, signup: Callable[[], "SignupModuleService"]) -> ChangeType:
    """The `signup.channel.delete` change type, for the builder to register.

    The payload is ``user_id`` (the account whose wizard holds the channel), ``channel_id`` (the
    channel held) and ``own_lane``. A hold's 24-hour timer asks it on the driver's own lane
    (``channel:<id>``, `own_lane` true), where Discord's refusal stops that lane alone; the
    channel of the account `/driver reassign` replaced is deleted on the league's, as the
    command's change. It is not due, and is dropped, where the driver's wizard no longer names the
    channel: a new signup, or a move to another account, has taken it over. Its one job
    `delete_channel` deletes the channel, one already gone being nothing to do, and its record
    deletes the wizard's row in the save that marks the job done, so the row names a channel only
    while the channel may stand. *signup* is a getter, so that the service is the one the bot
    holds when the job runs.
    """

    async def check(ctx: CheckContext) -> Verdict:
        row = await signup().get_wizard(str(ctx.payload["user_id"]))
        if row is None or row.signup_channel_id != int(ctx.payload["channel_id"]):
            return Verdict.not_due("the driver no longer holds that signup channel")
        return Verdict.go()

    async def held(ctx: StepContext) -> bool:
        row = await signup().get_wizard(str(ctx.step_payload["user_id"]))
        return row is not None and row.signup_channel_id == int(ctx.step_payload["channel_id"])

    @posts_in_a_job
    async def delete(ctx: StepContext) -> StepResult:
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise StepFailedOnDiscord("the league's server could not be found")
        found = guild.get_channel(int(ctx.step_payload["channel_id"]))
        if found is None:
            return StepResult(result={"gone": True})
        try:
            await delete_channel(found, reason="Signup wizard channel hold expired")
        except discord.NotFound:
            return StepResult(result={"gone": True})
        except discord.HTTPException as error:
            raise StepFailedOnDiscord(
                f"the signup channel {found.id} could not be deleted"
            ) from error
        return StepResult(result={})

    async def forget(db: aiosqlite.Connection, ctx: StepContext, _result: StepResult) -> None:
        await signup().delete_wizard_on(db, str(ctx.step_payload["user_id"]))

    async def describe(ctx: StepContext) -> str:
        return f"deleting the signup channel <#{ctx.step_payload['channel_id']}>"

    def outcome(ctx: OutcomeContext) -> str:
        deleted = {view.name: view for view in ctx.steps}.get(DELETE_CHANNEL)
        if deleted is not None and "discarded" in (deleted.result or {}):
            return "⚠️ The signup channel was not deleted: the job was discarded."
        return "✅ The signup channel has been deleted."

    return ChangeType(
        kind=DELETE_KIND,
        opening=(),
        plan=lambda payload: (PlannedStep(DELETE_CHANNEL, {
            "user_id": str(payload["user_id"]), "channel_id": int(payload["channel_id"]),
        }),),
        steps={
            DELETE_CHANNEL: Step(
                DELETE_CHANNEL, StepKind.DELETE, delete, still_due=held, describe=describe,
                record=forget,
            ),
        },
        check=check,
        key=lambda payload: f"{DELETE_KIND}:{payload['user_id']}",
        doing=lambda payload: "Deleting a signup channel",
        outcome=outcome,
        lane=lambda payload: (
            f"channel:{int(payload['channel_id'])}" if payload.get("own_lane") else LEAGUE_LANE
        ),
        writes=("signup_wizard_records",),
    )
