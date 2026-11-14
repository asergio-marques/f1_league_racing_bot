"""Signup's own closing jobs, and the `signup.closing` change that runs them on a driver's lane.

A driver's signup channel is closed in three jobs, each built by a factory the builder registers
with what it needs handed it (and, where a manager's typed reason began the closing, a fourth
before them, `delete_reason`, which takes that message down): `signup_notice` tells the channel why it closes, mentioning the
driver and no one else; `close_signup` takes the driver's typing from it; `arm_deletion` arms the
channel's deletion a day on (the `signup_channel_delete` timer, keyed by the driver's account).
The notice goes first, so a driver is never locked out of a channel that says nothing: where
Discord refuses it, the job stops its lane with nothing locked and nothing armed, and a Retry finds
the channel as it was.

The jobs are usable on either lane (docs/wip-specs/core_specification.md, "How a change is
carried out"). A league change that closes signups (a command or a timer) runs them itself; the
`signup.closing` change is what a league change that ended a driver's signup (Withdraw, Approve, a
Reject reason) asks back of the driver's own lane, so that a refused notice stops that driver's
lane alone. It needs only its channel to stand, not the signup it closes: that has ended.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Mapping, Sequence
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.drivers.driver_service import drivers_in_states
from leaguebot.core.models.change import (
    ChangeOrigin,
    FollowOn,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.posting import standing_posts
from leaguebot.core.services.queue.audit_service import record_system_change_on
from leaguebot.core.services.seasons.season_end_changes import season_ending_on
from leaguebot.core.services.seasons.season_lifecycle_service import (
    NO_CHANNEL_LEFT,
    advance_on_window_close_on,
)
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    DiscardedContext,
    OutcomeContext,
    RefusedContext,
    Step,
    StepContext,
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.signup.services.signup_posting import post, post_standing_message

if TYPE_CHECKING:
    from leaguebot.core.services.queue.change_queue import ChangeQueue
    from leaguebot.core.utils.league_bot import LeagueBot
    from leaguebot.signup.services.signup_module_service import SignupModuleService

log = logging.getLogger(__name__)

#: The job that deletes the reason a manager typed after Reject, ahead of the closing notice that
#: gives it.
DELETE_REASON = "delete_reason"
SIGNUP_NOTICE = "signup_notice"
CLOSE_SIGNUP = "close_signup"
ARM_DELETION = "arm_deletion"
DELETE_BUTTON = "delete_button"
POST_CLOSED = "post_closed"
#: The job, after the save that made them no longer due, that takes timers away: a driver's
#: inactivity, deletion and correction timers (``accounts``) and the window's close timer
#: (``close_timer``). A timer is disarmed by a job after the save, never inside it, so that a
#: save that fails leaves the timers it would have taken away.
DISARM_TIMERS = "disarm_timers"
#: The job that closes the window at a season's end, which core plans through its hook for the
#: close of what a module holds open.
CLOSE_WINDOW = "close_window"
#: What the signup channel of a driver is told where the signup window closes under it.
WINDOW_CLOSED_NOTICE = "🔒 Signups have closed. This channel will be automatically deleted in 24 hours."
#: What a close, a season's end or a wind-down says of a window it could not close. A close time
#: that was armed stays armed, and `/signup close` refuses while one is, so it is named first; it
#: refuses too while a season's end or wind-down is in hand on the queue, so that is named too.
WINDOW_NOT_CLOSED = (
    "The signup window could not be closed. Close it with `/signup close`. "
    "Where a close time is set, run `/signup close-time cancel` first, and where a season's end "
    "or wind-down is still on the queue, let it finish first, as `/signup close` is refused "
    "until then."
)
#: What a discarded deletion of the Sign Up button leaves for a league admin to do.
BUTTON_KEPT = "The Sign Up button could not be removed from the signup channel."
#: What the signup channel is left saying once the window has closed.
CLOSED_NOTICE = "🔒 Signups are now closed."
#: How a close nobody ran is audited and its line headed, by the cause the change gives it.
CLOSE_CAUSES: dict[str, tuple[str, str]] = {
    "season end": ("SIGNUP_SEASON_END_CLOSE", "🔒 Signups closed as the season ended"),
    "divisions done": (
        "SIGNUP_DIVISIONS_DONE_CLOSE", "🔒 Signups closed as every division is done"
    ),
    # The abort of a season being set up is a cause of its own for the words of the job, and is
    # recorded as a season's end is.
    "abort": ("SIGNUP_SEASON_END_CLOSE", "🔒 Signups closed as the season ended"),
}
#: The kind of the change that closes one driver's signup channel on the driver's own lane.
KIND = "signup.closing"
#: The kind of the change that takes down a manager's typed reason the signup it was typed for
#: has moved on from, on the driver's own lane.
REASON_KIND = "signup.reason.delete"
#: The timer that deletes a held signup channel a day on.
DELETE_TIMER = "signup_channel_delete"
#: The timer that ends a signup left unanswered for a day: a closing or a switch-off takes it away.
INACTIVITY_TIMER = "signup_inactivity"
#: How long a closed signup channel is kept before it is deleted.
HOLD = timedelta(hours=24)
#: The kind of the change that deletes a driver's held signup channel.
DELETE_KIND = "signup.channel.delete"
#: The kind of the change that ends a driver's signup, and the reason a day without an answer gives.
END = "signup.end"
EXPIRED = "expired"
#: The reason a signup ends when its signup channel is gone where its review panel was due.
CHANNEL_GONE = "channel_gone"
#: What a signup channel is told when its driver's 24 hours run out.
EXPIRED_NOTICE = (
    "⏰ Your signup session has expired due to inactivity. "
    "This channel will be automatically deleted in 24 hours."
)

#: The states of a driver whose signup is still in progress or in review: a season's end that
#: returns such a driver to Not Signed Up tells their signup channel and closes it.
_SIGNUP_IN_PROGRESS: frozenset[str] = frozenset({
    "PENDING_SIGNUP_COMPLETION",
    "PENDING_ADMIN_APPROVAL",
    "AWAITING_CORRECTION_PARAMETER",
    "PENDING_DRIVER_CORRECTION",
})

__all__ = [
    "ARM_DELETION", "BUTTON_KEPT", "CLOSED_NOTICE", "CLOSE_CAUSES", "CLOSE_SIGNUP", "CLOSE_WINDOW",
    "DELETE_BUTTON", "DELETE_REASON",
    "DELETE_TIMER", "DISARM_TIMERS",
    "CHANNEL_GONE", "DELETE_KIND", "END", "EXPIRED", "EXPIRED_NOTICE", "HOLD", "INACTIVITY_TIMER", "KIND",
    "POST_CLOSED", "REASON_KIND", "SIGNUP_NOTICE", "WINDOW_CLOSED_NOTICE", "WINDOW_NOT_CLOSED", "ask_channel_delete", "ask_expiry", "recover_correction_timeouts", "recover_gone_channels", "recover_wizards",
    "closing_change", "closing_steps", "driver_returned_jobs", "drivers_returned_on",
    "reason_delete_change", "season_end_closes", "season_end_driver_steps", "take_reason_down", "window_close_jobs",
    "window_close_steps", "disarm_timers_step", "disarming",
]


def disarming(
    accounts: Sequence[str] = (), *, close_timer: bool = False
) -> tuple[PlannedStep, ...]:
    """The `disarm_timers` job for *accounts*' timers and, where *close_timer*, the window's close
    timer, to plan after the save that made them no longer due; none where there is nothing to
    take away."""
    if not accounts and not close_timer:
        return ()
    return (PlannedStep(
        DISARM_TIMERS, {"accounts": [str(account) for account in accounts], "close_timer": close_timer},
    ),)


def disarm_timers_step(
    *,
    cancel_timers: Callable[[str], None],
    cancel_close_timer: Callable[[], None] | None = None,
    still_due: Callable[[StepContext], Awaitable[bool]] | None = None,
) -> Step:
    """The `disarm_timers` job (`disarming`), for a change type to register: it takes away each
    account's timers (*cancel_timers*) and, where the payload asks it, the window's close timer
    (*cancel_close_timer*, which a change that never asks it need not be handed). *still_due*
    judges the save it follows, as for any job that depends on one. Nothing is read or written in
    the database: a timer that is already gone is nothing to take away."""

    async def disarm(ctx: StepContext) -> StepResult:
        for account in ctx.step_payload["accounts"]:
            cancel_timers(str(account))
        if ctx.step_payload["close_timer"]:
            if cancel_close_timer is None:
                raise RuntimeError("the close timer was asked taken away of a change that has none")
            cancel_close_timer()
        return StepResult(result={})

    async def describe(_ctx: StepContext) -> str:
        return "taking the signup's timers away"

    return Step(DISARM_TIMERS, StepKind.ACT, disarm, still_due=still_due, describe=describe)


async def ask_expiry(queue: "ChangeQueue", db_path: str, user_id: str) -> int | None:
    """Ask the queue, as the bot, for the expiry of *user_id*'s signup (`signup.end` for the
    reason `EXPIRED`): the 24-hour timer's ask, and the restart's for a deadline that passed while
    the bot was down. Gives the change's id, or None where the driver has no wizard or the
    expiry is not due (the change's check judges that, and drops it)."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_wizard_records WHERE discord_user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return await queue.ask(
        END,
        {"user_id": user_id, "reason": EXPIRED, "channel_id": row["signup_channel_id"]},
        what=f"ending the signup of <@{user_id}>, left unanswered for a day",
        origin=ChangeOrigin.BOT,
    )


