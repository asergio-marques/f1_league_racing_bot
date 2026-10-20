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

A job that fails stops the queue (core specification, "How a change is carried out").
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.models.change import (
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
from leaguebot.core.utils.log_lines import name_of_member
from leaguebot.signup.models.signup_module import WizardState
from leaguebot.signup.services.closing_steps import INACTIVITY_TIMER, KIND as CLOSING
from leaguebot.signup.services.signup_posting import delete_channel, post
from leaguebot.signup.services.signup_views import IN_PROGRESS_STATES
from leaguebot.signup.services.wizard_service import WizardService, build_step_view

if TYPE_CHECKING:
    from leaguebot.core.services.change_queue import ChangeQueue
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.team_service import TeamService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

START = "signup.start"
DELETE_OLD_CHANNEL = "delete_old_channel"
CREATE_CHANNEL = "create_channel"
START_JOB = "start"
ARM_INACTIVITY = "arm_inactivity"
POST_WELCOME = "post_welcome"
#: The kind of the change that ends a driver's signup, and its one job.
END = "signup.end"
END_JOB = "end"
#: Why a signup ends: the driver pressed Cancel Signup.
WITHDRAWN = "withdrawn"
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
    "ARM_INACTIVITY", "CREATE_CHANNEL", "DELETE_OLD_CHANNEL", "END", "END_JOB", "INACTIVITY",
    "POST_WELCOME", "SIGNUP_ENDED", "START", "START_JOB", "WITHDRAWN", "WITHDRAWN_NOTICE",
    "end_change", "start_change",
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
        welcome = views.get(POST_WELCOME)
        if welcome is not None and "discarded" in (welcome.result or {}):
            text += "\nThe welcome could not be posted and was discarded."
        return text

    steps: dict[str, Step] = {
        DELETE_OLD_CHANNEL: Step(
            DELETE_OLD_CHANNEL, StepKind.DELETE, delete_old, describe=describe_delete
        ),
        CREATE_CHANNEL: Step(
            CREATE_CHANNEL, StepKind.ACT, create_channel, describe=describe_create,
            record=save_wizard,
        ),
        START_JOB: Step(START_JOB, StepKind.SAVE, start, describe=describe_start),
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
) -> ChangeType:
    """The `signup.end` change type, for the builder to register.

    The payload is ``user_id`` (the driver's account, as text) and ``reason``, here ``"withdrawn"``.
    The check refuses the driver's press where the signup has already ended (the transition to Not
    Signed Up is not one the state machine allows from where they stand), telling them so and
    changing nothing. Its one job `end` is a save: the driver is moved to Not Signed Up and the
    wizard ended in the same save, the correction timeout and the timers taken away and the
    "Withdrawn" line written; it asks back `signup.closing` for the driver's channel, which holds
    the notice, the lock and the deletion's arming on the channel's own lane. *cancel_timers* takes
    away a driver's timers and *wizard* is the service whose correction timeout is forgotten.
    """

    async def check(ctx: CheckContext) -> Verdict:
        user_id = str(ctx.payload["user_id"])
        profile = await drivers().get_profile(user_id)
        if profile is None or DriverState.NOT_SIGNED_UP not in ALLOWED_TRANSITIONS.get(
            profile.current_state, set()
        ):
            if ctx.origin is ChangeOrigin.MEMBER:
                return Verdict.refuse(f"⛔ {SIGNUP_ENDED}", SIGNUP_ENDED)
            return Verdict.not_due("the signup has already ended")
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (PlannedStep(END_JOB, {"user_id": str(payload["user_id"]), "reason": payload["reason"]}),)

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
        named = await name_of_member(ctx.bot, int(user_id))
        follow_ons: tuple[FollowOn, ...] = ()
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

    async def describe_end(ctx: StepContext) -> str:
        return f"ending the signup of <@{ctx.step_payload['user_id']}>"

    def outcome(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        ended = views.get(END_JOB)
        if ended is not None and "discarded" in (ended.result or {}):
            return "⚠️ Your signup was not withdrawn: the job was discarded."
        return "✅ Your signup has been withdrawn."

    return ChangeType(
        kind=END,
        opening=(),
        plan=plan,
        steps={END_JOB: Step(END_JOB, StepKind.SAVE, end, describe=describe_end)},
        check=check,
        key=lambda payload: f"{END}:{payload['user_id']}",
        doing=lambda payload: "Withdrawing a signup",
        outcome=outcome,
    )
