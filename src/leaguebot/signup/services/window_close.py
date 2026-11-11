"""The close of the signup window nobody ran, and the states a close returns to Not Signed Up.

The queue's own close (``signup.close``) is signup's change. This is who asks for it where no
member does: the close timer and the restart sweep. A season's end and every division done close
the window with signup's own `close_window` job (``window_changes.season_end_close_step``).
"""
from __future__ import annotations

from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.module_service import ModuleService
from leaguebot.signup.services.signup_module_service import SignupModuleService

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
    audit_action = _UNATTENDED_CAUSES[cause]
    if not await module_service.is_signup_enabled():
        return
    cfg = await signup_module_service.get_config()
    if cfg is None or not cfg.signups_open:
        return
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