async def ask_channel_delete(queue: "ChangeQueue", db_path: str, user_id: str) -> int | None:
    """Ask the queue, as the bot, to delete the signup channel *user_id*'s wizard holds
    (`signup.channel.delete`): the hold's 24-hour timer's ask, on the driver's own lane, since a
    timer's deletion is the conversation's own. Gives the change's id, or None where the driver
    holds no channel."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_wizard_records WHERE discord_user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    if row is None or row["signup_channel_id"] is None:
        return None
    return await queue.ask(
        DELETE_KIND,
        {"user_id": user_id, "channel_id": int(row["signup_channel_id"])},
        what=f"deleting the signup channel of <@{user_id}>",
        origin=ChangeOrigin.BOT,
    )


async def wizard_channel(db_path: str, user_id: str) -> int | None:
    """The signup channel *user_id*'s wizard holds, or None where they have no wizard or it holds
    none."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_wizard_records WHERE discord_user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    return None if row is None or row["signup_channel_id"] is None else int(row["signup_channel_id"])


async def hold_until_on(db: aiosqlite.Connection, user_id: str, until: datetime) -> None:
    """Record on the connection handed, committing nothing, that the hold on *user_id*'s signup
    channel runs out at *until* (``held_until`` of their wizard's row). The channel's deletion is
    due from then and not before, whether its timer survived a restart or not; the row's next
    save as a wizard clears it."""
    await db.execute(
        "UPDATE signup_wizard_records SET held_until = ? WHERE discord_user_id = ?",
        (until.isoformat(), user_id),
    )


