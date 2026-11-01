"""The close of the signup window: the drivers it returns to Not Signed Up, and who asks for it.

The queue's own close (``signup.close``) is signup's change. This is the rest of a close: the
return of the drivers still filling in the wizard, which a season's end and every division done
ask for through ``SeasonEndHooks.close_signups`` (core reads what it gives back as
``WindowClosed``), and the close nobody ran, which the close timer and the restart sweep ask the
queue for.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from leaguebot.core.models.change import ChangeOrigin, StepFailedOnDiscord
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.change_queue import ChangeQueue
from leaguebot.core.services.driver_service import DriverService
from leaguebot.core.services.module_service import ModuleService
from leaguebot.signup.services.signup_module_service import SignupModuleService

log = logging.getLogger(__name__)


#: The states a close returns to Not Signed Up: a driver still filling in the wizard. One
#: awaiting approval, awaiting a correction parameter or correcting keeps their state
#: (FR-002/FR-003). ``signup_close`` reads this set to tell a manager which drivers the close
#: will drop and which keep their place. When it kept a list of its own, it warned that every
#: driver mid-signup would be dropped (issue #128).
RETURNED_BY_CLOSE: frozenset[DriverState] = frozenset({DriverState.PENDING_SIGNUP_COMPLETION})


@dataclass(frozen=True)
class ForcedCloseOutcome:
    """What the close of the signup window did: the drivers it turned away.

    It lives in signup, beside the function that returns it. The season's end, which is core's,
    reads it as the ``WindowClosed`` protocol (``season_lifecycle_service``) through
    ``SeasonEndHooks.close_signups``, so core imports nothing from signup for it. Every failure of
    the close is raised and stops the queue, so the outcome names none.
    """

    #: How many drivers were returned to Not Signed Up, read at the moment of closing.
    returned: int
    #: The ids of the drivers returned, in the order the close read them. The change queue's jobs
    #: close each one's signup channel, so the close asked not to hold gives them back.
    returned_ids: tuple[str, ...] = ()


async def execute_forced_close(
    *,
    signup_module_service: SignupModuleService,
    driver_service: DriverService,
    audit_action: str,
    cancel_timers: Callable[[str], None],
) -> ForcedCloseOutcome:
    """Return the drivers still filling in the signup wizard to Not Signed Up, for the change
    queue's close of the signup window, and say what it did.

    This is the queue's form, the only form there is: no close of the window is made off the queue
    any more (`/signup close`, its timer, a restart past the close time and turning signup off
    ask the queue's own close, and a season's end and every division done ask this one). It
    notices and locks no signup channel itself, leaving each driver it returned (the outcome's
    ``returned_ids``) to the queue's ``signup_notice`` and ``close_signup`` jobs, so that a notice
    Discord refuses stops the queue before the channel is locked, and it leaves the season's stage
    where it stands: the Sign Up button's deletion, the "Signups are now closed" post, the
    window's close and its audit are jobs of the change that asked for it (signup's
    ``delete_button`` and ``post_closed``). *cancel_timers* takes away one driver's signup timers
    (the 24 hours, the channel's deletion and the correction's), signup's own, which core cannot
    name.

    Transition drivers in ``RETURNED_BY_CLOSE`` to NOT_SIGNED_UP, each marking their signup
    wizard over in the same save (owed its closing notice), so a restart never tells a driver
    whose signup has ended that it expired. A failed return, other than the state machine's, is
    raised as a `StepFailedOnDiscord` whose result names the drivers already returned
    (``returned``): the stop keeps them on the job, so that its next try, which finds none left
    to return, still tells and closes each one's channel.

    A driver whose transition the state machine refuses (``ValueError``) has moved on since the
    close read them: they are not counted as returned, and it is not a failure. Only the drivers
    returned have their timers cancelled, so a driver whose return failed keeps signing up.

    Whoever calls it hands in the two services it uses (`docs/design/architecture.md`, "Services
    stay attached to the bot, and are built in one place").

    The count is of transitions that succeeded, read at the moment of closing. The
    confirmation ``/signup close`` shows may be up to five minutes older than that.
    """
    cfg = await signup_module_service.get_config()
    if cfg is None:
        return ForcedCloseOutcome(returned=0)

    returned = 0
    returned_ids: list[str] = []
    for user_id in await driver_service.ids_in_states(RETURNED_BY_CLOSE):
        try:
            # The return and the end of the driver's signup are one save: their wizard is marked
            # over, so a restart never expires it, and owed its closing notice, which the queue's
            # jobs then give once. Signup writes its own table (`end_wizard_on`).
            await driver_service.transition(
                user_id,
                DriverState.NOT_SIGNED_UP,
                also_on=partial(
                    signup_module_service.end_wizard_on,
                    discord_user_id=user_id,
                    closing_notice_owed=True,
                ),
            )
            returned += 1
            returned_ids.append(user_id)
            # Their timers go now, before the next driver's save and any await, so that a kill
            # part-way through the loop never leaves a returned driver's 24-hour timer in the
            # persistent jobstore to tell them their session expired as well as that signups
            # closed.
            cancel_timers(user_id)
        except ValueError:
            # The state machine's refusal: the driver moved on since they were read.
            log.info("forced_close: driver %s had moved on", user_id)
        except Exception as exc:
            # As every failure on the queue does, this raises. Each driver returned before this
            # one is saved and marked owed, their timers already cancelled: the stop keeps them on
            # the job for its next try.
            raise StepFailedOnDiscord(
                f"driver {user_id} could not be returned to Not Signed Up",
                result={"returned": list(returned_ids)},
            ) from exc

    return ForcedCloseOutcome(returned=returned, returned_ids=tuple(returned_ids))


#: Why a close nobody ran happened, and the audit action it is recorded under.
_UNATTENDED_CAUSES: dict[str, str] = {
    "timer": "SIGNUP_AUTO_CLOSE",
    "restart": "SIGNUP_AUTO_CLOSE",
    "season end": "SIGNUP_SEASON_END_CLOSE",
    "divisions done": "SIGNUP_DIVISIONS_DONE_CLOSE",
}


async def close_signups_unattended(
    *,
    change_queue: ChangeQueue,
    module_service: ModuleService,
    signup_module_service: SignupModuleService,
    driver_service: DriverService,
    cause: str,
    cancel_timers: Callable[[str], None] | None = None,
) -> ForcedCloseOutcome | None:
    """Close the signup window where no member ran the close.

    *cause* is one of ``"timer"`` (the close time came), ``"restart"`` (the bot came back
    after it), ``"season end"`` and ``"divisions done"``; it picks the audit action the close
    is written under. The close timer and the restart sweep ask the change queue's own close
    (``signup.close``, named here as the builder registers it, signup's) as the bot's and give
    nothing back: the window stays open until the queue reaches it, and the change's jobs tell
    and lock each driver it returns, close the window in its channel and write the one line,
    which names no member, since none closed the window.

    A season's end and every division done close the window with ``execute_forced_close``: the
    outcome gives the drivers it returned and the queue's jobs tell and close each one's signup
    channel, close the window in its channel and record that in the line, which this writes
    none of. *cancel_timers* is handed it, as there, and so a season's end and every division
    done must give it; the close timer's and the restart's close is the queue's own, whose jobs
    cancel the timers themselves, and need not.

    The change queue and the services it uses are handed in by its caller, the entry point's
    close timer, restart sweep and season-end hooks, each of which holds the bot.

    Nothing is done where a window was not actually open: a module that is disabled produces
    nothing (the core specification, Modules), and a window not open needs no close, so both
    return ``None`` having touched nothing.
    """
    audit_action = _UNATTENDED_CAUSES[cause]
    if not await module_service.is_signup_enabled():
        return None
    cfg = await signup_module_service.get_config()
    if cfg is None or not cfg.signups_open:
        return None

    if cause in ("timer", "restart"):
        await change_queue.ask(
            "signup.close",
            {
                "cause": cause,
                "window": cfg.signup_button_message_id,
                "confirmed": False,
                "audit_action": audit_action,
                "close_at": cfg.close_at,
            },
            what="Closing signups",
            origin=ChangeOrigin.BOT,
        )
        return None

    if cancel_timers is None:
        raise TypeError(f"the {cause}'s close returns drivers, so it is handed their timers")
    return await execute_forced_close(
        signup_module_service=signup_module_service,
        driver_service=driver_service,
        audit_action=audit_action,
        cancel_timers=cancel_timers,
    )
