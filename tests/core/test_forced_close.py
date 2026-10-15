"""Force-closing the signup window on the change queue, as a season's end and every division done
do.

Issue #208, #439 slice 6b. `execute_forced_close` was once the shared sub-flow behind
`/module disable signup`, the scheduled close and `/signup close`; those now ask the queue's own
close (`signup.close`, `module.off:signup`, pinned in `tests/signup/test_window_changes.py`), and
what is left here is the form a season's end closes the window with (`hold_channels=False`): it
decides which drivers caught mid-signup are returned, and leaves telling them and closing their
channels to the queue's jobs. Recording the window closed is left to signup's own `post_closed`
job, whose record writes it (#439 slice 6b, C66), so these tests do not read it here.

**Only a driver still filling in the wizard is turned away.** `PENDING_SIGNUP_COMPLETION` is the
one state transitioned back to Not Signed Up. A driver waiting on a manager's approval, or
correcting an answer a manager asked about, has finished signing up as far as they are
concerned, and closing the window must not undo that (FR-002, FR-003).
`test_a_driver_awaiting_approval_is_left_alone` is the one that holds it. The close returns how
many it turned away, and which, counting only transitions that succeeded (issue #128).

**A returned driver's jobs go with them**, in the save that returns them, so that a kill part-way
never leaves a returned driver's inactivity job to tell them their session expired.

**A failure raises for the queue.** A return that fails, other than the state machine's refusal,
raises naming the drivers already returned, so the stop's next try still tells and closes each.
"""
from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.module_cog import execute_forced_close
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.driver_profile import DriverState

SERVER_ID = 13308
SIGNUP_CHANNEL = 700
BUTTON_MESSAGE = 8800
CLOSED_MESSAGE = 9900