def season_end_driver_steps(
    *,
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
    cancel_timers: Callable[[str], None],
    line_prefix: str | None = None,
) -> dict[str, Step]:
    """The two jobs of a driver's signup channel at a season's end, by name: `signup_notice` and
    `close_signup`, which are `closing_steps`' own (it holds the deletion's arming, as above).
    Where the change has no closing line of its own, *line_prefix* has the notice job write one.
    Not subscribed itself: signup's `window_changes.season_end_steps`, which is, includes it."""
    own = closing_steps(
        now=now, arm_once=arm_once, cancel_timers=cancel_timers, line_prefix=line_prefix
    )
    return {name: own[name] for name in (SIGNUP_NOTICE, CLOSE_SIGNUP)}


def closing_steps(
    *,
    now: Callable[[], datetime],
    arm_once: Callable[[str, str, datetime], None],
    cancel_timers: Callable[[str], None] | None = None,
    line_prefix: str | None = None,
) -> dict[str, Step]:
    """The jobs of one driver's signup channel's closing, named as above.

    Each reads ``user_id`` and ``channel_id`` from its payload, and the notice its ``notice``; a
    payload without ``channel_id`` (a season's end plans none, as it knows no channel) has the
    channel the driver's wizard still holds read when the job runs. A channel gone from the
    server is nothing to tell, lock or delete: `signup_notice` says so, and the two after it find
    nothing to do. *now* and *arm_once* are the queue's clock and the scheduler service's one-off
    timer.

    A season's end closes a driver in two jobs, not three: its `close_signup` carries ``hold``,
    and after the lock takes the driver's timers away (*cancel_timers*) and arms the channel's
    deletion a day on from the queue's clock, so that the closing a season's end plans among
    the drivers' jobs stays a notice and a lock. Where *line_prefix* is given ("System | Every
    division is done"), the notice job writes a line naming a driver it found no channel for.
    """

    async def _channel(ctx: StepContext) -> discord.TextChannel | None:
        channel_id = ctx.step_payload.get("channel_id")
        if channel_id is None:
            channel_id = await wizard_channel(ctx.db_path, str(ctx.step_payload["user_id"]))
        guild = await league_guild(ctx.bot)
        found = None if guild is None or channel_id is None else guild.get_channel(int(channel_id))
        return found if isinstance(found, discord.TextChannel) else None

    def _passed_over(ctx: StepContext) -> bool:
        return any(
            view.name == SIGNUP_NOTICE and view.done
            and str(view.payload.get("user_id")) == str(ctx.step_payload["user_id"])
            and (view.result or {}).get("no_channel")
            for view in ctx.steps
        )

    async def signup_notice(ctx: StepContext) -> StepResult:
        channel = await _channel(ctx)
        if channel is None:
            user_id = ctx.step_payload["user_id"]
            lines = (
                (f"{line_prefix} | {NO_CHANNEL_LEFT.format(who=f'<@{user_id}>')}",)
                if line_prefix else ()
            )
            return StepResult(result={"no_channel": True}, lines=lines)
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(id=int(ctx.step_payload["user_id"]))]
        )
        await post(
            channel, str(ctx.step_payload["notice"]), mentions=mentions,
            refused=f"the closing notice could not be posted in channel {channel.id}",
        )
        return StepResult(result={})

    def _hold(ctx: StepContext, channel: discord.TextChannel | None) -> dict[str, Any]:
        """A season's end's closing: the driver's timers away, the channel's deletion armed. Gives
        the result the step's record keeps the hold's end from."""
        user_id = str(ctx.step_payload["user_id"])
        if cancel_timers is not None:
            cancel_timers(user_id)
        if channel is None:
            return {}
        until = now() + HOLD
        arm_once(DELETE_TIMER, user_id, until)
        return {"held_until": until.isoformat()}

    async def close_signup(ctx: StepContext) -> StepResult:
        held = bool(ctx.step_payload.get("hold"))
        if _passed_over(ctx):
            return StepResult(result={"no_channel": True, **(_hold(ctx, None) if held else {})})
        channel = await _channel(ctx)
        member = None if channel is None else channel.guild.get_member(int(ctx.step_payload["user_id"]))
        if channel is not None and member is not None:
            try:
                await channel.set_permissions(
                    member, view_channel=True, send_messages=False,
                    reason="Signup wizard channel hold",
                )
            except discord.HTTPException as error:
                raise StepFailedOnDiscord(
                    f"the signup channel {channel.id} could not be locked"
                ) from error
        return StepResult(result=_hold(ctx, channel) if held else {})

    async def arm_deletion(ctx: StepContext) -> StepResult:
        if _passed_over(ctx):
            return StepResult(result={"no_channel": True})
        until = now() + HOLD
        arm_once(DELETE_TIMER, str(ctx.step_payload["user_id"]), until)
        return StepResult(result={"held_until": until.isoformat()})

    async def record_hold(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        """In the save that marks the arming done, the hold's end on the wizard's row, so that the
        deletion is due from then whatever becomes of the timer."""
        until = (result.result or {}).get("held_until")
        if until is not None:
            await hold_until_on(db, str(ctx.step_payload["user_id"]), datetime.fromisoformat(until))

    async def describe_notice(ctx: StepContext) -> str:
        return f"telling <@{ctx.step_payload['user_id']}> that their signup has closed"

    async def describe_close(ctx: StepContext) -> str:
        return f"closing the signup of <@{ctx.step_payload['user_id']}>"

    async def describe_arm(ctx: StepContext) -> str:
        return f"arming the deletion of <@{ctx.step_payload['user_id']}>'s signup channel"

    async def describe_reason(ctx: StepContext) -> str:
        return f"deleting the reason typed in <#{ctx.step_payload['channel_id']}>"

    return {
        DELETE_REASON: dataclasses.replace(
            standing_posts.delete_replaced_step(DELETE_REASON), describe=describe_reason
        ),
        SIGNUP_NOTICE: Step(SIGNUP_NOTICE, StepKind.ACT, signup_notice, describe=describe_notice),
        CLOSE_SIGNUP: Step(
            CLOSE_SIGNUP, StepKind.ACT, close_signup, describe=describe_close, record=record_hold
        ),
        ARM_DELETION: Step(
            ARM_DELETION, StepKind.ACT, arm_deletion, describe=describe_arm, record=record_hold
        ),
    }


def driver_returned_jobs(driver: Mapping[str, Any], *, notice: str) -> tuple[PlannedStep, ...]:
    """The jobs signup plans for one driver a season's end returns to Not Signed Up: a signup still
    in progress or in review is told *notice* (`signup_notice`) and then has its channel closed
    (`close_signup`); any other driver needs nothing of signup. *driver* holds their ``user_id``
    and ``state`` (the one they were in). Subscribed to core's `driver_returned_jobs`."""
    if driver["state"] not in _SIGNUP_IN_PROGRESS:
        return ()
    user_id = str(driver["user_id"])
    return (
        PlannedStep(SIGNUP_NOTICE, {"user_id": user_id, "notice": notice}),
        PlannedStep(CLOSE_SIGNUP, {"user_id": user_id, "hold": True}),
    )


async def drivers_returned_on(
    signup_module_service: Callable[[], "SignupModuleService"],
    db: aiosqlite.Connection,
    drivers: Sequence[Mapping[str, Any]],
) -> None:
    """Mark over the signup wizard of each of *drivers* a season's end returns to Not Signed Up
    whose signup was in progress or in review, on the save's connection, committing nothing, so
    that a restart finds no wizard still engaged for a signup that has ended and tells nobody it
    expired. *drivers* are mappings of ``user_id`` and ``state``. Subscribed to core's
    `drivers_returned`; *signup_module_service* is a getter, so that it is the service the bot
    holds when the save runs."""
    for driver in drivers:
        if driver["state"] in _SIGNUP_IN_PROGRESS:
            await signup_module_service().end_wizard_on(db, str(driver["user_id"]))


def season_end_closes(*, cause: str, season_number: int | None = None) -> tuple[PlannedStep, ...]:
    """The job signup plans to close its window as a season ends (`close_window`, which does
    nothing where signup is off or its window is not open): *cause* is the season's end, the abort
    of the season being set up or the wind-down's close as every division is done, and
    *season_number* the season's, where it has one, for the words of the job. Subscribed to core's
    `season_end_closes`."""
    return (PlannedStep(CLOSE_WINDOW, {"cause": cause, "season_number": season_number}),)


async def recover_wizards(bot: "LeagueBot", *, queue: "ChangeQueue") -> None:
    """Signup's catch-up at start: ask, as the bot, for what the wizards missed while it was down,
    before the queue starts.

    A signup whose channel was deleted meanwhile is ended first (`recover_gone_channels`); a
    driver parked awaiting a correction parameter is released (`recover_correction_timeouts`); and
    each signup left unanswered for 24 hours, judged against *queue*'s clock, so that a caught-up
    expiry and one run on time take one path, has its expiry asked (`ask_expiry`). The change's
    check drops what the driver has since moved on from. Each hold on a signup channel that ran out
    meanwhile (``held_until``) has its deletion asked (`ask_channel_delete`), which the change's
    check drops where the channel is no longer the driver's. It arms nothing: a deadline still ahead
    is a timer the scheduler's own store keeps through the restart, and an expiry asked of a wizard
    with no activity recorded is asked at once. SC-005.
    """
    gone = await recover_gone_channels(bot, queue=queue)
    await recover_correction_timeouts(bot.db_path, queue, skip=gone)
    now = queue.now()
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id, last_activity_at FROM signup_wizard_records "
            "WHERE wizard_state != 'UNENGAGED'"
        )
        wizards = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT discord_user_id, held_until FROM signup_wizard_records "
            "WHERE held_until IS NOT NULL"
        )
        holds = await cursor.fetchall()
    for hold in holds:
        # A hold that ran out while the bot was down: its timer may not have survived.
        if datetime.fromisoformat(hold["held_until"]) <= now:
            await ask_channel_delete(queue, bot.db_path, str(hold["discord_user_id"]))
    for wizard in wizards:
        user_id = str(wizard["discord_user_id"])
        if user_id in gone:
            continue
        if wizard["last_activity_at"]:
            last = datetime.fromisoformat(wizard["last_activity_at"])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last + HOLD > now:
                continue
        await ask_expiry(queue, bot.db_path, user_id)


