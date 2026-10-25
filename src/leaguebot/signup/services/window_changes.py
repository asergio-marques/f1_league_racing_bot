"""The signup window's opening and closing on the change queue (#439, slice 6b).

`/signup open` asks `signup.open`, whose check holds every refusal the command has always made
(the module, test mode, the configuration, the season's stage, the league's roles, the slots) and
the bot's permissions on the signup channel, so that a refusal names what is wrong before anything
is posted. Its jobs are three:

- `post_info` posts the information message with the Sign Up button, pinging the base role. **The
  window is recorded open in the save that marks this job done** (its record: the window, the
  button's id, the tracks, the season's move to its signup stage, the close time and the
  `SIGNUP_OPEN` audit row), so signups are open only once the message is up. A post Discord
  refuses stops the queue with signups still closed, and a Discard leaves them closed.
- `arm_close` arms the close timer, where a close time was given.
- `delete_closed` takes down the "signups closed" notice the last close left, once the information
  message stands, so that the channel always shows one or the other.

The command reads the close time and the tracks it was given, which are its own input, and the
signup channel and the notice standing in it, which the plan of the jobs reads from the payload.

`signup.close` closes it again, for `/signup close` (and its Confirm Close), the close timer and
the restart past the close time. Its jobs, in order, are:

- `return_drivers` (a save) returns each driver still filling in the wizard to Not Signed Up, ends
  their wizard, cancels their timers and plans every job that follows, so that a driver returned
  is never left without their notice;
- `delete_button` takes the Sign Up button down, and `post_closed` posts "Signups are now closed"
  and, in the save that marks it done, records the window closed, moves the season on and audits
  the close: no window is ever recorded closed with its season left behind, and a post Discord
  refuses stops the queue with signups still open;
- per returned driver, `signup_notice`, `close_signup` and `arm_deletion` (signup's closing jobs);
- `close` (a save) writes the one line, and names what a Discard left undone.

A job that fails stops the queue (core specification, "How a change is carried out"). A close a
member asked for is judged again as it comes up to run: a window closed since, reopened on
another button, or given a close time is refused, and nothing is closed. A close the bot asked
for finds a window no longer the one it was asked about no longer due.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from collections.abc import Collection
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.models.change import (
    AuditRecord,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services import standing_posts, track_service
from leaguebot.core.services.driver_service import write_transition
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.services.season_end_changes import season_ending, season_ending_jobs
from leaguebot.core.services.season_lifecycle_service import (
    WINDOW_CLOSED_NOTICE,
    WINDOW_NOT_CLOSED,
    WINDOW_OPENS_FROM,
    advance_on_window_open_on,
    live_season_stage,
)
from leaguebot.core.utils.channel_guard import league_role_faults
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.closing_steps import (
    ARM_DELETION,
    CLOSE_SIGNUP,
    DELETE_BUTTON,
    POST_CLOSED,
    SIGNUP_NOTICE,
    closing_steps,
    window_close_steps,
)
from leaguebot.signup.services.signup_posting import post
from leaguebot.signup.services.signup_views import SignupButtonView

if TYPE_CHECKING:
    from leaguebot.core.services.change_queue import ChangeQueue
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

OPEN = "signup.open"
POST_INFO = "post_info"
ARM_CLOSE = "arm_close"
DELETE_CLOSED = "delete_closed"
#: What a league is told when the module is off.
MODULE_OFF = "⛔ Signup module is not enabled. Use `/module enable signup` first."
#: What the bot needs on the signup channel to post the Sign Up button.
NEEDED = "**View Channel**, **Send Messages** and **Embed Links**"

CLOSE = "signup.close"
RETURN_DRIVERS = "return_drivers"
CLOSE_LINE = "close"
#: How a close is audited, by who asked for it.
AUDIT_CLOSE = "SIGNUP_CLOSE"
AUDIT_FORCE_CLOSE = "SIGNUP_FORCE_CLOSE"
AUDIT_AUTO_CLOSE = "SIGNUP_AUTO_CLOSE"
#: What a close the bot ran is headed with, by its cause.
AUTO_CLOSE_HEAD = "🔒 Signups closed automatically at their set time"
#: What a close that failed a step leaves, one sentence each, for the reply and the line.
BUTTON_NOT_REMOVED = "The Sign Up button could not be removed from the signup channel."
NOT_RETURNED = "The drivers still signing up could not be returned to Not Signed Up."

__all__ = [
    "ARM_CLOSE", "AUDIT_AUTO_CLOSE", "AUDIT_CLOSE", "AUDIT_FORCE_CLOSE", "CLOSE", "CLOSE_LINE",
    "DELETE_CLOSED", "OPEN", "POST_INFO", "RETURN_DRIVERS", "armed_close_refusal",
    "season_end_close_refusal",
    "discarded", "drivers_returned", "failed_jobs", "return_drivers_step", "window_close_change",
    "window_open_change", "window_was_closed",
]


def armed_close_refusal(close_at: str) -> str:
    """Why signups may not be closed by hand while a close time is armed, without its mark.

    It states the armed time and names the command that clears it, as the signup
    specification requires of ``/signup close``. ``/signup close`` and the Confirm Close it
    asks with both refuse with it, so the two cannot word it differently.
    """
    armed = datetime.fromisoformat(close_at)
    if armed.tzinfo is None:
        armed = armed.replace(tzinfo=timezone.utc)
    return (
        f"Signups will auto-close at {discord.utils.format_dt(armed, 'F')} "
        f"({discord.utils.format_dt(armed, 'R')}). "
        "Clear the timer with `/signup close-time cancel` if you need to close manually, or "
        "move it with `/signup close-time modify`."
    )


def _stamp(moment: datetime, fmt: Literal["F", "R"] = "F") -> str:
    """A dynamic timestamp for *moment*, read as UTC where it carries no zone."""
    return discord.utils.format_dt(moment, fmt)


def window_open_change(
    *,
    modules: "ModuleService",
    signup: "SignupModuleService",
    config: "ConfigService",
    schedule_close: Callable[[str], None],
    queue: Callable[[], "ChangeQueue"],
) -> ChangeType:
    """The `signup.open` change type, for the builder to register.

    The payload is ``track_ids`` (the tracks chosen, ids as text), ``close_at`` (the normalised
    close time or None), and ``channel_id`` and ``closed_message_id`` (the signup channel and
    the "signups closed" notice standing in it, each or None). *schedule_close* arms the close
    timer at an ISO 8601 time, and *queue* is the change queue the Sign Up button is handed.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_signup_enabled():
            return Verdict.refuse(MODULE_OFF)
        server_cfg = await config.get_server_config()
        if server_cfg is not None and server_cfg.test_mode_active:
            return Verdict.refuse(
                "⛔ Signups cannot be opened while test mode is active. "
                "Turn it off with `/test-mode toggle` first."
            )
        cfg = await signup.get_config()
        if cfg is None:
            return Verdict.refuse("❌ Signup module is not configured.")
        if cfg.signups_open:
            return Verdict.refuse("❌ Signups are already open.")
        live = await live_season_stage(ctx.db_path)
        if live is None or live[1] not in WINDOW_OPENS_FROM:
            return Verdict.refuse(
                "❌ Signups can only be opened while the season is waiting for its signup "
                "window, once its configuration is confirmed, or while it is ongoing with "
                "no placements left to confirm."
            )
        guild = await league_guild(ctx.bot)
        base_role_id = server_cfg.base_role_id if server_cfg is not None else None
        driver_role_id = server_cfg.driver_role_id if server_cfg is not None else None
        missing = []
        if cfg.signup_channel_id is None:
            missing.append("`signup channel` (use `/signup channel`)")
        if base_role_id is None:
            missing.append("`base role` (use `/bot base-role`)")
        if driver_role_id is None:
            missing.append("`driver role` (use `/bot driver-role`)")
        missing += league_role_faults(guild, base_role_id, driver_role_id)
        if missing:
            return Verdict.refuse(
                "❌ Signups cannot be opened until the signup module's configuration is "
                "put right:\n" + "\n".join(f"  • {m}" for m in missing),
                reason="the signup module's configuration is not put right: " + "; ".join(missing),
            )
        if not await signup.get_slots():
            return Verdict.refuse(
                "❌ At least one availability time slot must be configured before opening signups."
            )
        found = None if guild is None else guild.get_channel(int(cfg.signup_channel_id or 0))
        if not isinstance(found, discord.TextChannel):
            return Verdict.refuse("❌ Configured signup channel not found.")
        have = found.permissions_for(found.guild.me)
        if not (have.view_channel and have.send_messages and have.embed_links):
            return Verdict.refuse(
                f"❌ The bot needs {NEEDED} on {found.mention} to post the Sign Up button. "
                "Signups were not opened."
            )
        return Verdict.go()

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        planned = [PlannedStep(POST_INFO, {
            "channel_id": payload["channel_id"], "track_ids": list(payload["track_ids"]),
            "close_at": payload["close_at"],
        })]
        if payload["close_at"]:
            planned.append(PlannedStep(ARM_CLOSE, {"close_at": payload["close_at"]}))
        if payload["closed_message_id"] is not None and payload["channel_id"] is not None:
            planned.append(PlannedStep(DELETE_CLOSED, {
                "channel_id": payload["channel_id"], "message_id": payload["closed_message_id"],
                "what": "signups closed notice", "division_name": "signups",
            }))
        return tuple(planned)

    async def post_info(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        payload = ctx.step_payload
        guild = await league_guild(ctx.bot)
        found = None if guild is None else guild.get_channel(int(payload["channel_id"]))
        if guild is None or not isinstance(found, discord.TextChannel):
            raise StepFailedOnDiscord("the signup channel could not be found to post in")
        server_cfg = await config.get_server_config()
        base_role_id = server_cfg.base_role_id if server_cfg is not None else None
        base_role = guild.get_role(base_role_id) if base_role_id else None
        track_ids = [str(each) for each in payload["track_ids"]]
        if track_ids:
            async with get_connection(ctx.db_path) as db:
                names = await track_service.get_track_name_map(db)
            tracks_display = "\n".join(f"• {names[each]}" for each in track_ids)
        else:
            tracks_display = "No tracks specified"
        settings = await signup.get_settings()
        slots = await signup.get_slots()
        close_at = payload["close_at"]
        close_line = ""
        if close_at:
            parsed = datetime.fromisoformat(close_at)
            close_line = f"\n**Auto-closes:** {_stamp(parsed)} ({_stamp(parsed, 'R')})"
        role_line = f"\n\n{base_role.mention} — click below to sign up!" if base_role else ""
        time_type = "Time Trial" if settings.time_type == "TIME_TRIAL" else "Short Qualification"
        embed = discord.Embed(
            title="🏁 Driver Signups Are Open!",
            description=(
                "**Available time slots:**\n"
                + "\n".join(f"• {s.display_label}" for s in slots)
                + f"\n\n**Tracks:**\n{tracks_display}"
                + f"\n\n**Time type:** {time_type}"
                + f"\n**Time image proof:** {'Required' if settings.time_image_required else 'Not required'}"
                + f"\n**Nationality:** {'Required' if settings.nationality_required else 'Not required'}"
                + close_line
                + role_line
            ),
            color=discord.Color.green(),
        )
        mentions = (
            discord.AllowedMentions(roles=[base_role]) if base_role
            else discord.AllowedMentions.none()
        )
        message_id = await post(
            found, embed=embed, view=SignupButtonView(queue()), mentions=mentions,
            refused=f"the information message could not be posted in channel {found.id}",
        )
        audited: dict[str, Any] = {"track_ids": track_ids}
        if close_at:
            audited["close_at"] = close_at
        line = f"{ctx.named} | /signup open | Success"
        if track_ids:
            line += f"\n  track_ids: {', '.join(track_ids)}"
        if close_at:
            line += f"\n  close_time: {close_at}"
        return StepResult(
            result={"new": message_id, "channel_id": int(payload["channel_id"])}, lines=(line,),
            audits=(AuditRecord("SIGNUP_OPEN", "", audited),),
        )

    async def record(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        payload = ctx.step_payload
        await signup.set_window_open_on(
            db, int((result.result or {})["new"]), [str(each) for each in payload["track_ids"]]
        )
        await advance_on_window_open_on(db)
        if payload["close_at"]:
            await signup.set_close_at_on(db, str(payload["close_at"]))

    async def not_open(_ctx: StepContext) -> bool:
        return not await signup.get_window_state()

    async def opened_here(ctx: StepContext) -> bool:
        """Whether the window was opened on this change's own post. A discarded post (or one
        found not due) opened nothing, and the jobs that follow it belong to the window it
        opens: the closed notice stays, and no close timer is armed for a window that is not
        open or was opened by another change."""
        posted = next((view for view in ctx.steps if view.name == POST_INFO), None)
        return posted is not None and "new" in (posted.result or {})

    async def describe_post(_ctx: StepContext) -> str:
        return "posting the signup information and opening the window"

    async def arm_close(ctx: StepContext) -> StepResult:
        schedule_close(str(ctx.step_payload["close_at"]))
        return StepResult(result={})

    async def describe_arm(_ctx: StepContext) -> str:
        return "arming the signup window's close"

    async def describe_delete(_ctx: StepContext) -> str:
        return "taking down the earlier signups closed notice"

    def outcome(ctx: OutcomeContext) -> str:
        views = {view.name: view for view in ctx.steps}
        if "discarded" in (views[POST_INFO].result or {}):
            return (
                "⚠️ Signups were not opened: the information message could not be posted and "
                "was discarded. Run `/signup open` again."
            )
        text = f"✅ Signups opened. Button posted in <#{ctx.payload['channel_id']}>."
        close_at = ctx.payload["close_at"]
        if close_at:
            moment = datetime.fromisoformat(close_at)
            armed = views.get(ARM_CLOSE)
            if armed is not None and "discarded" in (armed.result or {}):
                text += (
                    " The auto-close was not scheduled: arm it again with `/signup close-time "
                    "cancel` and then `/signup close-time add`; the bot also arms it when it "
                    "next starts."
                )
            else:
                text += f" Auto-close scheduled for {_stamp(moment)} ({_stamp(moment, 'R')})."
        closed = views.get(DELETE_CLOSED)
        if closed is not None and "discarded" in (closed.result or {}):
            link = (closed.result or {}).get("link") or f"in <#{ctx.payload['channel_id']}>"
            text += f"\nThe earlier signups closed notice could not be deleted ({link}): delete it by hand."
        return text

    steps: dict[str, Step] = {
        POST_INFO: Step(
            POST_INFO, StepKind.ACT, post_info, still_due=not_open, describe=describe_post,
            record=record,
        ),
        ARM_CLOSE: Step(
            ARM_CLOSE, StepKind.ACT, arm_close, still_due=opened_here, describe=describe_arm
        ),
        DELETE_CLOSED: dataclasses.replace(
            standing_posts.delete_replaced_step(DELETE_CLOSED), still_due=opened_here,
            describe=describe_delete,
        ),
    }
    return ChangeType(
        kind=OPEN,
        opening=(),
        plan=plan,
        steps=steps,
        check=check,
        key=lambda payload: OPEN,
        doing=lambda payload: "Opening signups",
        outcome=outcome,
    )


def discarded(view: StepView | None) -> bool:
    return view is not None and "discarded" in (view.result or {})


def failed_jobs(steps: tuple[StepView, ...]) -> list[str]:
    """One plain sentence for each job of a close a league admin discarded, in the order they ran."""
    failed: list[str] = []
    for view in steps:
        if not discarded(view):
            continue
        who = f"<@{view.payload.get('user_id')}>"
        if view.name == RETURN_DRIVERS:
            failed.append(NOT_RETURNED)
        elif view.name == DELETE_BUTTON:
            failed.append(BUTTON_NOT_REMOVED)
        elif view.name == SIGNUP_NOTICE:
            failed.append(f"{who} was not told signups had closed.")
        elif view.name == CLOSE_SIGNUP:
            failed.append(
                f"{who}'s signup channel could not be locked and will not delete itself: "
                "delete it by hand."
            )
        elif view.name == ARM_DELETION:
            failed.append(f"{who}'s signup channel will not delete itself: delete it by hand.")
    return failed


def drivers_returned(steps: tuple[StepView, ...]) -> int:
    """How many drivers the close returned to Not Signed Up."""
    for view in steps:
        if view.name == RETURN_DRIVERS and not discarded(view):
            return len((view.result or {}).get("returned", ()))
    return 0


def window_was_closed(steps: tuple[StepView, ...]) -> bool:
    """Whether the window was recorded closed: its post stood, was not discarded and ran."""
    return any(
        view.name == POST_CLOSED and view.done and not discarded(view) for view in steps
    ) and not any(view.name == RETURN_DRIVERS and discarded(view) for view in steps)


def return_drivers_step(
    *,
    signup_module_service: Callable[[], "SignupModuleService"],
    returned_by_close: Collection[DriverState],
    cancel_timers: Callable[[str], None],
) -> Step:
    """The `return_drivers` job of a change that closes the window: one save that returns each
    driver still filling in the wizard to Not Signed Up, ends their wizard, cancels their timers
    and plans every job that follows, so that a driver returned is never left without their
    notice. It plans the Sign Up button's deletion and the "Signups are now closed" post (whose
    record closes the window and moves the season on), then each driver's notice, lock and
    deletion's arming; its payload is the ``audit_action`` the close is audited under. Its
    arguments are those of `window_close_change`; `module.off:signup` uses it too."""

    async def return_drivers(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        states = [state.value for state in returned_by_close]
        cursor = await db.execute(
            "SELECT id, discord_user_id, current_state FROM driver_profiles "
            f"WHERE current_state IN ({','.join('?' for _ in states)}) ORDER BY id",  # noqa: S608
            states,
        )
        rows = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT signup_channel_id, signup_button_message_id FROM signup_module_config WHERE id = 1"
        )
        config = await cursor.fetchone()
        channel_id = None if config is None else config["signup_channel_id"]
        button_id = None if config is None else config["signup_button_message_id"]
        returned: list[str] = []
        told: list[PlannedStep] = []
        for row in rows:
            account = str(row["discord_user_id"])
            try:
                await write_transition(
                    db, int(row["id"]), DriverState(row["current_state"]), DriverState.NOT_SIGNED_UP
                )
            except ValueError:
                continue  # the state machine's refusal: the driver moved on since they were read
            await signup_module_service().end_wizard_on(db, account)
            returned.append(account)
            cursor = await db.execute(
                "SELECT signup_channel_id FROM signup_wizard_records WHERE discord_user_id = ?",
                (account,),
            )
            wizard = await cursor.fetchone()
            if wizard is not None and wizard["signup_channel_id"] is not None:
                shared = {"user_id": account, "channel_id": int(wizard["signup_channel_id"])}
                told += [
                    PlannedStep(SIGNUP_NOTICE, {**shared, "notice": WINDOW_CLOSED_NOTICE}),
                    PlannedStep(CLOSE_SIGNUP, shared),
                    PlannedStep(ARM_DELETION, shared),
                ]
        planned: list[PlannedStep] = []
        if button_id is not None and channel_id is not None:
            planned.append(PlannedStep(DELETE_BUTTON, {
                "channel_id": int(channel_id), "message_id": int(button_id),
                "what": "Sign Up button", "division_name": "signups",
            }))
        planned.append(PlannedStep(POST_CLOSED, {
            "channel_id": None if channel_id is None else int(channel_id),
            "audit_action": str(ctx.step_payload["audit_action"]), "advance_season": True,
        }))
        for account in returned:
            cancel_timers(account)
        return StepResult(result={"returned": returned}, then=(*planned, *told))

    async def describe_return(_ctx: StepContext) -> str:
        return "returning the drivers still signing up to Not Signed Up"

    return Step(RETURN_DRIVERS, StepKind.SAVE, return_drivers, describe=describe_return)


async def season_end_close_refusal(db_path: str) -> str | None:
    """The refusal of a member's close of the window while the season's completion, cancellation,
    abort or wind-down is in hand on the queue, naming the job nearest its turn, or None where none
    is (a wind-down counted only where it would run now, `season_ending_jobs`): that end closes
    signups itself (core's "Ending a season", one rule for every request about
    a season whose end is in hand). Asked by `/signup close` before its confirmation, and by the
    close's check, when asked (the command, and its Confirm Close button) and as it runs."""
    jobs = await season_ending_jobs(db_path)
    if not jobs:
        return None
    # An end with only its own close left has no job to name; the lowest one that has is named.
    numbered = [job for job in jobs if job is not None]
    named = f" (job #{min(numbered)})" if numbered else ""
    return (
        f"⛔ The season's end is in hand on the queue{named} and closes signups itself, so "
        "nothing was closed. If it has stopped, press Retry or Discard on its notice in the log "
        "channel."
    )


def window_close_change(
    *,
    signup_module_service: Callable[[], "SignupModuleService"],
    returned_by_close: Collection[DriverState],
    cancel_timers: Callable[[str], None],
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
) -> ChangeType:
    """The `signup.close` change type, for the builder to register.

    The payload is ``cause`` (``"command"`` for a member's, ``"timer"`` or ``"restart"`` for the
    bot's), ``window`` (the Sign Up button message of the window asked about), ``confirmed``
    (whether a manager was shown the drivers it returns and pressed Confirm Close),
    ``audit_action`` and, for the bot's, ``close_at`` (the time it was set to close). The window
    is judged again as the change comes up to run. A member's close is refused, first, while the
    season's end or wind-down is in hand (`season_end_close_refusal`). The bot's close leaves a
    season whose end or wind-down is in hand on the queue to it (`season_ending`, which counts a
    wind-down only where it would run): no longer due at its check, it is
    dropped; started already, as a close cut off by a stop is, it returns no driver it has not
    returned, and none of its jobs moves the season on (the signup specification's close of the
    window). *signup_module_service* is a getter, so that the
    service is the one the bot holds when the job runs. *returned_by_close* are the states it turns
    away, *cancel_timers* removes one driver's inactivity and deletion timers, and *now* and
    *arm_once* are the queue's clock and the scheduler service's one-off timer.
    """

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        command = payload["cause"] == "command"
        if command:
            ending = await season_end_close_refusal(ctx.db_path)
            if ending is not None:
                return Verdict.refuse(ending)
        cfg = await signup_module_service().get_config()
        if cfg is None or not cfg.signups_open:
            if command:
                return Verdict.refuse("⛔ Signups are no longer open. Nothing was closed.")
            return Verdict.not_due("the signup window is not open")
        if cfg.signup_button_message_id != payload["window"]:
            if command:
                return Verdict.refuse(
                    "⛔ Signups were reopened since this was asked. Nothing was closed. "
                    "Run `/signup close` again."
                )
            return Verdict.not_due("the signup window is not the one this close was asked about")
        if command and cfg.close_at is not None:
            return Verdict.refuse(f"⛔ {armed_close_refusal(cfg.close_at)}")
        if not command and await season_ending(ctx.db_path):
            return Verdict.not_due(
                "a season's end or wind-down is in hand on the queue, and closes the window itself"
            )
        return Verdict.go()

    async def not_left_to_the_season(ctx: StepContext) -> bool:
        """Whether the bot's close still returns its drivers: not where a season's end or
        wind-down has come to be in hand since the close started, as a close cut off by a stop
        finds at the next start. The drivers are then left to that end, which returns them
        itself, and the close plans nothing more."""
        return ctx.payload["cause"] == "command" or not await season_ending(ctx.db_path)

    async def drivers_were_returned(ctx: StepContext) -> bool:
        """Whether the close's line is still to be written: not where the bot's close left its
        drivers to a season's end, and so did nothing, which, like a bot change found no longer
        due, writes no line."""
        return not any(
            view.name == RETURN_DRIVERS and (view.result or {}).get("dropped")
            for view in ctx.steps
        )

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (
            PlannedStep(RETURN_DRIVERS, {"audit_action": payload["audit_action"]}),
            PlannedStep(CLOSE_LINE),
        )

    def _head(ctx: StepContext) -> str:
        """What the line is headed with: the member who ran the close, marked "(force)" where they
        confirmed returning the drivers still signing up, or the cause of the bot's."""
        payload = ctx.payload
        if payload["cause"] == "command":
            return f"{ctx.named} | /signup close" + (" (force)" if payload["confirmed"] else "")
        head = AUTO_CLOSE_HEAD
        if payload.get("close_at"):
            armed = datetime.fromisoformat(str(payload["close_at"]))
            if armed.tzinfo is None:
                armed = armed.replace(tzinfo=timezone.utc)
            head += f" ({discord.utils.format_dt(armed, 'F')})"
        if payload["cause"] == "restart":
            head += ", at start-up"
        return head

    async def close_line(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Write the one line that records the close: what it did, and beneath it each job a
        league admin discarded."""
        closed = window_was_closed(ctx.steps)
        head = _head(ctx)
        command = ctx.payload["cause"] == "command"
        if command:
            head += " | Success" if closed else " | Incomplete"
        elif not closed:
            head = f"System | Signups auto-close | Incomplete ({head})"
        failed = failed_jobs(ctx.steps)
        if not closed and not any(step == NOT_RETURNED for step in failed):
            failed.append(WINDOW_NOT_CLOSED)
        line = (
            f"{head}\n  drivers_returned_to_not_signed_up: {drivers_returned(ctx.steps)}"
            + "".join(f"\n  failed_step: {step}" for step in failed)
        )
        return StepResult(result={}, lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        failed = failed_jobs(ctx.steps)
        if not window_was_closed(ctx.steps):
            text = (
                "⚠️ Signups were not closed: a job of the close could not be done and was "
                "discarded. Run `/signup close` again."
            )
            return text + "".join(f"\n• {step}" for step in failed)
        text = "✅ Signups closed."
        if ctx.payload["confirmed"]:
            text += (
                f" {drivers_returned(ctx.steps)} driver(s) still signing up were returned to "
                "Not Signed Up."
            )
        if failed:
            text += (
                "\n⚠️ The window is closed, but not every step succeeded:\n"
                + "\n".join(f"• {step}" for step in failed)
            )
        return text

    async def describe_line(_ctx: StepContext) -> str:
        return "recording the close of the signup window"

    steps: dict[str, Step] = {
        **closing_steps(now=now, arm_once=arm_once),
        **window_close_steps(signup_module_service=signup_module_service, now=now),
        RETURN_DRIVERS: dataclasses.replace(
            return_drivers_step(
                signup_module_service=signup_module_service, returned_by_close=returned_by_close,
                cancel_timers=cancel_timers,
            ),
            still_due=not_left_to_the_season,
        ),
        CLOSE_LINE: Step(
            CLOSE_LINE, StepKind.SAVE, close_line, still_due=drivers_were_returned,
            describe=describe_line,
        ),
    }
    return ChangeType(
        kind=CLOSE,
        opening=(),
        plan=plan,
        steps=steps,
        check=check,
        key=lambda payload: CLOSE,
        doing=lambda payload: "Closing signups",
        outcome=outcome,
    )