#: Until C66/C67, the queue's close audits the window's close and writes its "🔒 Signups closed"
#: line itself; from them on, `post_closed`'s record writes both, with the window's close.
AUDITED_BY_ITS_NOTICE = (
    "#439: the queue's close still audits the window's close and writes its line itself, not in "
    "post_closed's record (C66, C67)"
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name="forced_close", drivers=()):
    """*drivers* are ``(discord_user_id, state)``."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        for uid, state in drivers:
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, ?)",
                (uid, state.value),
            )
        await db.commit()
    return db_path


def _channel(*, fetch_error=None, send_error=None):
    channel = MagicMock()
    button = MagicMock()
    button.delete = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=button, side_effect=fetch_error)
    closed = MagicMock()
    closed.id = CLOSED_MESSAGE
    channel.send = AsyncMock(return_value=closed, side_effect=send_error)
    channel._button = button
    return channel


_UNSET = object()


def _bot(db_path, *, config=_UNSET, channel=None, guild=True, transition_error=None):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.signup_module_service = MagicMock()
    bot.signup_module_service.get_config = AsyncMock(
        return_value=SimpleNamespace(
            signup_channel_id=SIGNUP_CHANNEL, signup_button_message_id=BUTTON_MESSAGE
        )
        if config is _UNSET
        else config
    )
    bot.signup_module_service.set_window_closed = AsyncMock()
    # The close clears, in its audit save, the marks of the drivers it reached.
    bot.signup_module_service.clear_closing_notices_on = AsyncMock()
    bot.driver_service = MagicMock()
    bot.driver_service.transition = AsyncMock(side_effect=transition_error)
    bot.scheduler_service = MagicMock()
    bot.scheduler_service._scheduler = MagicMock()
    bot.wizard_service = MagicMock()
    channel = channel if channel is not None else _channel()
    g = MagicMock()
    g.get_channel = MagicMock(return_value=channel)
    bot.get_guild = MagicMock(return_value=g if guild else None)
    bot._channel = channel
    return bot


def _driver_notices(bot) -> list:
    """Every message the close posted other than the window's "signups are now closed" notice:
    a driver's closing notice. The bot's guild hands this one channel for every channel id, a
    driver's signup channel included, so a notice posted in any of them is counted. The queue's
    form posts none: each driver's notice is a job of its own, signup's closing steps."""
    return [
        call
        for call in bot._channel.send.await_args_list
        if call.args != ("🔒 Signups are now closed.",)
    ]


async def _audit(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, actor_name FROM audit_entries"
        )
        return [tuple(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Drivers mid-signup
# ---------------------------------------------------------------------------


def _ends_recorded(bot) -> list:
    """Each wizard the close marks over, recorded as the signup module service is handed it:
    (the connection, the account, whether the driver is owed their closing notice by the queue)."""
    ended: list = []

    async def _end(db, discord_user_id, *, closing_notice_owed=False, **_rest) -> None:
        ended.append((db, str(discord_user_id), closing_notice_owed))

    bot.signup_module_service.end_wizard_on = AsyncMock(side_effect=_end)
    return ended


async def _handed_on(bot) -> object:
    """Run what the close handed the transition to save with it, on a stand-in connection."""
    db = object()
    await bot.driver_service.transition.await_args.kwargs["also_on"](db)
    return db


@pytest.mark.parametrize(
    "state",
    [
        DriverState.PENDING_ADMIN_APPROVAL,
        DriverState.AWAITING_CORRECTION_PARAMETER,
        DriverState.PENDING_DRIVER_CORRECTION,
        DriverState.UNASSIGNED,
    ],
)
async def test_a_driver_awaiting_approval_is_left_alone(tmp_path, state):
    """FR-002/FR-003: driver 101 has finished signing up as far as they are concerned, and the
    queue's close of the window (a season's end) must not undo that: they are not returned and
    have none of their timers cancelled."""
    db_path = await _make_db(tmp_path, name=f"fc_{state.value}", drivers=[("101", state)])
    bot = _bot(db_path)

    outcome = await execute_forced_close(
        bot, audit_action="SIGNUP_SEASON_END_CLOSE", hold_channels=False
    )

    bot.driver_service.transition.assert_not_awaited()
    bot.scheduler_service.cancel_job.assert_not_called()
    assert list(outcome.returned_ids) == []


async def test_a_job_already_gone_is_stepped_over(tmp_path):
    """Driver 101 is part-way through the wizard, but the scheduler no longer holds their
    inactivity or channel-delete job: the queue's close returns them all the same."""
    from apscheduler.jobstores.base import JobLookupError

    from leaguebot.core.services.scheduler_service import SchedulerService

    # The real scheduler service, over an APScheduler that no longer holds the job.
    scheduler = SchedulerService.__new__(SchedulerService)
    scheduler._scheduler = MagicMock()
    scheduler._scheduler.remove_job = MagicMock(side_effect=JobLookupError("no job"))

    db_path = await _make_db(
        tmp_path, name="fc_nojob", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path)
    bot.scheduler_service = scheduler

    outcome = await execute_forced_close(bot, audit_action="X", hold_channels=False)

    assert list(outcome.returned_ids) == ["101"]