async def recover_gone_channels(bot: "LeagueBot", *, queue: "ChangeQueue") -> set[str]:
    """The first of the wizards' catch-up: end the signup of every driver whose signup channel was
    deleted while the bot was down (#439, slice 8).

    Every driver whose signup is under way (`IN_PROGRESS_STATES`) and whose wizard names a
    channel is looked at. A channel the cache lacks is confirmed with `fetch_channel`: not
    found, or found as no text channel, is gone, and the restart asks the queue, as the bot
    and before it starts, for the ending of that signup (`signup.end`, reason `CHANNEL_GONE`,
    found at a restart), whose check drops it where the driver has since moved on. Any other
    failure of the lookup is logged and the channel treated as standing: a sweep cannot stop
    the queue, and the deletion listener or the next restart tries again. With no league
    server in the cache nothing is ended, and a warning says so.

    Gives the accounts it asked an ending of, which the rest of the recovery skips.
    """
    from leaguebot.core.utils.log_lines import name_of_member
    from leaguebot.signup.services.signup_views import IN_PROGRESS_STATES
    from leaguebot.signup.services.wizard_changes import RESTART, ask_channel_gone

    asked: set[str] = set()
    rows: list[dict[str, Any]] = []
    async with get_connection(bot.db_path) as db:
        for driver in await drivers_in_states(db, [state.value for state in IN_PROGRESS_STATES]):
            cursor = await db.execute(
                "SELECT signup_channel_id FROM signup_wizard_records "
                "WHERE discord_user_id = ? AND signup_channel_id IS NOT NULL",
                (driver["discord_user_id"],),
            )
            wizard = await cursor.fetchone()
            if wizard is not None:
                rows.append({
                    "discord_user_id": driver["discord_user_id"],
                    "signup_channel_id": wizard["signup_channel_id"],
                })
    if not rows:
        return asked
    guild = await league_guild(bot)
    if guild is None:
        log.warning(
            "recover_gone_channels: the league's server is not in the cache, so no signup "
            "channel was looked for"
        )
        return asked
    for row in rows:
        user_id = row["discord_user_id"]
        channel_id = int(row["signup_channel_id"])
        try:
            if guild.get_channel(channel_id) is not None:
                continue
            try:
                fetched = await guild.fetch_channel(channel_id)
            except discord.NotFound:
                fetched = None
            if isinstance(fetched, discord.TextChannel):
                continue
            await ask_channel_gone(
                queue,
                user_id=user_id,
                channel_id=channel_id,
                named=await name_of_member(bot, int(user_id)),
                found=RESTART,
            )
            asked.add(user_id)
        except discord.HTTPException:
            log.warning(
                "recover_gone_channels: signup channel %s of %s could not be looked for; "
                "treated as standing", channel_id, user_id, exc_info=True,
            )
        except Exception:  # noqa: BLE001 - one driver's refused ask must not strand the rest
            log.exception("recover_gone_channels: could not ask the ending of %s's signup", user_id)
    return asked


