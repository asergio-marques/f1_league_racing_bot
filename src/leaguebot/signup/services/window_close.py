"""The close of the signup window nobody ran, and the states a close returns to Not Signed Up.

The queue's own close (``signup.close``) is signup's change. This is who asks for it where no
member does: the close timer and the restart sweep. A season's end and every division done close
the window with signup's own `close_window` job (``window_changes.season_end_close_step``).

**The close timer is a one-off timer of signup's own kind** (``signup.close_timer``, one key: there
is one window at a time), armed through `SchedulerService.arm_once` with no lateness limit, so a
close time that passes while the bot is down fires on start. Its handler, `close_when_due`, judges
the stored close time against the queue's clock and closes only where it has come: a timer left
standing after its close time was cancelled or replaced closes nothing, whichever order the disarming
and the firing happened in. The restart catch-up, `recover_missed_close`, closes a window whose close
time passed while the bot was down and arms the timer of one still ahead, so that an arming a Discard
dropped is made good on start.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import ChangeOrigin, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.queue.change_types import ChangeType, CheckContext, Step, StepContext
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.modules.module_service import ModuleService
from leaguebot.core.utils.input_validator import parse_datetime
from leaguebot.signup.services.signup_module_service import SignupModuleService, signup_enabled_on

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

#: The kind of the one-off timer that closes signups at their set time, and its one key.
CLOSE_TIMER = "signup.close_timer"
CLOSE_TIMER_KEY = "window"

#: The change that arms the close timer at start, and its one step.
ARM_KIND = "signup.close_timer.arm"
ARM_CLOSE_TIMER = "arm_close_timer"

#: The states a close returns to Not Signed Up: a driver still filling in the wizard. One
#: awaiting approval, awaiting a correction parameter or correcting keeps their state
#: (FR-002/FR-003). ``signup_close`` reads this set to tell a manager which drivers the close
#: will drop and which keep their place. When it kept a list of its own, it warned that every
#: driver mid-signup would be dropped (issue #128).
RETURNED_BY_CLOSE: frozenset[DriverState] = frozenset({DriverState.PENDING_SIGNUP_COMPLETION})


#: Why a close nobody ran happened, and the audit action it is recorded under.
_UNATTENDED_CAUSES: dict[str, str] = {
    "timer": "SIGNUP_AUTO_CLOSE",
    "restart": "SIGNUP_AUTO_CLOSE",
}


async def close_signups_unattended(
    *,
    change_queue: ChangeQueue,
    module_service: ModuleService,
    signup_module_service: SignupModuleService,
    cause: str,
) -> None:
    """Close the signup window where no member ran the close, by asking the change queue's own
    close (``signup.close``, named here as the builder registers it, signup's) as the bot.

    *cause* is ``"timer"`` (the close time came) or ``"restart"`` (the bot came back after it);
    it picks the audit action the change's close is written under (`_UNATTENDED_CAUSES`). Nothing
    is given back: the window stays open until the queue reaches it, and the change's jobs tell
    and lock each driver it returns, close the window in its channel and write the one line,
    which names no member, since none closed the window. A season's end and every division done
    close the window with signup's `close_window` job, which core plans at the moment
    (`season_end_closes`), not through here.

    The change queue and the services it uses are handed in by its caller, the entry point's
    close timer and restart sweep, each of which holds the bot.

    Nothing is done where a window was not actually open: a module that is disabled produces
    nothing (the core specification, Modules), and a window not open needs no close, so both
    ask for nothing.
    """
    if not await module_service.is_signup_enabled():
        return
    cfg = await signup_module_service.get_config()
    if cfg is None or not cfg.signups_open:
        return
    await _ask_close(change_queue, cfg.signup_button_message_id, cfg.close_at, cause)


async def _ask_close(
    change_queue: ChangeQueue, window: int | None, close_at: str | None, cause: str
) -> None:
    """Ask the queue's ``signup.close`` as the bot, for the window the Sign Up button *window*
    stands for, under the audit action of *cause*."""
    await change_queue.ask(
        "signup.close",
        {
            "cause": cause,
            "window": window,
            "confirmed": False,
            "audit_action": _UNATTENDED_CAUSES[cause],
            "close_at": close_at,
        },
        what="Closing signups",
        origin=ChangeOrigin.BOT,
    )


def same_instant(stored: str, other: str) -> bool:
    """Whether two ISO 8601 times name one instant, a time with no timezone read as UTC.

    `/signup close-time modify` uses it to tell a replacement from the time already armed: the
    stored string is normalised, but a manager's need not be spelt the same way.
    """
    first, second = parse_datetime(stored), parse_datetime(other)
    return first is not None and first == second


async def close_when_due(
    *,
    change_queue: ChangeQueue,
    module_service: ModuleService,
    signup_module_service: SignupModuleService,
) -> None:
    """The close timer's handler: close the window where its stored close time has come.

    The stored close time, not the timer, says whether the window is due to close, judged against
    the queue's clock: a timer that fires after its time was cancelled (no close time stored) or
    replaced by a later one closes nothing.
    """
    cfg = await signup_module_service.get_config()
    due = None if cfg is None else parse_datetime(cfg.close_at)
    if due is None or due > change_queue.now():
        return
    await close_signups_unattended(
        change_queue=change_queue,
        module_service=module_service,
        signup_module_service=signup_module_service,
        cause="timer",
    )


async def recover_missed_close(bot: "LeagueBot", *, queue: ChangeQueue) -> None:
    """The close timer's catch-up at start: close a window whose close time passed while the bot
    was down, or arm the timer of one still ahead.

    The close time is judged against *queue*'s clock, so a close caught up and one run on time take
    one path. Both are asked of the queue as the bot: the close (``signup.close``), and the timer's
    arming (``signup.close_timer.arm``), which replaces any standing timer, so that an arming a
    Discard dropped (`/signup open`'s) is made good here, as the reply to it says. Nothing is armed
    for a time already gone.
    """
    async with get_connection(bot.db_path) as db:
        if not await signup_enabled_on(db):
            return
        cursor = await db.execute(
            "SELECT signups_open, signup_button_message_id, close_at FROM signup_module_config "
            "WHERE id = 1"
        )
        row = await cursor.fetchone()
    due = None if row is None else parse_datetime(row["close_at"])
    if row is None or not row["signups_open"] or due is None:
        return
    if due <= queue.now():
        await _ask_close(queue, row["signup_button_message_id"], row["close_at"], "restart")
    else:
        await queue.ask(
            ARM_KIND, {"close_at": row["close_at"]},
            what="Arming the signup auto-close", origin=ChangeOrigin.BOT,
        )


def close_timer_arm_change(*, arm_once: Callable[[str, str, datetime], None]) -> ChangeType:
    """The `signup.close_timer.arm` change type, for the builder to register: the arming of the
    close timer for the window's stored close time, as the bot asks it at start
    (`recover_missed_close`). Its check finds it no longer due where signup is off, the window
    closed or the close time not the one asked."""

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT signups_open, close_at FROM signup_module_config WHERE id = 1"
            )
            row = await cursor.fetchone()
            enabled = await signup_enabled_on(db)
        stored = None if row is None else row["close_at"]
        if enabled and row is not None and row["signups_open"] and stored == ctx.payload["close_at"]:
            return Verdict.go()
        return Verdict.refuse("The close time is no longer the one to arm.")

    async def arm(ctx: StepContext) -> StepResult:
        arm_once(CLOSE_TIMER, CLOSE_TIMER_KEY, close_time_of(str(ctx.payload["close_at"])))
        return StepResult()

    return ChangeType(
        kind=ARM_KIND,
        opening=(PlannedStep(ARM_CLOSE_TIMER),),
        steps={ARM_CLOSE_TIMER: Step(ARM_CLOSE_TIMER, StepKind.ACT, arm)},
        check=check,
        key=lambda payload: f"{ARM_KIND}|{payload['close_at']}",
        doing=lambda _payload: "Arming the signup auto-close",
        outcome=lambda _ctx: "",
        writes=(),
    )


def close_time_of(stored: str) -> datetime:
    """The aware UTC moment of a stored close time, for the timer to be armed at."""
    moment = parse_datetime(stored)
    if moment is None:
        raise ValueError(f"not a close time: {stored!r}")
    return moment.astimezone(timezone.utc)