async def test_a_driver_already_moved_on_is_neither_counted_nor_a_failure(tmp_path):
    """Driver 101's transition is refused as the state machine refuses a driver who has moved
    on since the queue's close read them (`ValueError`): they are not counted as returned, none
    of their timers is cancelled, and nothing is reported as failed (#457)."""
    db_path = await _make_db(
        tmp_path, name="fc_movedon", drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    bot = _bot(db_path, transition_error=ValueError("not in PENDING_SIGNUP_COMPLETION"))

    outcome = await execute_forced_close(bot, audit_action="X", hold_channels=False)

    assert outcome.returned == 0
    assert list(outcome.returned_ids) == []
    bot.scheduler_service.cancel_job.assert_not_called()
    assert list(outcome.failed) == []


WIZARD_CHANNEL = 555


async def test_a_close_asked_not_to_hold_gives_the_drivers_it_returned_and_holds_nothing(tmp_path):
    """The season's end closes the window on the change queue, where each driver's notice and
    lock are jobs of their own: asked not to hold, the close returns driver 101 (still filling
    in the wizard) to Not Signed Up, marking their wizard over and owed its closing notice in
    the same save, and gives their id, leaves 102 (awaiting approval) alone, and holds no
    channel itself."""
    db_path = await _make_db(
        tmp_path,
        name="fc_unheld",
        drivers=[
            ("101", DriverState.PENDING_SIGNUP_COMPLETION),
            ("102", DriverState.PENDING_ADMIN_APPROVAL),
        ],
    )
    bot = _bot(db_path)
    ended = _ends_recorded(bot)

    outcome = await execute_forced_close(bot, audit_action="X", hold_channels=False)

    bot.driver_service.transition.assert_awaited_once()
    assert bot.driver_service.transition.await_args.args == ("101", DriverState.NOT_SIGNED_UP)
    db = await _handed_on(bot)
    assert ended == [(db, "101", True)]
    assert _driver_notices(bot) == []
    assert outcome.returned == 1
    assert [str(uid) for uid in outcome.returned_ids] == ["101"]
    assert list(outcome.failed) == []


async def _real_services(tmp_path, *, name, hours_since_activity=1.0):
    """Driver 101 part-way through the wizard (Pending Signup Completion, the wizard collecting
    their notes in channel 555), their last answer *hours_since_activity* hours ago, and the
    window open on button 8800 in channel 700; the bot's signup module and driver services the
    real ones on the migrated database. `recover_wizards` reads the host's clock, so the time is
    computed from it."""
    from datetime import timedelta, timezone

    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    db_path = await _make_db(
        tmp_path, name=name, drivers=[("101", DriverState.PENDING_SIGNUP_COMPLETION)]
    )
    last = datetime.now(timezone.utc) - timedelta(hours=hours_since_activity)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_module_config (id, signup_channel_id, signups_open, "
            "signup_button_message_id) VALUES (1, ?, 1, ?)",
            (SIGNUP_CHANNEL, BUTTON_MESSAGE),
        )
        await db.execute(
            "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, "
            "signup_channel_id, last_activity_at) VALUES ('101', 'COLLECTING_NOTES', ?, ?)",
            (WIZARD_CHANNEL, last.isoformat()),
        )
        await db.commit()
    bot = _bot(db_path)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.driver_service = DriverService(db_path)
    return bot


async def _wizard_of(db_path, user_id: str) -> tuple[str, int]:
    """The wizard's state and its signup channel."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT wizard_state, signup_channel_id FROM signup_wizard_records "
            "WHERE discord_user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    assert row is not None, user_id
    return row[0], row[1]


async def _owed_of(db_path, user_id: str) -> int:
    """Whether the wizard's driver is owed their closing notice."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT closing_notice_owed FROM signup_wizard_records WHERE discord_user_id = ?",
            (user_id,),
        )
        return (await cursor.fetchone())[0]


async def _state_of(db_path, user_id: str) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", (user_id,)
        )
        return (await cursor.fetchone())[0]


async def _window_still_open(db_path) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT signups_open FROM signup_module_config WHERE id = 1")
        return bool((await cursor.fetchone())[0])


@pytest.mark.parametrize("case", ["on the queue", "the mark fails"])
async def test_the_queue_s_close_marks_each_driver_it_returns_owed_in_the_same_save(tmp_path, case):
    """Driver 101, part-way through the wizard, is returned to Not Signed Up by the window's
    close, on the real services.

    - On the queue (`hold_channels=False`): 101 is Not Signed Up, their wizard unengaged and
      owed its closing notice, together, its channel kept for the queue's jobs.
    - The mark cannot be written (the wizard's record refuses the update): 101's return is
      rolled back with it, still Pending Signup Completion with the wizard as it was, and the
      queue's close raises the failure, stopping before the window is recorded closed."""
    bot = await _real_services(tmp_path, name="fc_mark")
    if case == "the mark fails":
        async with get_connection(bot.db_path) as db:
            await db.execute(
                "CREATE TRIGGER mark_fails BEFORE UPDATE ON signup_wizard_records "
                "BEGIN SELECT RAISE(ABORT, 'the mark could not be written'); END"
            )
            await db.commit()

    if case == "the mark fails":
        with pytest.raises(Exception) as raised:
            await execute_forced_close(bot, audit_action="X", hold_channels=False)
        chain, error = [], raised.value
        while error is not None:
            chain.append(str(error))
            error = error.__cause__ or error.__context__
        assert any("the mark could not be written" in text for text in chain), chain
        assert await _state_of(bot.db_path, "101") == "PENDING_SIGNUP_COMPLETION"
        assert await _wizard_of(bot.db_path, "101") == ("COLLECTING_NOTES", WIZARD_CHANNEL)
        assert await _owed_of(bot.db_path, "101") == 0
        assert await _window_still_open(bot.db_path)
        return

    await execute_forced_close(bot, audit_action="X", hold_channels=False)

    assert await _state_of(bot.db_path, "101") == "NOT_SIGNED_UP"
    assert await _wizard_of(bot.db_path, "101") == ("UNENGAGED", WIZARD_CHANNEL)
    assert await _owed_of(bot.db_path, "101") == 1