async def recover_correction_timeouts(
    db_path: str, queue: "ChangeQueue", *, skip: frozenset[str] | set[str] = frozenset()
) -> None:
    """Release every driver awaiting a correction parameter, save those in *skip*, whose signup
    the restart has just ended for a channel gone.

    The five-minute selection window is a one-off timer, so a restart discards it and nothing
    re-creates it, which left the driver parked for good, shown in no listing and refused by
    their own Sign Up button (issue #129). The restart asks the queue, as the bot and before it
    starts, for the lapse of each such driver (`signup.correction.lapse`, with *restart* set,
    which words the notice).

    A restart releases them **unconditionally** rather than re-arming whatever time was left. The
    state exists only while a manager is mid-decision, and a manager whose bot has restarted is no
    longer mid-decision (decided 2026-09-15). That is also why nothing here reads a stored
    deadline, and why a driver stranded by the old behaviour, whose record predates any of this,
    is released on the same path.

    These drivers cannot come from `recover_wizards`' own read of the wizards: the wizard record is
    UNENGAGED for the whole window. The driver profile is the only record that the window is
    open, so it is what is read here.
    """
    from leaguebot.signup.services.review_changes import ask_lapse

    async with get_connection(db_path) as db:
        rows = await drivers_in_states(db, [DriverState.AWAITING_CORRECTION_PARAMETER.value])

    for row in rows:
        if row["discord_user_id"] in skip:
            continue
        try:
            await ask_lapse(queue, row["discord_user_id"], restart=True)
        except Exception:  # noqa: BLE001 - one driver's refused ask must not strand the rest
            log.exception(
                "recover_correction_timeouts: could not ask the lapse of %s",
                row["discord_user_id"],
            )


def window_close_jobs(
    cause: str, returned: int, *, channel_id: int | None, button_message_id: int | None
) -> tuple[PlannedStep, ...]:
    """The jobs that close the signup window in its channel once a close nobody ran has returned
    its drivers: the Sign Up button's deletion where one stands, then the "Signups are now
    closed" post, whose record closes the window. *cause* is a key of `CLOSE_CAUSES`, *returned*
    how many drivers the close returned, for the line the post's record writes."""
    audit_action, head = CLOSE_CAUSES[cause]
    planned: list[PlannedStep] = []
    if button_message_id is not None and channel_id is not None:
        planned.append(PlannedStep(DELETE_BUTTON, {
            "channel_id": channel_id, "message_id": button_message_id,
            "what": "Sign Up button", "division_name": "signups",
        }))
    planned.append(PlannedStep(POST_CLOSED, {
        "channel_id": channel_id, "audit_action": audit_action, "head": head, "returned": returned,
    }))
    return tuple(planned)


def window_close_steps(
    *, signup_module_service: Callable[[], "SignupModuleService"], now: Callable[[], datetime]
) -> dict[str, Step]:
    """The two jobs that close the window in its channel, for a change that ends signups.

    `delete_button` takes the Sign Up button down (a button already gone completes it). `post_closed`
    posts "Signups are now closed" in the signup channel, and in the save that marks it done its
    record closes the window with the post's id (so the next opening deletes the notice), audits
    the close and has the queue write the "Signups closed" line, where the payload gives a
    ``head`` for it (a close a member ran writes its own line in its last job), and moves the
    season on where it says ``advance_season`` (a season's end moves its own, and a season whose
    end, or a wind-down that would run, is in hand on the queue is left to it, `season_ending_on`):
    no window is ever recorded closed without its notice up, and a refused post stops the queue
    with signups still open.
    Where its try posted and the record then failed, the message is kept for the next try to remove
    before it posts again. A window already closed by another hand is not closed twice.
    *signup_module_service* is a getter, so that the service is the one the bot holds when the job runs.
    """

    async def post_closed(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        guild = await league_guild(ctx.bot)
        found = None if guild is None or ctx.step_payload.get("channel_id") is None else (
            guild.get_channel(int(ctx.step_payload["channel_id"]))
        )
        if not isinstance(found, discord.TextChannel):
            raise StepFailedOnDiscord("the signup channel could not be found to post in")
        message_id = await post_standing_message(
            found, CLOSED_NOTICE, mentions=discord.AllowedMentions.none(),
            refused=f"the closed notice could not be posted in channel {found.id}",
        )
        payload = ctx.step_payload
        lines: tuple[str, ...] = ()
        if "head" in payload:
            lines = (
                f"{payload['head']}\n  drivers_returned_to_not_signed_up: {payload['returned']}",
            )
        return StepResult(result={"new": message_id, "channel_id": found.id}, lines=lines)

    async def record(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        await signup_module_service().set_window_closed_on(
            db, closed_msg_id=int((result.result or {})["new"])
        )
        await record_system_change_on(
            db, change_type=str(ctx.step_payload["audit_action"]), old_value="open",
            new_value="closed", now=now(),
        )
        # A season whose end or wind-down is in hand on the queue is left to it, which moves it
        # its own way.
        if ctx.step_payload.get("advance_season") and not await season_ending_on(db):
            await advance_on_window_close_on(db)

    async def window_stands(_ctx: StepContext) -> bool:
        return await signup_module_service().get_window_state()

    async def describe_closed(_ctx: StepContext) -> str:
        return "closing the signup window and telling its channel"

    async def describe_button(_ctx: StepContext) -> str:
        return "taking the Sign Up button down"

    button = dataclasses.replace(
        standing_posts.delete_replaced_step(DELETE_BUTTON), describe=describe_button
    )
    return {
        DELETE_BUTTON: button,
        POST_CLOSED: Step(
            POST_CLOSED, StepKind.ACT, post_closed, still_due=window_stands,
            describe=describe_closed, record=record,
        ),
    }


def closing_change(
    *, now: Callable[[], datetime], arm_once: Callable[[str, str, datetime], None]
) -> ChangeType:
    """The `signup.closing` change type, for the builder to register, on the driver's lane
    (``channel:<signup channel>``): the notice, the lock and the deletion's arming of a signup
    that has ended. The payload is ``user_id``, ``channel_id`` and ``notice``, and
    ``reason_message_id`` where a manager's typed reason (after Reject) is to come down first: the
    notice gives the reason, so the message is deleted ahead of it."""

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        shared = {"user_id": str(payload["user_id"]), "channel_id": int(payload["channel_id"])}
        planned: list[PlannedStep] = []
        if payload.get("reason_message_id") is not None:
            planned.append(PlannedStep(DELETE_REASON, {
                "channel_id": shared["channel_id"],
                "message_id": int(payload["reason_message_id"]), "what": "reason message",
            }))
        planned += [
            PlannedStep(SIGNUP_NOTICE, {**shared, "notice": str(payload["notice"])}),
            PlannedStep(CLOSE_SIGNUP, shared),
            PlannedStep(ARM_DELETION, shared),
        ]
        return tuple(planned)

    async def standing(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=KIND,
        opening=(),
        plan=plan,
        steps=closing_steps(now=now, arm_once=arm_once),
        check=standing,
        key=lambda payload: f"{KIND}:{payload['user_id']}",
        doing=lambda payload: f"Closing the signup of <@{payload['user_id']}>",
        outcome=outcome,
        lane=lambda payload: f"channel:{int(payload['channel_id'])}",
        writes=("signup_wizard_records",),
    )


def take_reason_down(context: RefusedContext | DiscardedContext) -> tuple[FollowOn, ...]:
    """The change's `on_refused` and `on_discarded` for a rejection or a request for changes: the
    manager's reason, typed in the driver's channel (``reason_channel_id`` and
    ``reason_message_id`` of the payload), is taken down by `signup.reason.delete` when the press
    is refused as it comes up to run, or discarded at its check before it started, so that a
    reason the change will never take down does not stay where the driver can read it. A payload
    that carries no reason (an expiry, a withdrawal) asks nothing."""
    channel_id = context.payload.get("reason_channel_id")
    message_id = context.payload.get("reason_message_id")
    if channel_id is None or message_id is None:
        return ()
    return (FollowOn(
        REASON_KIND,
        {"channel_id": int(channel_id), "message_id": int(message_id)},
        "taking down a manager's reason",
    ),)


def reason_delete_change() -> ChangeType:
    """The `signup.reason.delete` change type, for the builder to register, on the driver's own lane
    (``channel:<signup channel>``): the manager's reason, typed after Reject or Request Changes,
    for a signup that has moved on since the press. The press was refused and nothing else is
    done, so the one job `delete_reason` takes the message down, as the closing and the correction
    choices do for a reason that was taken. The payload is ``channel_id`` and ``message_id``; a
    message already gone is nothing to do, and one Discord refuses to delete stops the driver's
    lane."""

    def plan(payload: dict[str, Any]) -> tuple[PlannedStep, ...]:
        return (PlannedStep(DELETE_REASON, {
            "channel_id": int(payload["channel_id"]),
            "message_id": int(payload["message_id"]), "what": "reason message",
        }),)

    async def standing(_ctx: CheckContext) -> Verdict:
        return Verdict.go()

    async def describe_reason(ctx: StepContext) -> str:
        return f"deleting the reason typed in <#{ctx.step_payload['channel_id']}>"

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=REASON_KIND,
        opening=(),
        plan=plan,
        steps={
            DELETE_REASON: dataclasses.replace(
                standing_posts.delete_replaced_step(DELETE_REASON), describe=describe_reason
            ),
        },
        check=standing,
        key=lambda payload: f"{REASON_KIND}:{payload['message_id']}",
        doing=lambda payload: "Taking down a manager's reason",
        outcome=outcome,
        lane=lambda payload: f"channel:{int(payload['channel_id'])}",
        writes=(),
    )