class _Killed(BaseException):
    """The bot's process killed: no handler of the bot's sees it, nothing more is saved."""


async def test_a_kill_part_way_through_the_queue_s_close_leaves_no_returned_driver_s_timer_armed(
    tmp_path,
):
    """Drivers 101 and 102 are both part-way through the wizard, each with an inactivity job
    standing in the scheduler, when the change queue's close returns them, on the real driver
    and signup module services. The process is killed in 102's return, after 101's return has
    been saved. At that moment 101 is Not Signed Up and owed their closing notice, and their
    inactivity and channel-delete jobs are already cancelled: none is left to tell them their
    session expired once the bot starts again. 102 is untouched: still part-way through, owed
    nothing, their inactivity job standing."""
    from datetime import timedelta, timezone

    from leaguebot.signup.services.wizard_service import channel_delete_job_id, inactivity_job_id

    bot = await _real_services(tmp_path, name="fc_killed")
    last = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    async with get_connection(bot.db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES ('102', 'PENDING_SIGNUP_COMPLETION')"
        )
        await db.execute(
            "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, "
            "signup_channel_id, last_activity_at) VALUES ('102', 'COLLECTING_NOTES', 556, ?)",
            (last,),
        )
        await db.commit()

    standing = {inactivity_job_id("101"), inactivity_job_id("102")}
    cancelled: list[str] = []

    def _cancel(job_id: str) -> None:
        cancelled.append(job_id)
        standing.discard(job_id)

    bot.scheduler_service.cancel_job = MagicMock(side_effect=_cancel)
    returning = bot.driver_service.transition
    asked: list[str] = []

    async def _transition(user_id, *args, **kwargs):
        asked.append(str(user_id))
        if str(user_id) == "102":
            raise _Killed()
        return await returning(user_id, *args, **kwargs)

    bot.driver_service.transition = _transition

    with pytest.raises(_Killed):
        await execute_forced_close(bot, audit_action="X", hold_channels=False)

    # What stands at the moment of the kill, before anything after the loop could run.
    assert asked == ["101", "102"]
    assert await _state_of(bot.db_path, "101") == "NOT_SIGNED_UP"
    assert await _owed_of(bot.db_path, "101") == 1
    assert inactivity_job_id("101") in cancelled
    assert channel_delete_job_id("101") in cancelled
    assert inactivity_job_id("101") not in standing
    assert await _state_of(bot.db_path, "102") == "PENDING_SIGNUP_COMPLETION"
    assert await _owed_of(bot.db_path, "102") == 0
    assert not any(job_id.endswith("_102") for job_id in cancelled)
    assert standing == {inactivity_job_id("102")}


async def test_a_driver_whose_return_fails_keeps_their_timers_and_channel_and_is_named(tmp_path):
    """A season's end closes the window on the change queue and finds drivers 101 and 102
    part-way through the wizard. The first driver's return goes through; the second's fails with
    an error other than the state machine's refusal. The close raises for the queue, naming the
    first driver as already returned, so that the stop's next try tells and closes them. The
    second is still signing up: none of their timers is cancelled. Nothing is closed."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _make_db(
        tmp_path,
        name="fc_return_fails",
        drivers=[
            ("101", DriverState.PENDING_SIGNUP_COMPLETION),
            ("102", DriverState.PENDING_SIGNUP_COMPLETION),
        ],
    )
    bot = _bot(db_path)
    asked: list[str] = []

    async def _transition(user_id, *_a, **_k):
        asked.append(str(user_id))
        if len(asked) == 2:
            raise RuntimeError("db locked")

    bot.driver_service.transition = AsyncMock(side_effect=_transition)

    with pytest.raises(StepFailedOnDiscord) as raised:
        await execute_forced_close(bot, audit_action="SIGNUP_SEASON_END_CLOSE", hold_channels=False)

    first, second = asked
    assert sorted(asked) == ["101", "102"]
    assert raised.value.result == {"returned": [first]}
    cancelled = {c.args[0] for c in bot.scheduler_service.cancel_job.call_args_list}
    assert cancelled == {f"wizard_inactivity_{first}", f"wizard_channel_delete_{first}"}
    assert _driver_notices(bot) == []
    bot.signup_module_service.set_window_closed.assert_not_awaited()


async def test_the_close_returns_how_many_drivers_it_turned_away(tmp_path):
    """The queue's close (a season's end) gives how many drivers it returned and which (issue
    #128): drivers 101 and 102, part-way through the wizard, are counted; 103, awaiting
    approval, is not turned away, so is not counted."""
    db_path = await _make_db(
        tmp_path,
        name="fc_count",
        drivers=[
            ("101", DriverState.PENDING_SIGNUP_COMPLETION),
            ("102", DriverState.PENDING_SIGNUP_COMPLETION),
            ("103", DriverState.PENDING_ADMIN_APPROVAL),
        ],
    )

    outcome = await execute_forced_close(_bot(db_path), audit_action="X", hold_channels=False)

    assert outcome.returned == 2
    assert sorted(outcome.returned_ids) == ["101", "102"]
    assert list(outcome.failed) == []


# ---------------------------------------------------------------------------
# The window itself
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=AUDITED_BY_ITS_NOTICE)
async def test_the_queue_s_close_writes_no_audit_of_the_window(tmp_path):
    """The queue's close serves a season's end and every division done. It writes no audit of
    the window's close: that record is written by the "Signups are now closed" post's own job,
    with the window's close, so a discarded post leaves no record that signups closed."""
    db_path = await _make_db(tmp_path, name="fc_audit")

    await execute_forced_close(
        _bot(db_path), audit_action="SIGNUP_DIVISIONS_DONE_CLOSE", hold_channels=False
    )

    assert await _audit(db_path) == []


async def test_a_server_with_no_signup_configuration_does_nothing(tmp_path):
    """No signup configuration exists: the close returns an outcome with nobody returned and
    nothing failed, closes nothing and audits nothing."""
    db_path = await _make_db(tmp_path, name="fc_noconfig")
    bot = _bot(db_path, config=None)

    outcome = await execute_forced_close(bot, audit_action="X", hold_channels=False)

    assert outcome.returned == 0
    assert list(outcome.failed) == []

    bot.signup_module_service.set_window_closed.assert_not_awaited()
    assert await _audit(db_path) == []


def _window(*, signups_open=True, button=BUTTON_MESSAGE, close_at=None):
    """The signup configuration as the close re-reads it."""
    return SimpleNamespace(
        signup_channel_id=SIGNUP_CHANNEL,
        signup_button_message_id=button,
        signups_open=signups_open,
        close_at=close_at,
    )


# ---------------------------------------------------------------------------
# A close nobody ran: the timer, the restart sweep, a season's end, every division done
# ---------------------------------------------------------------------------

#: The set time a timer closed at, already past.
SET_TIME = "2026-09-30T18:00:00+00:00"


def _unattended_bot(db_path, *, signups_open=True, enabled=True, channel=None):
    """A bot whose signup module is *enabled* or not, and whose window is open or not, with
    its close time set at `SET_TIME`, and a log channel that keeps what is posted to it."""
    bot = _bot(db_path, config=_window(signups_open=signups_open, close_at=SET_TIME), channel=channel)
    bot.module_service.is_signup_enabled = AsyncMock(return_value=enabled)
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


# (the cause the caller gives; what the line's head says; the audit action it is closed under).
# The timer and the restart sweep ask the change queue's own close (`signup.close`), whose line
# that close writes; a season's end and every division done close on the queue through this one.
_CAUSES = [
    pytest.param(
        "season end", ("🔒 Signups closed as the season ended",), "SIGNUP_SEASON_END_CLOSE",
        id="season end",
    ),
    pytest.param(
        "divisions done",
        ("🔒 Signups closed as every division is done",),
        "SIGNUP_DIVISIONS_DONE_CLOSE",
        id="divisions done",
    ),
]


@pytest.mark.xfail(strict=True, reason=AUDITED_BY_ITS_NOTICE)
@pytest.mark.parametrize("cause, heads, audit_action", _CAUSES)
async def test_a_close_nobody_ran_on_the_queue_leaves_its_audit_and_line_to_the_closed_notice(
    tmp_path, cause, heads, audit_action
):
    """Signups are open with drivers 101 and 102 still filling in the wizard, and the window
    closes on the change queue with nobody at the keyboard: the season ended, or every division
    finished. The close returns both drivers, but writes no audit and no "🔒 Signups closed"
    line: the "Signups are now closed" post's own job writes both, with the window's close, so
    a discarded post leaves no record that signups closed."""
    from leaguebot.core.cogs.module_cog import close_signups_unattended

    db_path = await _make_db(
        tmp_path,
        drivers=[
            ("101", DriverState.PENDING_SIGNUP_COMPLETION),
            ("102", DriverState.PENDING_SIGNUP_COMPLETION),
        ],
    )
    bot = _unattended_bot(db_path)

    outcome = await close_signups_unattended(bot, cause=cause, hold_channels=False)

    assert outcome is not None
    assert sorted(outcome.returned_ids) == ["101", "102"]
    assert await _audit(db_path) == []
    bot.output_router.post_log.assert_not_awaited()


@pytest.mark.parametrize("cause", ["timer", "restart", "season end", "divisions done"])
@pytest.mark.parametrize(
    "signups_open, enabled",
    [pytest.param(False, True, id="no window open"), pytest.param(True, False, id="module off")],
)
async def test_a_close_nobody_ran_that_closes_nothing_writes_no_line(
    tmp_path, cause, signups_open, enabled
):
    """A close nobody ran finds no window open, or the signup module disabled: it writes no
    line, since only a window actually closed is recorded and a disabled module produces
    nothing."""
    from leaguebot.core.cogs.module_cog import close_signups_unattended

    db_path = await _make_db(tmp_path)
    bot = _unattended_bot(db_path, signups_open=signups_open, enabled=enabled)

    await close_signups_unattended(bot, cause=cause)

    bot.output_router.post_log.assert_not_awaited()


def _functions_in_main():
    """Every function `__main__` defines, the closures inside `on_ready` included."""
    import ast
    import inspect

    import leaguebot.__main__ as bot_main

    tree = ast.parse(inspect.getsource(bot_main))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _calls_to(node, name: str) -> list:
    """The calls under *node* to a function or method called *name*."""
    import ast

    return [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and name in (getattr(call.func, "id", None), getattr(call.func, "attr", None))
    ]


def _cause_of(call):
    import ast

    for keyword in call.keywords:
        if keyword.arg == "cause" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _close_timer_callback():
    """The function registered as the signup close timer's callback."""
    functions = _functions_in_main()
    register = next(
        call for function in functions
        for call in _calls_to(function, "register_signup_close_callback")
    )
    name = register.args[0].id
    return next(function for function in functions if function.name == name)


def _restart_sweep():
    """The innermost function that re-arms the signup close timer at start-up: the sweep
    whose other branch closes a window whose set time passed while the bot was down."""
    return min(
        (f for f in _functions_in_main() if _calls_to(f, "schedule_signup_close_timer")),
        key=lambda f: f.end_lineno - f.lineno,
    )


@pytest.mark.parametrize(
    "find, cause",
    [
        pytest.param(_close_timer_callback, "timer", id="close timer"),
        pytest.param(_restart_sweep, "restart", id="restart sweep"),
    ],
)
def test_the_close_timer_and_the_restart_sweep_call_the_close_nobody_ran(find, cause):
    """Signups close at their set time, or the bot restarts after it: the job that runs then
    closes them through `close_signups_unattended` with its own cause, so its one line naming
    no member is written, and never through `execute_forced_close`, which writes none. Both
    are closures inside `on_ready`, which no test drives whole, so the wiring is read from the
    source, as `test_hub_hooks.py` reads the hub's."""
    function = find()

    closes = _calls_to(function, "close_signups_unattended")
    assert closes, f"{function.name} does not call close_signups_unattended"
    assert [_cause_of(call) for call in closes] == [cause] * len(closes)
    assert _calls_to(function, "execute_forced_close") == []
